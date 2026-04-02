import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from uuid import uuid4

from app.core.database import get_db, get_supabase_client, get_supabase_admin_client
from app.core.security import privacy
from app.core.response import success_response, error_response
from app.models.identity import UserIdentity
from app.models.tenant import Tenant, TenantMember
from app.api.deps.auth import get_current_user_identity
from app.schemas.auth import (
    RegisterRequest,
    LoginRequest,
    RefreshRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    SwitchTenantRequest,
)

logger = logging.getLogger("sentinel.auth")

router = APIRouter()


@router.post("/register")
async def register(body: RegisterRequest, db: Session = Depends(get_db)):
    try:
        user_hash = privacy.hash_identity(body.email)

        # Check if user already exists locally
        existing = db.query(UserIdentity).filter_by(user_hash=user_hash).first()

        if not existing:
            # Create user in Supabase Auth (may fail if already exists there)
            try:
                admin_client = get_supabase_admin_client()
                admin_client.auth.admin.create_user(
                    {
                        "email": body.email,
                        "password": body.password,
                        "email_confirm": True,
                    }
                )
            except Exception:
                # User may already exist in Supabase Auth — that's OK
                pass

            # Create UserIdentity in local DB
            user = UserIdentity(
                user_hash=user_hash,
                email_encrypted=privacy.encrypt(body.email),
                role="employee",
            )
            db.add(user)
            db.flush()  # Flush to ensure user exists before tenant

            # Create default notification preferences
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

            # Create default Tenant for the new user
            tenant_id = uuid4()
            tenant_slug = f"{user_hash[:8]}-workspace"
            tenant = Tenant(
                id=tenant_id,
                name=f"{body.full_name or body.email}'s Workspace",
                slug=tenant_slug,
                plan="free",
                status="active",
            )
            db.add(tenant)
            db.flush()

            # Create TenantMember as owner
            member = TenantMember(
                tenant_id=tenant_id,
                user_hash=user_hash,
                role="owner",
            )
            db.add(member)
        else:
            # Existing user — find their first tenant
            existing_membership = (
                db.query(TenantMember)
                .filter_by(user_hash=user_hash)
                .first()
            )
            tenant_id = existing_membership.tenant_id if existing_membership else None

        db.commit()

        # Sign in to get tokens
        supabase = get_supabase_client()
        auth_result = supabase.auth.sign_in_with_password(
            {"email": body.email, "password": body.password}
        )

        return success_response(
            {
                "access_token": auth_result.session.access_token,
                "refresh_token": auth_result.session.refresh_token,
                "token_type": "bearer",
                "user_hash": user_hash,
                "tenant_id": str(tenant_id) if tenant_id else None,
            }
        )

    except Exception as e:
        db.rollback()
        logger.exception("Registration failed")
        return error_response(
            "registration_failed", "Registration failed. Please try again."
        )


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

    return success_response(
        {
            "user_hash": user.user_hash,
            "email": email,
            "role": user.role,
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
