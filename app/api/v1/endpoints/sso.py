import logging
import secrets
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.database import get_db, get_supabase_admin_client
from app.core.security import privacy
from app.core.response import success_response, error_response
from app.models.identity import UserIdentity, AuditLog
from app.models.tenant import Tenant, TenantMember
from app.services.sso_service import sso_service, SSOUserInfo
from app.api.deps.auth import require_role
from app.config import get_settings

logger = logging.getLogger("sentinel.sso")
settings = get_settings()
router = APIRouter()

# In-memory state store (use Redis in production)
_sso_states: dict[str, dict] = {}


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

    sso_provider = sso_service.get_provider(provider)
    if not sso_provider:
        return error_response(
            "provider_not_found", f"SSO provider '{provider}' not found"
        )

    # Exchange code for user info
    user_info = await sso_provider.exchange_code(
        code=code,
        redirect_uri=_sso_states.get(state, {}).get(
            "redirect_uri", "http://localhost:3000/auth/sso/callback"
        ),
    )

    if not user_info:
        return error_response(
            "sso_failed", "Failed to retrieve user info from SSO provider"
        )

    # Find or create user
    user_hash = privacy.hash_identity(user_info.email)
    existing = db.query(UserIdentity).filter_by(user_hash=user_hash).first()

    if not existing:
        # Create user from SSO
        user = UserIdentity(
            user_hash=user_hash,
            email_encrypted=privacy.encrypt(user_info.email),
            role="employee",
        )
        db.add(user)
        db.flush()

        # Create default tenant
        from uuid import uuid4

        tenant_id = uuid4()
        tenant = Tenant(
            id=tenant_id,
            name=f"{user_info.name or user_info.email}'s Workspace",
            slug=f"{user_hash[:8]}-workspace",
            plan="free",
            status="active",
        )
        db.add(tenant)
        db.flush()

        member = TenantMember(
            tenant_id=tenant_id,
            user_hash=user_hash,
            role="owner",
        )
        db.add(member)

        # Log SSO registration
        db.add(
            AuditLog(
                user_hash=user_hash,
                action="auth:sso_register",
                details={"provider": provider, "email_domain": user_info.tenant_domain},
            )
        )

        db.commit()
    else:
        # Log SSO login
        db.add(
            AuditLog(
                user_hash=user_hash,
                action="auth:sso_login",
                details={"provider": provider},
            )
        )
        db.commit()

    # Clean up state
    _sso_states.pop(state, None)

    # Sign in via Supabase to get JWT tokens
    try:
        supabase = get_supabase_admin_client()
        # Generate a magic link or sign in the user
        # For demo: return user info and let frontend handle session
        return success_response(
            {
                "user_hash": user_hash,
                "email": user_info.email,
                "name": user_info.name,
                "provider": provider,
                "message": "SSO authentication successful. Use /auth/login with credentials or Supabase magic link to complete session.",
            }
        )
    except Exception as e:
        logger.exception("SSO post-auth failed")
        return success_response(
            {
                "user_hash": user_hash,
                "email": user_info.email,
                "name": user_info.name,
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
