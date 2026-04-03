import logging
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

    # 2. Create Supabase Auth user
    try:
        admin_client = get_supabase_admin_client()
        auth_response = admin_client.auth.admin.create_user(
            {
                "email": invitation.email,
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

    # 3. Create UserIdentity
    user_hash = privacy.hash_identity(invitation.email)
    user_identity = UserIdentity(
        user_hash=user_hash,
        tenant_id=invitation.tenant_id,
        email_encrypted=privacy.encrypt(invitation.email),
    )
    db.add(user_identity)
    db.flush()

    # 4. Create TenantMember
    member = TenantMember(
        tenant_id=invitation.tenant_id,
        user_hash=user_hash,
        role=invitation.role,
        team_id=invitation.team_id,
        invited_by=invitation.invited_by,
    )
    db.add(member)

    # 5. Create default notification preferences
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

    # 6. Mark invitation accepted and clear plaintext email (PII)
    original_email = invitation.email
    invitation.status = "accepted"
    invitation.email = "REDACTED"

    # 7. Audit log
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

    # 8. Sign in to get session tokens
    try:
        supabase = get_supabase_client()
        signin = supabase.auth.sign_in_with_password(
            {"email": original_email, "password": body.password}
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
async def login(body: LoginRequest, db: Session = Depends(get_db)):
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

    except Exception as e:
        logger.exception("Login failed")
        raise HTTPException(
            status_code=401,
            detail={
                "success": False,
                "error": {
                    "code": "login_failed",
                    "message": "Invalid email or password",
                },
            },
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
    user: UserIdentity = Depends(get_current_user_identity),
):
    try:
        # Note: Client-side Supabase auth handles session invalidation
        # This endpoint exists for API consistency and audit logging
        logger.info("User logged out: %s", user.user_hash)
        return success_response({"message": "Logged out successfully"})
    except Exception:
        return success_response({"message": "Logged out successfully"})


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
