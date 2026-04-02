"""
Admin Promote/Demote API Endpoint (/admin/members)

Handles role promotion and demotion with five safety guards:
1. Admin cannot change their own role
2. Last admin cannot be demoted
3. Promoting to manager requires team assignment
4. Demoting manager with team requires reassignment info
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps.auth import require_role
from app.core.database import get_db
from app.models.team import Team
from app.models.tenant import TenantMember
from app.services.permission_service import PermissionService

router = APIRouter(prefix="/admin/members", tags=["Admin Promote"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------
class PromoteDemoteRequest(BaseModel):
    target_user_hash: str
    new_role: str = Field(..., pattern=r"^(employee|manager|admin)$")
    new_team_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Safety guards (extracted for testability)
# ---------------------------------------------------------------------------
def validate_promote_demote_guards(
    db: Session,
    caller: TenantMember,
    target: TenantMember,
    new_role: str,
    tenant_id: str,
    new_team_id: Optional[str] = None,
) -> None:
    """Validate all safety guards before allowing a promote/demote operation.

    Raises ``HTTPException`` on guard violation; returns ``None`` if all pass.
    """
    # Guard 1: Admin cannot change their own role
    if caller.user_hash == target.user_hash:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot change yourself — admin cannot modify their own role",
        )

    # Guard 2: Last admin cannot be demoted
    if target.role == "admin" and new_role != "admin":
        admin_count = (
            db.query(TenantMember)
            .filter(
                TenantMember.tenant_id == tenant_id,
                TenantMember.role == "admin",
            )
            .count()
        )
        if admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot demote the last admin — at least one admin must remain",
            )

    # Guard 3: Promoting to manager requires team assignment
    if new_role == "manager" and new_team_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Promoting to manager requires a team assignment",
        )

    # Guard 4: Demoting manager with team requires reassignment
    if (
        target.role == "manager"
        and new_role == "employee"
        and target.team_id is not None
        and new_team_id is None
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Must reassign team before demoting a manager who leads a team",
        )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@router.post("/promote")
def promote_demote_member(
    body: PromoteDemoteRequest,
    caller: TenantMember = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Promote or demote a tenant member's role with safety guards."""
    tenant_id = str(caller.tenant_id)

    # Look up the target member within the same tenant
    target = (
        db.query(TenantMember)
        .filter(
            TenantMember.tenant_id == caller.tenant_id,
            TenantMember.user_hash == body.target_user_hash,
        )
        .first()
    )

    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target member not found in this tenant",
        )

    old_role = target.role

    # Run safety guards
    validate_promote_demote_guards(
        db=db,
        caller=caller,
        target=target,
        new_role=body.new_role,
        tenant_id=tenant_id,
        new_team_id=body.new_team_id,
    )

    # Apply role change
    target.role = body.new_role

    if body.new_role == "manager" and body.new_team_id:
        # Validate team exists in this tenant (prevent IDOR)
        team_uuid = UUID(body.new_team_id)
        team = (
            db.query(Team)
            .filter(Team.id == team_uuid, Team.tenant_id == caller.tenant_id)
            .first()
        )
        if team is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Team not found in this tenant",
            )
        target.team_id = team_uuid
    elif body.new_role == "employee":
        target.team_id = None

    # Audit log
    PermissionService.log_data_access(
        db,
        actor_hash=caller.user_hash,
        actor_role=caller.role,
        target_hash=target.user_hash,
        action="promote_demote_role",
        tenant_id=tenant_id,
        ip_address="internal",
        details={
            "old_role": old_role,
            "new_role": body.new_role,
            "team_id": body.new_team_id,
        },
    )

    db.commit()

    return {
        "user_hash": target.user_hash,
        "old_role": old_role,
        "new_role": body.new_role,
        "team_id": str(target.team_id) if target.team_id else None,
        "updated": datetime.now(timezone.utc).isoformat(),
    }
