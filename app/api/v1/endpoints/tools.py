"""
External Tools API Endpoints

Provides REST API for executing external tool integrations via Composio.
Used by the AI agent to gather real-time context from Calendar, Slack, GitHub, Jira.
Also exposes marketplace endpoints for connecting/disconnecting integrations.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from typing import Dict, Any, Optional, List

from app.models.identity import UserIdentity
from app.api.deps.auth import get_current_user_identity
from app.integrations.composio_client import composio_client
from pydantic import BaseModel, Field

logger = logging.getLogger("sentinel.api.tools")

router = APIRouter()


# ============================================================================
# MARKETPLACE / INTEGRATION MANAGEMENT SCHEMAS
# ============================================================================


class ConnectToolRequest(BaseModel):
    """Request to initiate a tool connection"""

    tool_slug: str = Field(..., description="Tool slug to connect (e.g. 'slack', 'github')")


class DisconnectToolRequest(BaseModel):
    """Request to disconnect a tool"""

    tool_slug: str = Field(..., description="Tool slug to disconnect")


class MarketplaceToolExecuteRequest(BaseModel):
    """Request to execute an action on a connected tool via marketplace endpoint"""

    tool_slug: str = Field(..., description="Tool slug")
    action: str = Field(..., description="Action to perform")
    params: Dict[str, Any] = Field(default_factory=dict, description="Action parameters")


# ============================================================================
# REQUEST/RESPONSE SCHEMAS
# ============================================================================


class ToolExecuteRequest(BaseModel):
    """Request to execute an external tool action"""

    tool: str = Field(..., description="Tool name (calendar, slack, github, jira)")
    action: str = Field(..., description="Action to perform (e.g., list_events)")
    params: Dict[str, Any] = Field(
        default_factory=dict, description="Action parameters"
    )
    entity_id: Optional[str] = Field(
        None, description="Target user entity ID (defaults to current user)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "tool": "calendar",
                "action": "list_events",
                "params": {"timeMin": "2026-03-31T00:00:00Z", "maxResults": 10},
            }
        }


class ToolExecuteResponse(BaseModel):
    """Response from tool execution"""

    success: bool
    tool: str
    action: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class CalendarAnalysisRequest(BaseModel):
    """Request for calendar meeting load analysis"""

    entity_id: Optional[str] = Field(
        None, description="Target user entity ID (defaults to current user)"
    )
    days: int = Field(
        7, ge=1, le=30, description="Number of days to analyze (1-30)"
    )


class SlackActivityRequest(BaseModel):
    entity_id: str
    days: int = Field(default=7, ge=1, le=30)


class IntegrationStatusResponse(BaseModel):
    """Status of external tool integrations"""

    composio_enabled: bool
    connected_tools: List[str]
    available_tools: List[str]


# ============================================================================
# API ENDPOINTS
# ============================================================================


@router.get("/status", response_model=IntegrationStatusResponse)
async def get_integration_status(
    entity_id: Optional[str] = None,
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """
    Get status of external tool integrations

    Returns which tools are configured and available actions.
    """
    composio_enabled = composio_client.is_available()

    # Define available actions per tool
    available_actions = {
        "calendar": [
            "list_events",
            "analyze_meeting_load",
        ],
        "slack": [
            "search_messages",
            "get_user",
        ],
        "github": [
            "list_commits",
            "get_pull_request",
        ],
    }

    connected_tools: List[str] = []
    if composio_enabled and entity_id:
        connected_tools = await composio_client.get_connected_integrations(entity_id)
    # If no entity_id, we can't check specific connections — leave as []

    return IntegrationStatusResponse(
        composio_enabled=composio_enabled,
        available_tools=list(available_actions.keys()),
        connected_tools=connected_tools,
    )


@router.post("/execute", response_model=ToolExecuteResponse)
async def execute_tool(
    request: ToolExecuteRequest,
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """
    Execute an external tool action via Composio

    Allows AI agent to fetch real-time data from integrated tools.

    Security:
    - Requires authentication
    - Entity ID defaults to current user if not specified
    - Admin users can query other users' data (for team insights)
    """
    # Default to current user's entity if not specified
    entity_id = request.entity_id or current_user.user_hash

    # Security check: Only admins can query other users
    if entity_id != current_user.user_hash and current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions to query other users",
        )

    # Check if Composio is available
    if not composio_client.is_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="External tool integration is not configured. Set COMPOSIO_API_KEY in environment.",
        )

    # Execute the tool action
    result = await composio_client.execute_tool(
        tool=request.tool,
        action=request.action,
        params=request.params,
        entity_id=entity_id,
    )

    if not result.get("success"):
        raw_error = result.get("error", "")
        logger.error(f"Tool execution failed: {raw_error}")
        return ToolExecuteResponse(
            success=False,
            tool=request.tool,
            action=request.action,
            error="Tool execution failed. Please check your integration is connected.",
        )

    return ToolExecuteResponse(
        success=True,
        tool=request.tool,
        action=request.action,
        result=result.get("result"),
    )


@router.post("/calendar/analyze")
async def analyze_calendar_load(
    request: CalendarAnalysisRequest,
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """
    Analyze calendar meeting load and detect burnout signals

    Returns:
    - Total meeting hours
    - Average hours per day
    - Back-to-back meeting count
    - Risk assessment (LOW/MODERATE/HIGH)
    - Comparison to healthy baselines
    """
    # Default to current user
    entity_id = request.entity_id or current_user.user_hash

    # Security check
    if entity_id != current_user.user_hash and current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions to analyze other users",
        )

    # Check Composio availability
    if not composio_client.is_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Calendar integration not configured",
        )

    # Perform analysis
    analysis = await composio_client.analyze_meeting_load(entity_id, days=request.days)

    if not analysis.get("success"):
        raw_error = analysis.get("error", "")
        logger.error(f"Calendar analysis failed: {raw_error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tool execution failed. Please check your integration is connected.",
        )

    return analysis


@router.get("/calendar/events/{entity_id}")
async def get_calendar_events(
    entity_id: str,
    days_ahead: int = 7,
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """
    Fetch calendar events for a user

    Args:
        entity_id: User identifier
        days_ahead: Number of days to look ahead (default 7)
    """
    # Security check
    if entity_id != current_user.user_hash and current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )

    if not composio_client.is_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Calendar integration not configured",
        )

    result = await composio_client.get_calendar_events(entity_id, days_ahead=days_ahead)

    if not result.get("success"):
        raw_error = result.get("error", "")
        logger.error(f"Calendar events fetch failed: {raw_error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tool execution failed. Please check your integration is connected.",
        )

    return result


@router.post("/slack/activity")
async def get_slack_activity(
    request: SlackActivityRequest,
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """
    Get Slack activity metrics for a user

    Useful for detecting communication overload or isolation patterns.
    """
    target_entity = request.entity_id

    # Security check
    if target_entity != current_user.user_hash and not current_user.role == "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )

    if not composio_client.is_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Slack integration not configured",
        )

    result = await composio_client.get_slack_activity(target_entity, request.days)

    if not result.get("success"):
        raw_error = result.get("error", "")
        logger.error(f"Slack activity fetch failed: {raw_error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tool execution failed. Please check your integration is connected.",
        )

    return result


# ============================================================================
# MARKETPLACE ENDPOINTS
# ============================================================================

# Static catalogue of supported integrations shown in the Marketplace UI
_AVAILABLE_TOOLS = [
    {
        "slug": "slack",
        "name": "Slack",
        "description": "Monitor communication patterns and detect isolation signals.",
        "category": "communication",
        "icon": "slack",
    },
    {
        "slug": "github",
        "name": "GitHub",
        "description": "Track commit cadence, late-night sessions and PR review load.",
        "category": "development",
        "icon": "github",
    },
    {
        "slug": "googlecalendar",
        "name": "Google Calendar",
        "description": "Analyse meeting density and back-to-back scheduling patterns.",
        "category": "productivity",
        "icon": "google",
    },
    {
        "slug": "jira",
        "name": "Jira",
        "description": "Monitor ticket velocity, overdue items and sprint pressure.",
        "category": "project_management",
        "icon": "jira",
    },
    {
        "slug": "zoom",
        "name": "Zoom",
        "description": "Detect meeting fatigue from video-call frequency and duration.",
        "category": "communication",
        "icon": "zoom",
    },
    {
        "slug": "linear",
        "name": "Linear",
        "description": "Track issue workload and cycle time for engineering health.",
        "category": "project_management",
        "icon": "linear",
    },
]


@router.get("/available")
async def get_available_tools(
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """
    Get list of all available integration tools.

    Returns the static catalogue regardless of whether Composio is configured.
    Requires authentication to prevent information disclosure.
    """
    return {
        "tools": _AVAILABLE_TOOLS,
        "total": len(_AVAILABLE_TOOLS),
    }


@router.get("/connected")
async def get_connected_tools(
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """
    Get list of connected integration tools for the current user.

    Queries Composio for active connected accounts.  Falls back gracefully
    to an empty list when Composio is not configured or the call fails.
    """
    if not composio_client.is_available():
        logger.info("Composio not configured — returning empty connected tools list")
        return {"tools": [], "total": 0, "composio_enabled": False}

    connected_slugs = await composio_client.get_connected_integrations(
        current_user.user_hash
    )

    # Annotate with metadata from the catalogue where available
    slug_meta = {t["slug"]: t for t in _AVAILABLE_TOOLS}
    tools = []
    for slug in connected_slugs:
        meta = slug_meta.get(slug, {"slug": slug, "name": slug.title()})
        tools.append({**meta, "status": "connected"})

    return {"tools": tools, "total": len(tools), "composio_enabled": True}


@router.post("/connect")
async def connect_tool(
    request: ConnectToolRequest,
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """
    Initiate connection to an integration tool via Composio.

    Returns a redirect URL for the OAuth flow when Composio is available,
    or a graceful response when it is not configured.
    """
    if not composio_client.is_available():
        logger.warning(
            "connect_tool called but Composio is not configured "
            f"(user={current_user.user_hash}, tool={request.tool_slug})"
        )
        return {
            "success": False,
            "tool_slug": request.tool_slug,
            "message": "Integration service not configured. Set COMPOSIO_API_KEY to enable connections.",
            "redirect_url": None,
        }

    try:
        # Composio initiates OAuth and returns a redirect URL for the user
        redirect_url = await composio_client.initiate_connection(
            tool_slug=request.tool_slug,
            entity_id=current_user.user_hash,
        )
        return {
            "success": True,
            "tool_slug": request.tool_slug,
            "redirect_url": redirect_url,
            "message": f"Redirect to complete {request.tool_slug} connection",
        }
    except Exception as exc:
        logger.error(f"connect_tool failed (tool={request.tool_slug}): {exc}")
        return {
            "success": False,
            "tool_slug": request.tool_slug,
            "message": "Failed to initiate connection. Please try again.",
            "redirect_url": None,
        }


@router.post("/disconnect")
async def disconnect_tool(
    request: DisconnectToolRequest,
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """
    Disconnect an integration tool for the current user.

    Removes the active connected account from Composio.
    Falls back gracefully when Composio is not configured.
    """
    if not composio_client.is_available():
        logger.warning(
            "disconnect_tool called but Composio is not configured "
            f"(user={current_user.user_hash}, tool={request.tool_slug})"
        )
        return {
            "success": False,
            "tool_slug": request.tool_slug,
            "message": "Integration service not configured.",
        }

    try:
        await composio_client.remove_connection(
            tool_slug=request.tool_slug,
            entity_id=current_user.user_hash,
        )
        return {
            "success": True,
            "tool_slug": request.tool_slug,
            "message": f"{request.tool_slug} disconnected successfully.",
        }
    except Exception as exc:
        logger.error(f"disconnect_tool failed (tool={request.tool_slug}): {exc}")
        return {
            "success": False,
            "tool_slug": request.tool_slug,
            "message": "Failed to disconnect tool. Please try again.",
        }


@router.post("/marketplace/execute")
async def execute_marketplace_tool_action(
    request: MarketplaceToolExecuteRequest,
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """
    Execute an action on a connected integration tool.

    Delegates to the generic Composio execute_tool method.
    Returns a graceful error response (not 500) when Composio is unavailable.
    """
    if not composio_client.is_available():
        logger.warning(
            "execute marketplace action called but Composio is not configured "
            f"(user={current_user.user_hash}, tool={request.tool_slug})"
        )
        return {
            "success": False,
            "tool_slug": request.tool_slug,
            "action": request.action,
            "result": None,
            "message": "Integration service not configured. Set COMPOSIO_API_KEY to enable tool execution.",
        }

    result = await composio_client.execute_tool(
        tool=request.tool_slug,
        action=request.action,
        params=request.params,
        entity_id=current_user.user_hash,
    )

    if not result.get("success"):
        raw_error = result.get("error", "")
        logger.error(
            f"execute marketplace action failed "
            f"(tool={request.tool_slug}, action={request.action}): {raw_error}"
        )
        return {
            "success": False,
            "tool_slug": request.tool_slug,
            "action": request.action,
            "result": None,
            "message": "Tool action failed. Please verify the integration is connected.",
        }

    return {
        "success": True,
        "tool_slug": request.tool_slug,
        "action": request.action,
        "result": result.get("result"),
        "message": "Action executed successfully.",
    }
