import csv
import io
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from uuid import uuid4

from app.core.database import get_db, get_supabase_admin_client
from app.core.security import privacy
from app.core.response import success_response, error_response
from app.models.identity import UserIdentity, AuditLog
from app.models.tenant import Tenant, TenantMember
from app.models.notification import Notification
from app.api.deps.auth import get_current_user_identity

logger = logging.getLogger("sentinel.users")
router = APIRouter()


@router.get("")
@router.get("/")
async def list_users(
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
    role: str = None,
    search: str = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
):
    """List all users with optional filtering."""
    query = db.query(UserIdentity)

    # Role-based filtering
    if user.role == "employee":
        query = query.filter(UserIdentity.user_hash == user.user_hash)
    elif user.role == "manager":
        team_hashes = set(
            m.user_hash
            for m in db.query(UserIdentity.user_hash)
            .filter(UserIdentity.manager_hash == user.user_hash)
            .all()
        )
        team_hashes.add(user.user_hash)
        query = query.filter(UserIdentity.user_hash.in_(team_hashes))
    # admin: no filter, sees all

    if role:
        query = query.filter(UserIdentity.role == role)

    if search:
        # Search by user_hash (email can't be searched since it's encrypted)
        query = query.filter(UserIdentity.user_hash.contains(search.lower()))

    total = query.count()
    users = query.offset(offset).limit(limit).all()

    return success_response(
        {
            "users": [
                {
                    "user_hash": u.user_hash,
                    "role": u.role,
                    "consent_share_with_manager": u.consent_share_with_manager,
                    "consent_share_anonymized": u.consent_share_anonymized,
                    "monitoring_paused": u.monitoring_paused_until is not None
                    if u.monitoring_paused_until
                    else False,
                    "created_at": u.created_at.isoformat() if u.created_at else None,
                }
                for u in users
            ],
            "total": total,
        }
    )


@router.get("/{user_hash}")
async def get_user(
    user_hash: str,
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """Get a specific user's profile."""
    # Permission check
    if user.role == "employee":
        if user.user_hash != user_hash:
            raise HTTPException(status_code=403, detail="Employees can only view their own profile")
    elif user.role == "manager":
        target_check = db.query(UserIdentity).filter_by(user_hash=user_hash).first()
        if target_check and user.user_hash != user_hash and target_check.manager_hash != user.user_hash:
            raise HTTPException(status_code=403, detail="Managers can only view their own profile and direct reports")
    # admin: can view anyone

    target = db.query(UserIdentity).filter_by(user_hash=user_hash).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Get tenant memberships
    memberships = (
        db.query(TenantMember, Tenant.name)
        .join(Tenant, Tenant.id == TenantMember.tenant_id)
        .filter(TenantMember.user_hash == user_hash)
        .all()
    )

    return success_response(
        {
            "user_hash": target.user_hash,
            "role": target.role,
            "consent_share_with_manager": target.consent_share_with_manager,
            "consent_share_anonymized": target.consent_share_anonymized,
            "monitoring_paused": target.monitoring_paused_until is not None,
            "manager_hash": target.manager_hash,
            "created_at": target.created_at.isoformat() if target.created_at else None,
            "tenants": [
                {
                    "id": str(m.TenantMember.tenant_id),
                    "name": name,
                    "role": m.TenantMember.role,
                }
                for m, name in memberships
            ],
        }
    )


@router.put("/{user_hash}/role")
async def update_user_role(
    user_hash: str,
    body: dict,
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """Update a user's role (admin only)."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can change roles")

    valid_roles = ["employee", "manager", "admin"]
    new_role = body.get("role")
    if new_role not in valid_roles:
        raise HTTPException(
            status_code=400, detail=f"Invalid role. Must be one of: {valid_roles}"
        )

    target = db.query(UserIdentity).filter_by(user_hash=user_hash).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    old_role = target.role
    target.role = new_role

    # Audit log
    db.add(
        AuditLog(
            user_hash=user_hash,
            action="user:role_changed",
            details={
                "old_role": old_role,
                "new_role": new_role,
                "changed_by": user.user_hash,
            },
        )
    )

    # Create notification for the user
    db.add(
        Notification(
            user_hash=user_hash,
            type="team",
            title="Role Updated",
            message=f"Your role has been changed to {new_role}.",
            priority="high",
            data={"old_role": old_role, "new_role": new_role},
        )
    )

    db.commit()
    return success_response({"message": f"Role updated to {new_role}"})


@router.put("/{user_hash}/manager")
async def assign_manager(
    user_hash: str,
    body: dict,
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """Assign a manager to a user."""
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only admins can assign managers",
        )
    manager_hash = body.get("manager_hash")

    target = db.query(UserIdentity).filter_by(user_hash=user_hash).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if manager_hash:
        manager = db.query(UserIdentity).filter_by(user_hash=manager_hash).first()
        if not manager:
            raise HTTPException(status_code=404, detail="Manager not found")

    target.manager_hash = manager_hash
    db.commit()
    return success_response({"message": "Manager assigned"})


@router.delete("/{user_hash}")
async def deactivate_user(
    user_hash: str,
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """Deactivate a user (admin only). Does not delete data."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can deactivate users")

    if user_hash == user.user_hash:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")

    target = db.query(UserIdentity).filter_by(user_hash=user_hash).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Audit log
    db.add(
        AuditLog(
            user_hash=user_hash,
            action="user:deactivated",
            details={"deactivated_by": user.user_hash},
        )
    )

    db.commit()
    return success_response({"message": "User deactivated"})


@router.get("/export/csv")
async def export_users_csv(
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """Export all users as a CSV file."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can export users")

    users = db.query(UserIdentity).order_by(UserIdentity.created_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(
        [
            "user_hash",
            "email",
            "role",
            "manager_hash",
            "consent_share_with_manager",
            "consent_share_anonymized",
            "created_at",
        ]
    )

    for u in users:
        try:
            email = privacy.decrypt(u.email_encrypted)
        except Exception:
            email = "[encrypted]"

        writer.writerow(
            [
                u.user_hash,
                email,
                u.role,
                u.manager_hash or "",
                u.consent_share_with_manager,
                u.consent_share_anonymized,
                u.created_at.isoformat() if u.created_at else "",
            ]
        )

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=sentinel_users_export.csv"
        },
    )


@router.get("/export/template")
async def export_template_csv(
    user: UserIdentity = Depends(get_current_user_identity),
):
    """Download a CSV template for user import."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["email", "role"])
    writer.writerow(["newuser@example.com", "employee"])
    writer.writerow(["manager@example.com", "manager"])
    writer.writerow(["admin@example.com", "admin"])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=sentinel_user_import_template.csv"
        },
    )


