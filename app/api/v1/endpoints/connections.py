"""
Connections API Endpoints

OAuth connection management for external tool integrations via Composio.
Handles initiating OAuth flows, processing callbacks, checking connection
status, and disconnecting integrations.
"""

import logging
from urllib.parse import urlencode, quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional

from app.models.identity import UserIdentity
from app.models.tenant import TenantMember
from app.api.deps.auth import get_current_user_identity, get_tenant_member
from app.core.database import get_db
from app.core.security import privacy
from app.config import get_settings
from app.integrations.composio_client import composio_client
from app.services.mcp_tool_router import mcp_tool_router
from app.services.audit_service import AuditService, AuditAction

logger = logging.getLogger("sentinel.api.connections")
settings = get_settings()

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_entity_id(user: UserIdentity) -> str:
    """Build the Composio entity ID from the user's encrypted email."""
    email = privacy.decrypt(user.email_encrypted) if user.email_encrypted else ""
    return f"{email}-{settings.environment}" if email else ""


def _get_backend_base(request: Request) -> str:
    """Derive the backend base URL from the incoming request."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost:8000"))
    return f"{scheme}://{host}"


def _get_frontend_fallback() -> str:
    """Return the first allowed origin as a frontend fallback URL."""
    origins = settings.allowed_origins.split(",")
    return origins[0].strip() if origins else "http://localhost:3000"


def _validate_redirect_url(url: str) -> str:
    """Validate redirect URL against allowed origins to prevent open redirect."""
    if not url:
        return _get_frontend_fallback() + "/marketplace"
    from urllib.parse import urlparse
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    allowed = [o.strip() for o in settings.allowed_origins.split(",")]
    if origin not in allowed:
        logger.warning("Blocked redirect to non-allowed origin: %s", origin)
        return _get_frontend_fallback() + "/marketplace"
    return url


def _safe_entity_log(entity_id: str) -> str:
    """Mask entity ID for safe logging (hide email PII)."""
    if not entity_id or "@" not in entity_id:
        return entity_id[:8] + "..." if entity_id else "(empty)"
    parts = entity_id.split("@")
    return f"{parts[0][:3]}***@***-{entity_id.split('-')[-1]}" if "-" in entity_id else f"{parts[0][:3]}***@***"


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class InitiateConnectionRequest(BaseModel):
    """Request to start an OAuth connection flow."""
    toolkit_name: str = Field(..., description="Toolkit slug to connect (e.g. 'gmail', 'slack')")
    success_url: str = Field(
        default="",
        description="Frontend URL to redirect after OAuth completes",
    )


class DisconnectRequest(BaseModel):
    """Request to disconnect a toolkit integration."""
    toolkit_name: str = Field(..., description="Toolkit slug to disconnect")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/initiate")
async def initiate_connection(
    body: InitiateConnectionRequest,
    request: Request,
    user: UserIdentity = Depends(get_current_user_identity),
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
):
    """
    Initiate an OAuth connection to an external toolkit.

    Returns a redirect URL that the frontend should open so the user can
    authorize the integration. After authorization Composio redirects to
    our /connections/callback endpoint which in turn redirects the user
    back to the frontend success_url.
    """
    if not composio_client.is_available():
        return {
            "success": False,
            "toolkit_name": body.toolkit_name,
            "message": "Integration service not configured. Set COMPOSIO_API_KEY.",
            "redirect_url": None,
        }

    entity_id = _get_entity_id(user)
    if not entity_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to resolve user identity for connection.",
        )

    # Build callback URL that Composio will redirect to after OAuth
    success_url = body.success_url or f"{_get_frontend_fallback()}/marketplace"
    backend_base = _get_backend_base(request)
    callback_url = (
        f"{backend_base}/api/v1/connections/callback"
        f"?success_url={quote(success_url, safe='')}"
    )

    result = await composio_client.initiate_connection(
        tool_slug=body.toolkit_name,
        entity_id=entity_id,
        callback_url=callback_url,
    )

    if not result.get("success"):
        logger.error(
            f"initiate_connection failed (toolkit={body.toolkit_name}): "
            f"{result.get('error', 'unknown')}"
        )
        return {
            "success": False,
            "toolkit_name": body.toolkit_name,
            "message": "Failed to initiate connection. Please try again.",
            "redirect_url": None,
        }

    # No-auth toolkit (e.g. a webhook-only integration)
    if result.get("no_auth"):
        audit = AuditService(db)
        audit.log(
            actor_hash=member.user_hash,
            actor_role=member.role,
            action=AuditAction.TOOL_CONNECTED,
            details={"toolkit_name": body.toolkit_name, "status": "no_auth"},
            tenant_id=member.tenant_id,
        )
        db.commit()
        # Invalidate MCP Tool Router cache so new tools are discovered immediately
        mcp_tool_router.invalidate(entity_id)

        return {
            "success": True,
            "toolkit_name": body.toolkit_name,
            "redirect_url": None,
            "no_auth": True,
            "message": f"{body.toolkit_name} connected (no OAuth required).",
        }

    # Standard OAuth flow
    audit = AuditService(db)
    audit.log(
        actor_hash=member.user_hash,
        actor_role=member.role,
        action=AuditAction.TOOL_CONNECTED,
        details={"toolkit_name": body.toolkit_name, "status": "initiated"},
        tenant_id=member.tenant_id,
    )
    db.commit()

    # Pre-invalidate MCP cache so the first poll after OAuth completes fetches
    # a fresh connection list from Composio rather than hitting a stale cache.
    mcp_tool_router.invalidate(entity_id)

    return {
        "success": True,
        "redirect_url": result.get("redirect_url"),
        "connection_id": result.get("connection_id"),
        "toolkit_name": body.toolkit_name,
    }


@router.get("/callback")
async def connection_callback(
    success_url: str = Query(default=""),
    error: Optional[str] = Query(default=None),
    connected_account_id: Optional[str] = Query(default=None),
    connection_id: Optional[str] = Query(default=None),
    connection_status: Optional[str] = Query(default=None, alias="status"),
):
    """
    OAuth callback endpoint invoked by Composio after the user completes
    (or cancels) the authorization flow.

    Always returns a RedirectResponse (never JSON) so the browser lands
    back on the frontend.  No auth required because this is called by
    Composio's redirect, not by the frontend directly.
    """
    redirect_target = _validate_redirect_url(success_url)

    if error:
        logger.warning(f"Connection callback received error: {error}")
        separator = "&" if "?" in redirect_target else "?"
        return RedirectResponse(
            url=f"{redirect_target}{separator}error={quote(error, safe='')}",
            status_code=302,
        )

    # Success path
    separator = "&" if "?" in redirect_target else "?"
    return RedirectResponse(
        url=f"{redirect_target}{separator}connection=success",
        status_code=302,
    )


@router.get("/connected")
async def get_connected_tools(
    user: UserIdentity = Depends(get_current_user_identity),
    member: TenantMember = Depends(get_tenant_member),
):
    """
    Get the list of toolkits the current user has active connections for.
    """
    if not composio_client.is_available():
        return {"tools": [], "total": 0, "composio_enabled": False}

    entity_id = _get_entity_id(user)
    if not entity_id:
        return {"tools": [], "total": 0, "composio_enabled": True}

    connected_slugs = await composio_client.get_connected_integrations(entity_id)

    return {
        "tools": connected_slugs,
        "total": len(connected_slugs),
        "composio_enabled": True,
    }


@router.get("/toolkit-status")
async def get_toolkit_status(
    toolkit_name: str = Query(..., description="Toolkit slug to check"),
    user: UserIdentity = Depends(get_current_user_identity),
    member: TenantMember = Depends(get_tenant_member),
):
    """
    Check whether a specific toolkit is connected for the current user.
    """
    if not composio_client.is_available():
        return {
            "toolkit_name": toolkit_name,
            "is_connected": False,
            "composio_enabled": False,
        }

    entity_id = _get_entity_id(user)
    if not entity_id:
        return {
            "toolkit_name": toolkit_name,
            "is_connected": False,
            "composio_enabled": True,
        }

    connected_slugs = await composio_client.get_connected_integrations(entity_id)

    return {
        "toolkit_name": toolkit_name,
        "is_connected": toolkit_name.lower() in [s.lower() for s in connected_slugs],
        "composio_enabled": True,
    }


@router.post("/invalidate-cache")
async def invalidate_tool_cache(
    user: UserIdentity = Depends(get_current_user_identity),
):
    """Invalidate the MCP Tool Router cache for the current user.

    Called by the frontend after connecting/disconnecting a tool
    to ensure the next chat message sees the updated tool set.
    """
    entity_id = _get_entity_id(user)
    logger.info("Invalidating MCP cache for entity_id=%s", _safe_entity_log(entity_id))
    invalidated = mcp_tool_router.invalidate(entity_id)
    # Fallback: if entity_id didn't match any cached key, clear everything
    if not invalidated:
        cleared = mcp_tool_router.invalidate_all()
        logger.info("Fallback: cleared %d cached sessions", cleared)
    return {"success": True, "invalidated": invalidated}


@router.post("/disconnect")
async def disconnect_tool(
    body: DisconnectRequest,
    user: UserIdentity = Depends(get_current_user_identity),
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
):
    """
    Disconnect an external toolkit integration for the current user.

    Removes all connected accounts for the specified toolkit from Composio
    and logs the action for audit compliance.
    """
    if not composio_client.is_available():
        return {
            "success": False,
            "toolkit_name": body.toolkit_name,
            "message": "Integration service not configured.",
        }

    entity_id = _get_entity_id(user)
    if not entity_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to resolve user identity for disconnection.",
        )

    result = await composio_client.remove_connection(
        tool_slug=body.toolkit_name,
        entity_id=entity_id,
    )

    if not result.get("success"):
        logger.warning(
            f"disconnect_tool: no accounts found or delete failed "
            f"(toolkit={body.toolkit_name}, entity={_safe_entity_log(entity_id)})"
        )
        return {
            "success": False,
            "toolkit_name": body.toolkit_name,
            "message": "No active connection found or disconnection failed.",
        }

    audit = AuditService(db)
    audit.log(
        actor_hash=member.user_hash,
        actor_role=member.role,
        action=AuditAction.TOOL_DISCONNECTED,
        details={
            "toolkit_name": body.toolkit_name,
            "deleted_count": result.get("deleted_count", 0),
        },
        tenant_id=member.tenant_id,
    )
    db.commit()

    # Invalidate MCP Tool Router cache so next request picks up the change
    mcp_tool_router.invalidate(entity_id)

    return {
        "success": True,
        "toolkit_name": body.toolkit_name,
        "message": "Disconnected successfully.",
    }


@router.post("/post-connect-sync")
async def post_connect_sync(
    background_tasks: BackgroundTasks,
    user: UserIdentity = Depends(get_current_user_identity),
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
):
    """Called by frontend after OAuth completes. Triggers 7-day backfill in background."""
    entity_id = _get_entity_id(user)
    if not entity_id:
        raise HTTPException(status_code=400, detail="Unable to resolve user identity")

    from app.services.data_sync import background_sync
    background_tasks.add_task(
        background_sync, entity_id, member.user_hash, str(member.tenant_id)
    )

    logger.info("Post-connect sync scheduled for entity=%s", _safe_entity_log(entity_id))
    return {"success": True, "message": "Data sync started in background. Check the dashboard in ~30 seconds."}
