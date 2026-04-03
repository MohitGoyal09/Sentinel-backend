import csv
import io
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from uuid import uuid4

from app.core.database import get_db, get_supabase_admin_client
from app.core.security import privacy
from app.core.response import success_response, error_response
from app.models.identity import UserIdentity, AuditLog
from app.models.tenant import Tenant, TenantMember
from app.models.team import Team
from app.models.notification import Notification
from app.api.deps.auth import get_current_user_identity, get_tenant_member
from app.services.audit_service import AuditService, AuditAction

logger = logging.getLogger("sentinel.users")
router = APIRouter()


@router.get("")
@router.get("/")
def list_users(
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
    role: str = None,
    search: str = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
):
    """List all users with optional filtering."""
    query = db.query(UserIdentity)

    # Role-based filtering
    if member.role == "employee":
        query = query.filter(UserIdentity.user_hash == member.user_hash)
    elif member.role == "manager":
        team_member_hashes = [
            tm.user_hash
            for tm in db.query(TenantMember.user_hash).filter_by(
                team_id=member.team_id, tenant_id=member.tenant_id
            ).all()
        ]
        if member.user_hash not in team_member_hashes:
            team_member_hashes.append(member.user_hash)
        query = query.filter(UserIdentity.user_hash.in_(team_member_hashes))
    else:
        # admin: tenant-scoped (only see users in their tenant)
        tenant_member_hashes = [
            tm.user_hash
            for tm in db.query(TenantMember.user_hash).filter_by(
                tenant_id=member.tenant_id
            ).all()
        ]
        query = query.filter(UserIdentity.user_hash.in_(tenant_member_hashes))

    if role:
        # Filter by TenantMember role within this tenant
        role_hashes = [
            tm.user_hash
            for tm in db.query(TenantMember.user_hash).filter_by(
                tenant_id=member.tenant_id, role=role
            ).all()
        ]
        query = query.filter(UserIdentity.user_hash.in_(role_hashes))

    if search:
        # Search by user_hash (email can't be searched since it's encrypted)
        query = query.filter(UserIdentity.user_hash.contains(search.lower()))

    total = query.count()
    users = query.offset(offset).limit(limit).all()

    # Fetch TenantMember records for role/team_id enrichment
    user_hashes = [u.user_hash for u in users]
    tenant_members = {
        tm.user_hash: tm
        for tm in db.query(TenantMember).filter(
            TenantMember.user_hash.in_(user_hashes),
            TenantMember.tenant_id == member.tenant_id,
        ).all()
    }

    return success_response(
        {
            "users": [
                {
                    "user_hash": u.user_hash,
                    "role": tenant_members[u.user_hash].role
                    if u.user_hash in tenant_members
                    else "employee",
                    "team_id": str(tenant_members[u.user_hash].team_id)
                    if u.user_hash in tenant_members and tenant_members[u.user_hash].team_id
                    else None,
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
def get_user(
    user_hash: str,
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
):
    """Get a specific user's profile."""
    # Permission check
    if member.role == "employee":
        if member.user_hash != user_hash:
            raise HTTPException(status_code=403, detail="Employees can only view their own profile")
    elif member.role == "manager":
        target_member = db.query(TenantMember).filter_by(
            user_hash=user_hash, tenant_id=member.tenant_id
        ).first()
        if (
            target_member
            and member.user_hash != user_hash
            and target_member.team_id != member.team_id
        ):
            raise HTTPException(
                status_code=403,
                detail="Managers can only view their own profile and direct reports",
            )
    # admin: can view anyone

    target = db.query(UserIdentity).filter_by(user_hash=user_hash).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    target_member = db.query(TenantMember).filter_by(
        user_hash=user_hash, tenant_id=member.tenant_id
    ).first()

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
            "role": target_member.role if target_member else "employee",
            "team_id": str(target_member.team_id)
            if target_member and target_member.team_id
            else None,
            "consent_share_with_manager": target.consent_share_with_manager,
            "consent_share_anonymized": target.consent_share_anonymized,
            "monitoring_paused": target.monitoring_paused_until is not None,
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
def update_user_role(
    user_hash: str,
    body: dict,
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
):
    """Update a user's role (admin only)."""
    if member.role != "admin":
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

    target_member = db.query(TenantMember).filter_by(
        user_hash=user_hash, tenant_id=member.tenant_id
    ).first()
    if not target_member:
        raise HTTPException(status_code=404, detail="User is not a member of this tenant")

    old_role = target_member.role
    target_member.role = new_role

    # Audit log
    audit = AuditService(db)
    audit.log(
        actor_hash=member.user_hash,
        actor_role=member.role,
        action=AuditAction.ROLE_CHANGED,
        target_hash=user_hash,
        details={"old_role": old_role, "new_role": new_role},
        tenant_id=member.tenant_id,
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
def assign_manager(
    user_hash: str,
    body: dict,
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
):
    """Assign a user to a team (admin only)."""
    if member.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only admins can assign managers",
        )

    team_id = body.get("team_id")

    target = db.query(UserIdentity).filter_by(user_hash=user_hash).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    target_member = db.query(TenantMember).filter_by(
        user_hash=user_hash, tenant_id=member.tenant_id
    ).first()
    if not target_member:
        raise HTTPException(status_code=404, detail="User is not a member of this tenant")

    if team_id:
        team = db.query(Team).filter_by(id=team_id, tenant_id=member.tenant_id).first()
        if not team:
            raise HTTPException(
                status_code=404, detail="Team not found in this tenant"
            )
        target_member.team_id = team_id
    else:
        target_member.team_id = None

    db.commit()
    return success_response({"message": "Team assignment updated"})


@router.delete("/{user_hash}")
def deactivate_user(
    user_hash: str,
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
):
    """Deactivate a user (admin only). Does not delete data."""
    if member.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can deactivate users")

    if user_hash == member.user_hash:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")

    # Verify target belongs to the same tenant before deactivating
    target_member = db.query(TenantMember).filter_by(
        user_hash=user_hash, tenant_id=member.tenant_id
    ).first()
    if not target_member:
        raise HTTPException(status_code=404, detail="User not found")

    target = db.query(UserIdentity).filter_by(user_hash=user_hash).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    target.is_active = False
    target.deactivated_at = datetime.utcnow()

    # Audit log
    audit = AuditService(db)
    audit.log(
        actor_hash=member.user_hash,
        actor_role=member.role,
        action=AuditAction.USER_DEACTIVATED,
        target_hash=user_hash,
        details={"deactivated_by": member.user_hash},
        tenant_id=member.tenant_id,
    )

    db.commit()
    return success_response({"message": "User deactivated"})


@router.get("/export/csv")
def export_users_csv(
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
):
    """Export all users as a CSV file."""
    if member.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can export users")

    tenant_hashes = [
        tm.user_hash
        for tm in db.query(TenantMember.user_hash).filter_by(
            tenant_id=member.tenant_id
        ).all()
    ]
    users = db.query(UserIdentity).filter(
        UserIdentity.user_hash.in_(tenant_hashes)
    ).order_by(UserIdentity.created_at.desc()).all()

    # Build a map of user_hash -> TenantMember for this tenant
    user_hashes = [u.user_hash for u in users]
    tenant_members = {
        tm.user_hash: tm
        for tm in db.query(TenantMember).filter(
            TenantMember.user_hash.in_(user_hashes),
            TenantMember.tenant_id == member.tenant_id,
        ).all()
    }

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(
        [
            "user_hash",
            "email",
            "role",
            "team_id",
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

        tm = tenant_members.get(u.user_hash)
        writer.writerow(
            [
                u.user_hash,
                email,
                tm.role if tm else "employee",
                str(tm.team_id) if tm and tm.team_id else "",
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
def export_template_csv(
    member: TenantMember = Depends(get_tenant_member),
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
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
):
    """Import users from a CSV file."""
    if member.role != "admin":
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
                # Update TenantMember role if it differs
                existing_member = db.query(TenantMember).filter_by(
                    user_hash=user_hash, tenant_id=member.tenant_id
                ).first()
                if existing_member and existing_member.role != role:
                    old_role = existing_member.role
                    existing_member.role = role
                    db.add(
                        AuditLog(
                            user_hash=user_hash,
                            action="user:role_imported",
                            details={
                                "old_role": old_role,
                                "new_role": role,
                                "imported_by": member.user_hash,
                            },
                        )
                    )
                skipped += 1
                continue

            new_user = UserIdentity(
                user_hash=user_hash,
                email_encrypted=privacy.encrypt(email),
                consent_share_with_manager=False,
                consent_share_anonymized=False,
            )
            db.add(new_user)

            new_member = TenantMember(
                tenant_id=member.tenant_id,
                user_hash=user_hash,
                role=role,
                invited_by=member.user_hash,
            )
            db.add(new_member)

            db.add(
                AuditLog(
                    user_hash=user_hash,
                    action="user:imported",
                    details={"imported_by": member.user_hash, "role": role},
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
