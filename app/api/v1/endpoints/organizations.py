import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from uuid import uuid4
from datetime import datetime

from app.core.database import get_db
from app.core.response import success_response, error_response
from app.models.identity import UserIdentity, AuditLog
from app.models.tenant import Tenant, TenantMember
from app.api.deps.auth import get_current_user_identity, require_role

logger = logging.getLogger("sentinel.orgs")
router = APIRouter()


@router.get("/current")
async def get_current_organization(
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """Get the user's current organization details."""
    membership = (
        db.query(TenantMember, Tenant)
        .join(Tenant, Tenant.id == TenantMember.tenant_id)
        .filter(TenantMember.user_hash == user.user_hash)
        .first()
    )

    if not membership:
        return error_response("no_org", "You are not a member of any organization")

    member, tenant = membership
    member_count = db.query(TenantMember).filter_by(tenant_id=tenant.id).count()

    return success_response(
        {
            "id": str(tenant.id),
            "name": tenant.name,
            "slug": tenant.slug,
            "plan": tenant.plan,
            "status": tenant.status,
            "settings": tenant.settings or {},
            "your_role": member.role,
            "member_count": member_count,
            "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
        }
    )


@router.put("/current/settings")
async def update_org_settings(
    body: dict,
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """Update organization settings (admin/owner only)."""
    membership = db.query(TenantMember).filter_by(user_hash=user.user_hash).first()

    if not membership or membership.role not in ("owner", "admin"):
        raise HTTPException(
            status_code=403, detail="Only owners and admins can update settings"
        )

    tenant = db.query(Tenant).filter_by(id=membership.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Update allowed settings
    allowed_keys = [
        "timezone",
        "language",
        "date_format",
        "password_policy",
        "session_timeout",
        "mfa_required",
    ]
    current_settings = tenant.settings or {}
    for key in allowed_keys:
        if key in body:
            current_settings[key] = body[key]

    tenant.settings = current_settings
    tenant.updated_at = datetime.utcnow()
    db.commit()

    return success_response(
        {"message": "Settings updated", "settings": current_settings}
    )


@router.get("/stats")
async def get_org_stats(
    member: TenantMember = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Get organization statistics for the dashboard."""
    tenant_id = member.tenant_id

    # Count stats scoped to tenant via TenantMember
    tenant_members_query = db.query(TenantMember).filter_by(tenant_id=tenant_id)
    total_users = tenant_members_query.count()
    admin_count = tenant_members_query.filter_by(role="admin").count()
    manager_count = (
        db.query(TenantMember)
        .filter_by(tenant_id=tenant_id, role="manager")
        .count()
    )

    return success_response(
        {
            "total_users": total_users,
            "tenant_members": total_users,
            "admin_count": admin_count,
            "manager_count": manager_count,
            "employee_count": total_users - admin_count - manager_count,
        }
    )
