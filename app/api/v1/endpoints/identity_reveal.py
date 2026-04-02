"""
Identity Reveal Endpoint (/team/reveal-identity)

Explicit identity reveal with full audit logging and 36h critical override.
Part of Phase 2 Task 6 of the Sentinel RBAC system.

Access Rules:
- Admin: Always allowed (audit-logged)
- Manager: Allowed if can_manager_view_employee returns True, OR if
  the target is at CRITICAL risk for 36+ continuous hours (emergency override)
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps.auth import require_role
from app.core.database import get_db
from app.models.identity import UserIdentity
from app.models.tenant import TenantMember
from app.services.permission_service import PermissionService

router = APIRouter(prefix="/team", tags=["Team", "Identity"])


class RevealRequest(BaseModel):
    target_user_hash: str


@router.post("/reveal-identity")
def reveal_identity(
    body: RevealRequest,
    request: Request,
    member: TenantMember = Depends(require_role("manager", "admin")),
    db: Session = Depends(get_db),
) -> dict:
    """
    Reveal the identity behind a pseudonymized user hash.

    - Admins can always reveal (audit-logged).
    - Managers can reveal only if permission checks pass or the target
      has been at CRITICAL risk for 36+ hours (emergency override).
    """
    client_ip = request.client.host if request.client else None
    tenant_id = str(member.tenant_id)

    # 1. Resolve target identity
    target = (
        db.query(UserIdentity)
        .filter_by(user_hash=body.target_user_hash, tenant_id=member.tenant_id)
        .first()
    )
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target user not found in this tenant",
        )

    perm_service = PermissionService(db)

    # 2. Admin path -- always allowed
    if member.role == "admin":
        PermissionService.log_data_access(
            db,
            actor_hash=member.user_hash,
            actor_role=member.role,
            target_hash=body.target_user_hash,
            action="admin_identity_reveal",
            tenant_id=tenant_id,
            ip_address=client_ip,
            details={"override": False},
        )
        db.commit()
        return {
            "user_hash": target.user_hash,
            "has_encrypted_identity": True,
            "revealed": True,
            "reveal_reason": "admin_access",
        }

    # 3. Manager path
    can_view, reason = perm_service.can_manager_view_employee(
        db, member, body.target_user_hash
    )

    if can_view:
        PermissionService.log_data_access(
            db,
            actor_hash=member.user_hash,
            actor_role=member.role,
            target_hash=body.target_user_hash,
            action="manager_identity_reveal",
            tenant_id=tenant_id,
            ip_address=client_ip,
            details={"reason": reason},
        )
        db.commit()
        return {
            "user_hash": target.user_hash,
            "has_encrypted_identity": True,
            "revealed": True,
            "reveal_reason": "team_member",
        }

    # 4. Critical 36h override check
    if perm_service.is_critical_for_36h(body.target_user_hash):
        PermissionService.log_data_access(
            db,
            actor_hash=member.user_hash,
            actor_role=member.role,
            target_hash=body.target_user_hash,
            action="critical_36h_identity_reveal",
            tenant_id=tenant_id,
            ip_address=client_ip,
            details={"override": True, "reason": reason},
        )
        db.commit()
        return {
            "user_hash": target.user_hash,
            "has_encrypted_identity": True,
            "revealed": True,
            "reveal_reason": "critical_36h_override",
        }

    # 5. Access denied
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Identity reveal denied: {reason}",
    )
