import logging
from collections import defaultdict
import time as _time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.core.database import get_db, get_supabase_client, get_supabase_admin_client
from app.core.security import privacy
from app.core.response import success_response, error_response
from app.models.identity import UserIdentity
from app.models.tenant import Tenant, TenantMember
from app.models.invitation import Invitation
from app.api.deps.auth import get_current_user_identity
from app.services.permission_service import PermissionService
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    SwitchTenantRequest,
)

logger = logging.getLogger("sentinel.auth")

router = APIRouter()

# Simple in-memory rate limiter for auth endpoints
_login_attempts: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_WINDOW = 300  # 5 minutes
_RATE_LIMIT_MAX = 10  # max attempts per window


def _check_rate_limit(identifier: str) -> bool:
    """Returns True if rate limited (should block)."""
    now = _time.time()
    attempts = _login_attempts[identifier]
    # Clean old entries
    _login_attempts[identifier] = [t for t in attempts if now - t < _RATE_LIMIT_WINDOW]
    if len(_login_attempts[identifier]) >= _RATE_LIMIT_MAX:
        return True
    _login_attempts[identifier].append(now)
    return False


class AcceptInviteRequest(BaseModel):
    token: str = Field(..., min_length=80, max_length=128)
    password: str = Field(..., min_length=8)


class AcceptInviteResponse(BaseModel):
    access_token: str
    refresh_token: str
    user_hash: str
    tenant_id: str
    role: str


