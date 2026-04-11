import logging
import secrets
from datetime import datetime, timezone

from cachetools import TTLCache
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.database import get_db, get_supabase_admin_client
from app.core.security import privacy
from app.core.response import success_response, error_response
from app.models.identity import UserIdentity, AuditLog
from app.models.invitation import Invitation
from app.models.tenant import Tenant, TenantMember
from app.services.sso_service import sso_service, SSOUserInfo
from app.services.permission_service import PermissionService
from app.api.deps.auth import require_role
from app.config import get_settings

logger = logging.getLogger("sentinel.sso")
settings = get_settings()
router = APIRouter()

# Bounded, auto-expiring state store (5 min TTL, max 1000 pending flows)
_sso_states: TTLCache = TTLCache(maxsize=1000, ttl=300)


@router.get("/providers")
async def list_sso_providers():
    """List available SSO providers for the login page."""
    providers = sso_service.get_available_providers()
    return success_response({"providers": providers})


@router.get("/{provider}/login")
async def sso_login(
    provider: str,
    redirect_uri: str = Query(default="http://localhost:3000/auth/sso/callback"),
):
    """Initiate SSO login flow. Returns authorization URL."""
    sso_provider = sso_service.get_provider(provider)
    if not sso_provider:
        raise HTTPException(
            status_code=404, detail=f"SSO provider '{provider}' not found"
        )

    state = secrets.token_urlsafe(32)
    _sso_states[state] = {"provider": provider, "redirect_uri": redirect_uri}

    auth_url = sso_provider.get_authorization_url(
        state=state, redirect_uri=redirect_uri
    )
    return success_response({"auth_url": auth_url, "state": state})


@router.get("/{provider}/callback")
async def sso_callback(
    provider: str,
    code: str = Query(default=None),
    state: str = Query(default=None),
    error: str = Query(default=None),
    db: Session = Depends(get_db),
):
    """Handle SSO callback after user authenticates with IdP."""
    if error:
        logger.warning("SSO callback error: provider=%s error=%s", provider, error)
        return error_response("sso_error", f"SSO authentication failed: {error}")

    if not code:
        return error_response("missing_code", "Authorization code not provided")

    # --- Bug 3 fix: validate state BEFORE exchanging auth code ---
    if state is None or state not in _sso_states:
        logger.warning("SSO callback with invalid/missing state: provider=%s", provider)
        return error_response(
            "invalid_state",
            "SSO state validation failed. Please restart the login flow.",
            status_code=400,
        )

    state_data = _sso_states.pop(state)  # consume state (single-use)

    sso_provider = sso_service.get_provider(provider)
    if not sso_provider:
        return error_response(
            "provider_not_found", f"SSO provider '{provider}' not found"
        )

    # Exchange code for user info
    user_info = await sso_provider.exchange_code(
        code=code,
        redirect_uri=state_data.get(
            "redirect_uri", "http://localhost:3000/auth/sso/callback"
        ),
    )

    if not user_info:
        return error_response(
            "sso_failed", "Failed to retrieve user info from SSO provider"
        )

    # --- Bug 1 fix: enforce invite-only registration for SSO ---
    user_hash = privacy.hash_identity(user_info.email)
    existing = db.query(UserIdentity).filter_by(user_hash=user_hash).first()

    if existing:
        # Returning user — just log and sign in
        db.add(
            AuditLog(
                user_hash=user_hash,
                action="auth:sso_login",
                details={"provider": provider},
            )
        )
        db.commit()
    else:
        # New user — must have a pending invitation
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        invitation = (
            db.query(Invitation)
            .filter(
                Invitation.email == user_info.email,
                Invitation.status == "pending",
            )
            .first()
        )

        if invitation is None:
            logger.warning(
                "SSO login rejected — no invitation: email_domain=%s provider=%s",
                user_info.tenant_domain,
                provider,
            )
            return error_response(
                "no_invitation",
                "No pending invitation found for this email. Contact your organisation admin.",
                status_code=403,
            )

        if now > invitation.expires_at:
            invitation.status = "expired"
            db.commit()
            return error_response(
                "invitation_expired",
                "Your invitation has expired. Ask your admin to resend.",
                status_code=410,
            )

        # Accept invitation: create UserIdentity + TenantMember (mirrors auth.accept_invite)
        user = UserIdentity(
            user_hash=user_hash,
            tenant_id=invitation.tenant_id,
            email_encrypted=privacy.encrypt(user_info.email),
        )
        db.add(user)
        db.flush()

        member = TenantMember(
            tenant_id=invitation.tenant_id,
            user_hash=user_hash,
            role=invitation.role,
            team_id=invitation.team_id,
            invited_by=invitation.invited_by,
        )
        db.add(member)

        # Create default notification preferences
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

        # Mark invitation accepted and redact PII
        invitation.status = "accepted"
        invitation.email = "REDACTED"

        # Audit log
        PermissionService.log_data_access(
            db,
            actor_hash=user_hash,
            actor_role=invitation.role,
            target_hash=user_hash,
            action="user_joined",
            tenant_id=str(invitation.tenant_id),
            ip_address="sso",
            details={
                "provider": provider,
                "invited_by": invitation.invited_by,
                "role": invitation.role,
                "team_id": str(invitation.team_id) if invitation.team_id else None,
                "email_domain": user_info.tenant_domain,
            },
        )

        db.commit()

    # Sign in via Supabase to get JWT tokens
    try:
        supabase = get_supabase_admin_client()
        return success_response(
            {
                "user_hash": user_hash,
                "provider": provider,
                "message": "SSO authentication successful. Use /auth/login with credentials or Supabase magic link to complete session.",
            }
        )
    except Exception as e:
        logger.exception("SSO post-auth failed")
        return success_response(
            {
                "user_hash": user_hash,
                "provider": provider,
                "message": "SSO authentication successful.",
            }
        )


@router.post("/{provider}/setup")
async def sso_provider_setup(
    provider: str,
    body: dict,
    db: Session = Depends(get_db),
    user=Depends(require_role("admin")),
):
    """Configure SSO provider settings (admin only, demo stub)."""
    return success_response(
        {
            "message": f"SSO provider '{provider}' configuration saved",
            "provider": provider,
            "config": {
                "client_id": body.get("client_id", "***configured***"),
                "domain_restriction": body.get("allowed_domains", []),
                "status": "active",
            },
        }
    )
