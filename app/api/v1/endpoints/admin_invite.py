"""
Admin Invite Endpoint (/admin/invite)

Allows admins to send token-based invitations to new users.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.api.deps.auth import require_role
from app.core.database import get_db
from app.core.security import privacy
from app.models.invitation import Invitation
from app.models.team import Team
from app.models.tenant import TenantMember
from app.services.permission_service import PermissionService
from app.utils.email import send_invite_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin", "Invitations"])

VALID_INVITE_ROLES = {"employee", "manager", "admin"}


class InviteRequest(BaseModel):
    email: EmailStr
    role: str = Field(..., pattern=r"^(employee|manager|admin)$")
    team_id: Optional[str] = None


@router.post("/invite")
def create_invite(
    body: InviteRequest,
    request: Request,
    caller: TenantMember = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Admin-only: create an invitation for a new user.

    - Validates role is employee/manager/admin
    - Rejects duplicate pending invites for same email in this tenant
    - If team_id provided, validates team exists in this tenant
    - Generates cryptographic 86-char URL-safe token (7-day expiry)
    - Sends invite email (logs in dev mode)
    - Writes audit log
    """
    tenant_id = caller.tenant_id

    # Check for existing pending invite
    email_hash = privacy.hash_identity(body.email)
    existing = (
        db.query(Invitation)
        .filter(
            Invitation.tenant_id == tenant_id,
            Invitation.email_hash == email_hash,
            Invitation.status == "pending",
            Invitation.expires_at > datetime.now(timezone.utc).replace(tzinfo=None),
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"'{body.email}' already has a pending invitation for this tenant",
        )

    # Validate team_id if provided
    team_uuid = None
    normalized_team_id = (body.team_id or "").strip().lower() if body.team_id is not None else None
    if normalized_team_id not in (None, "", "none", "null"):
        try:
            team_uuid = UUID(body.team_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="team_id must be a valid UUID",
            )
        team = (
            db.query(Team)
            .filter(Team.id == team_uuid, Team.tenant_id == tenant_id)
            .first()
        )
        if team is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Team not found in this tenant",
            )

    token = secrets.token_urlsafe(64)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expires_at = now + timedelta(days=7)

    invitation = Invitation(
        tenant_id=tenant_id,
        email_hash=email_hash,
        email_encrypted=privacy.encrypt(body.email),
        token=token,
        role=body.role,
        team_id=team_uuid,
        invited_by=caller.user_hash,
        status="pending",
        created_at=now,
        expires_at=expires_at,
    )
    db.add(invitation)

    # Audit log
    PermissionService.log_data_access(
        db,
        actor_hash=caller.user_hash,
        actor_role=caller.role,
        target_hash=email_hash,
        action="user_invited",
        tenant_id=str(tenant_id),
        ip_address=request.headers.get(
            "X-Forwarded-For",
            request.client.host if request.client else "unknown",
        ),
        details={
            "invited_email_hash": email_hash,
            "role": body.role,
            "team_id": body.team_id,
        },
    )

    db.commit()
    db.refresh(invitation)

    # Send invite email (non-blocking failure: log, don't 500)
    try:
        send_invite_email(
            recipient_email=body.email,
            invited_by_name=caller.user_hash,
            token=token,
            role=body.role,
        )
    except Exception as exc:
        logger.error("Failed to send invite email: %s", exc)

    return {
        "id": str(invitation.id),
        "email": body.email,
        "role": invitation.role,
        "team_id": str(invitation.team_id) if invitation.team_id else None,
        "status": invitation.status,
        "expires_at": invitation.expires_at.isoformat(),
    }