@router.post("/import/csv")
async def import_users_csv(
    file: UploadFile = File(...),
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """Import users from a CSV file."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can import users")

    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")

    content = await file.read()
    text = content.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))

    required_fields = {"email", "role"}
    if not required_fields.issubset(set(reader.fieldnames or [])):
        raise HTTPException(
            status_code=400,
            detail=f"CSV must contain columns: {', '.join(required_fields)}",
        )

    valid_roles = {"employee", "manager", "admin"}
    imported = 0
    skipped = 0
    errors = []

    for row_num, row in enumerate(reader, start=2):
        email = row.get("email", "").strip()
        role = row.get("role", "employee").strip().lower()

        if not email:
            errors.append(f"Row {row_num}: Email is required")
            continue

        if role not in valid_roles:
            errors.append(
                f"Row {row_num}: Invalid role '{role}'. Must be one of: {valid_roles}"
            )
            continue

        try:
            user_hash = privacy.hash_identity(email)
            existing = db.query(UserIdentity).filter_by(user_hash=user_hash).first()

            if existing:
                if existing.role != role:
                    old_role = existing.role
                    existing.role = role
                    db.add(
                        AuditLog(
                            user_hash=user_hash,
                            action="user:role_imported",
                            details={
                                "old_role": old_role,
                                "new_role": role,
                                "imported_by": user.user_hash,
                            },
                        )
                    )
                skipped += 1
                continue

            new_user = UserIdentity(
                user_hash=user_hash,
                email_encrypted=privacy.encrypt(email),
                role=role,
                consent_share_with_manager=True,
                consent_share_anonymized=True,
            )
            db.add(new_user)

            db.add(
                AuditLog(
                    user_hash=user_hash,
                    action="user:imported",
                    details={"imported_by": user.user_hash, "role": role},
                )
            )
            imported += 1

        except Exception as e:
            errors.append(f"Row {row_num}: {str(e)}")

    db.commit()

    return success_response(
        {
            "imported": imported,
            "skipped": skipped,
            "errors": errors,
            "total_rows": imported + skipped + len(errors),
        }
    )