@router.post("/accept-invite")
def accept_invite(body: AcceptInviteRequest, request: Request, db: Session = Depends(get_db)):
    """
    Public endpoint (no auth required).
    Validates invite token, creates Supabase user + UserIdentity + TenantMember.
    Returns session tokens.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Look up invitation by token
    invitation = db.query(Invitation).filter(Invitation.token == body.token).first()

    if invitation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation not found",
        )

    if invitation.status == "accepted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This invitation has already been accepted",
        )

    if now > invitation.expires_at:
        invitation.status = "expired"
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This invitation has expired. Ask your admin to resend.",
        )

    # 2. Decrypt invitation email (stored encrypted, never plaintext)
    invitation_email = privacy.decrypt(invitation.email_encrypted)

    # 3. Create Supabase Auth user
    try:
        admin_client = get_supabase_admin_client()
        auth_response = admin_client.auth.admin.create_user(
            {
                "email": invitation_email,
                "password": body.password,
                "email_confirm": True,
            }
        )
    except Exception as exc:
        logger.exception("Failed to create Supabase auth user")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create auth user",
        )

    # 4. Create UserIdentity
    user_hash = privacy.hash_identity(invitation_email)
    user_identity = UserIdentity(
        user_hash=user_hash,
        tenant_id=invitation.tenant_id,
        email_encrypted=privacy.encrypt(invitation_email),
    )
    db.add(user_identity)
    db.flush()

    # 5. Create TenantMember
    member = TenantMember(
        tenant_id=invitation.tenant_id,
        user_hash=user_hash,
        role=invitation.role,
        team_id=invitation.team_id,
        invited_by=invitation.invited_by,
    )
    db.add(member)

    # 6. Create default notification preferences
    try:
        from app.models.notification import NotificationPreference
        default_prefs = [
            {"channel": "in_app", "notification_type": "auth", "enabled": True},
            {"channel": "in_app", "notification_type": "team", "enabled": True},
            {"channel": "in_app", "notification_type": "system", "enabled": True},
            {"channel": "in_app", "notification_type": "security", "enabled": True},
            {"channel": "in_app", "notification_type": "activity", "enabled": True},
            {"channel": "email", "notification_type": "auth", "enabled": True},
            {"channel": "email", "notification_type": "security", "enabled": True},
            {"channel": "email", "notification_type": "team", "enabled": False},
        ]
        for pref in default_prefs:
            db.add(NotificationPreference(user_hash=user_hash, **pref))
    except Exception:
        pass  # Notifications are optional

    # 7. Mark invitation accepted (email is already encrypted, no redaction needed)
    invitation.status = "accepted"

    # 8. Audit log
    PermissionService.log_data_access(
        db,
        actor_hash=user_hash,
        actor_role=invitation.role,
        target_hash=user_hash,
        action="user_joined",
        tenant_id=str(invitation.tenant_id),
        ip_address=request.headers.get(
            "X-Forwarded-For",
            request.client.host if request.client else "unknown",
        ),
        details={
            "invited_by": invitation.invited_by,
            "role": invitation.role,
            "team_id": str(invitation.team_id) if invitation.team_id else None,
        },
    )

    db.commit()

    # 9. Sign in to get session tokens
    try:
        supabase = get_supabase_client()
        signin = supabase.auth.sign_in_with_password(
            {"email": invitation_email, "password": body.password}
        )
        session = signin.session
    except Exception as exc:
        logger.exception("User created but sign-in failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Account created but sign-in failed. Try logging in manually.",
        )

    return success_response({
        "access_token": session.access_token,
        "refresh_token": session.refresh_token,
        "user_hash": user_hash,
        "tenant_id": str(invitation.tenant_id),
        "role": invitation.role,
    })


@router.post("/login")
async def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    # Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    if _check_rate_limit(f"login:{client_ip}:{body.email}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please try again in 5 minutes.",
        )

    try:
        supabase = get_supabase_client()
        auth_result = supabase.auth.sign_in_with_password(
            {"email": body.email, "password": body.password}
        )

        if not auth_result or not auth_result.session:
            raise HTTPException(
                status_code=401,
                detail={
                    "success": False,
                    "error": {"code": "login_failed", "message": "Invalid credentials"},
                },
            )

        user_hash = privacy.hash_identity(body.email)

        # Get user's tenants (single JOIN query instead of N+1)
        tenant_rows = (
            db.query(Tenant, TenantMember.role)
            .join(TenantMember, Tenant.id == TenantMember.tenant_id)
            .filter(TenantMember.user_hash == user_hash)
            .all()
        )
        tenants = [
            {
                "id": str(row.Tenant.id),
                "name": row.Tenant.name,
                "slug": row.Tenant.slug,
                "role": row.role,
            }
            for row in tenant_rows
        ]

        # Create login notification
        from app.models.notification import Notification

        db.add(
            Notification(
                user_hash=user_hash,
                type="auth",
                title="New Login",
                message=f"You logged in successfully.",
                priority="normal",
                data={"event": "login"},
            )
        )
        db.commit()

        return success_response(
            {
                "access_token": auth_result.session.access_token,
                "refresh_token": auth_result.session.refresh_token,
                "token_type": "bearer",
                "tenants": tenants,
            }
        )

    except HTTPException:
        raise  # Re-raise HTTP exceptions (including our rate limit 429)
    except Exception as e:
        logger.error("Login failed with unexpected error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during login",
        )


@router.post("/refresh")
async def refresh_token(body: RefreshRequest, db: Session = Depends(get_db)):
    try:
        supabase = get_supabase_client()
        auth_result = supabase.auth.refresh_session(body.refresh_token)

        if not auth_result or not auth_result.session:
            return error_response(
                "refresh_failed", "Invalid refresh token", status_code=401
            )

        return success_response(
            {
                "access_token": auth_result.session.access_token,
                "refresh_token": auth_result.session.refresh_token,
                "token_type": "bearer",
            }
        )

    except Exception as e:
        logger.exception("Token refresh failed")
        return error_response("refresh_failed", "Token refresh failed. Please log in again.", status_code=401)


@router.post("/logout")
async def logout(
    current_user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """Logout and invalidate server-side session."""
    try:
        # Client-side Supabase auth handles token invalidation via supabase.auth.signOut()
        # Server-side, we log the action for audit
        from app.services.audit_service import AuditService, AuditAction

        audit = AuditService(db)
        primary_member = db.query(TenantMember).filter_by(user_hash=current_user.user_hash).first()
        if primary_member:
            audit.log(
                actor_hash=primary_member.user_hash,
                actor_role=primary_member.role,
                action=AuditAction.USER_LOGGED_OUT,
                details={"method": "server_logout"},
                tenant_id=primary_member.tenant_id,
            )
            db.commit()
    except Exception as e:
        logger.warning("Server-side logout cleanup failed: %s", e)

    return {"success": True, "message": "Logged out successfully"}


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest):
    try:
        supabase = get_supabase_client()
        supabase.auth.reset_password_for_email(body.email)
        return success_response(
            {"message": "Password reset email sent if account exists"}
        )
    except Exception as e:
        logger.exception("Forgot password failed")
        return error_response("forgot_password_failed", "Password reset request failed. Please try again.")


@router.post("/reset-password")
async def reset_password(body: ResetPasswordRequest):
    try:
        supabase = get_supabase_client()
        supabase.auth.update_user(
            {"password": body.new_password},
            access_token=body.access_token,
        )
        return success_response({"message": "Password updated successfully"})
    except Exception as e:
        logger.exception("Reset password failed")
        return error_response("reset_password_failed", "Password reset failed. Please try again.")


@router.get("/me")
async def get_me(
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    # Decrypt email for display
    email = privacy.decrypt(user.email_encrypted)

    # Get user's tenants (single JOIN query)
    tenant_rows = (
        db.query(Tenant, TenantMember.role)
        .join(TenantMember, Tenant.id == TenantMember.tenant_id)
        .filter(TenantMember.user_hash == user.user_hash)
        .all()
    )
    tenants = [
        {
            "id": str(row.Tenant.id),
            "name": row.Tenant.name,
            "slug": row.Tenant.slug,
            "role": row.role,
        }
        for row in tenant_rows
    ]

    # Get user's primary role from first tenant membership
    primary_member = db.query(TenantMember).filter_by(user_hash=user.user_hash).first()

    if not primary_member and not tenants:
        # User has no tenant membership — likely an orphaned auth account
        return success_response(
            {
                "user_hash": user.user_hash,
                "email": email,
                "role": None,
                "tenants": [],
                "consent_share_with_manager": False,
                "consent_share_anonymized": False,
                "error": "no_tenant_membership",
            }
        )

    primary_role = primary_member.role if primary_member else "employee"

    return success_response(
        {
            "user_hash": user.user_hash,
            "email": email,
            "role": primary_role,
            "tenants": tenants,
            "consent_share_with_manager": user.consent_share_with_manager,
            "consent_share_anonymized": user.consent_share_anonymized,
        }
    )


@router.post("/switch-tenant")
async def switch_tenant(
    body: SwitchTenantRequest,
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    # Verify user is member of requested tenant
    membership = (
        db.query(TenantMember)
        .filter_by(tenant_id=body.tenant_id, user_hash=user.user_hash)
        .first()
    )

    if not membership:
        return error_response(
            "not_member", "You are not a member of this tenant", status_code=403
        )

    tenant = db.query(Tenant).filter_by(id=body.tenant_id).first()
    if not tenant or tenant.status != "active":
        return error_response(
            "tenant_inactive", "Tenant is not active", status_code=403
        )

    return success_response(
        {
            "tenant_id": str(tenant.id),
            "tenant_name": tenant.name,
            "tenant_slug": tenant.slug,
            "role": membership.role,
            "message": "Switch tenant by including X-Tenant-ID header or tenant_id claim in subsequent requests",
        }
    )
