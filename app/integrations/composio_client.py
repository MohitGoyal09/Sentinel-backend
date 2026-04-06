"""
Composio Integration Client

Provides unified interface to external tools (Calendar, Slack, GitHub, Jira)
for the Sentinel AI agent to gather additional context for employee insights.

Composio SDK v1.0.0+: uses Composio(api_key=...).tools.execute(slug, arguments, user_id=...)
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, timezone

import requests as http_requests

from app.config import get_settings

logger = logging.getLogger("sentinel.composio")
settings = get_settings()


def _safe_entity(entity_id: str) -> str:
    """Mask entity ID for logging — hides the email portion."""
    if not entity_id or "@" not in entity_id:
        return entity_id[:8] + "..." if len(entity_id) > 8 else entity_id
    user_part = entity_id.split("@")[0]
    return f"{user_part[:3]}***@***"

# Graceful import — server must start even without composio configured
try:
    from composio import Composio as ComposioSDK
    _composio_available = True
except ImportError:
    _composio_available = False
    logger.warning("composio package not installed — Composio integration disabled")


class ComposioClient:
    """
    Composio Tool Router Client

    Manages connections to external tools and executes actions
    to enrich employee insights with real-time data.

    Uses Composio SDK v1.0.0+ API:
      composio = Composio(api_key=...)
      composio.tools.execute(slug, arguments, user_id=entity_id)
    """

    def __init__(self):
        """Initialize Composio client with API key from settings."""
        self.enabled = False
        self._composio = None
        self._tools = None

        if not _composio_available:
            logger.warning("Composio SDK not installed — integration disabled")
            return

        try:
            if settings.composio_api_key:
                self._composio = ComposioSDK(api_key=settings.composio_api_key)
                self._tools = self._composio.tools  # Fix 1: was self._composio.client.tools
                self.enabled = True
                logger.info("Composio client initialized successfully (SDK v1.0+)")
            else:
                logger.info("COMPOSIO_API_KEY not set — Composio integration disabled")
        except Exception as e:
            logger.warning(f"Composio initialization failed: {e}")

    def is_available(self) -> bool:
        """Check if Composio integration is available."""
        return self.enabled and self._tools is not None

    def _execute(self, slug: str, arguments: Dict[str, Any], entity_id: str) -> Dict[str, Any]:
        """Execute a Composio tool synchronously. Call via asyncio.to_thread() from async context."""
        response = self._tools.execute(
            slug=slug,
            arguments=arguments,
            user_id=entity_id,
            dangerously_skip_version_check=True,
        )
        # ToolExecutionResponse is already a plain TypedDict dict
        if isinstance(response, dict):
            return response
        # Fallback for any SDK version differences
        return {"successful": getattr(response, "successful", False),
                "data": getattr(response, "data", {}),
                "error": getattr(response, "error", str(response))}

    # ========================================================================
    # CALENDAR INTEGRATION
    # ========================================================================

    async def get_calendar_events(
        self,
        entity_id: str,
        days_ahead: int = 7,
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Fetch calendar events for a user."""
        if not self.is_available():
            return {"error": "Composio not configured", "events": []}

        try:
            if not time_min:
                time_min = datetime.now(timezone.utc)
            if not time_max:
                time_max = time_min + timedelta(days=days_ahead)

            result = await asyncio.to_thread(
                self._execute,
                "GOOGLECALENDAR_LIST_EVENTS",
                {
                    "timeMin": time_min.isoformat(),
                    "timeMax": time_max.isoformat(),
                    "singleEvents": True,
                    "orderBy": "startTime",
                    "maxResults": 100,
                },
                entity_id,
            )

            if not result.get("successful"):
                raise Exception(result.get("error", "Tool execution failed"))
            data = result.get("data", {})
            events = data.get("items", [])

            total_hours = self._calculate_meeting_hours(events)

            return {
                "success": True,
                "events": events,
                "total_events": len(events),
                "total_meeting_hours": total_hours,
                "average_hours_per_day": total_hours / days_ahead if days_ahead > 0 else 0,
                "time_range": {
                    "start": time_min.isoformat(),
                    "end": time_max.isoformat(),
                },
            }

        except Exception as e:
            logger.error(f"Calendar fetch failed for {_safe_entity(entity_id)}: {e}")
            return {"success": False, "error": str(e), "events": [], "total_events": 0, "total_meeting_hours": 0}

    def _calculate_meeting_hours(self, events: List[Dict]) -> float:
        """Calculate total meeting hours from event list."""
        total_minutes = 0
        for event in events:
            start = event.get("start", {})
            end = event.get("end", {})
            if "dateTime" not in start or "dateTime" not in end:
                continue
            try:
                start_time = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
                end_time = datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00"))
                total_minutes += (end_time - start_time).total_seconds() / 60
            except Exception:
                continue
        return total_minutes / 60

    async def analyze_meeting_load(self, entity_id: str, days: int = 7) -> Dict[str, Any]:
        """Analyze meeting load and identify potential burnout signals."""
        calendar_data = await self.get_calendar_events(entity_id, days_ahead=days)
        if not calendar_data.get("success"):
            return calendar_data

        total_hours = calendar_data.get("total_meeting_hours", 0)
        avg_per_day = calendar_data.get("average_hours_per_day", 0)
        event_count = calendar_data.get("total_events", 0)

        HEALTHY_MAX_HOURS_PER_DAY = 4.0
        HEALTHY_MAX_TOTAL_WEEKLY = 20.0

        risk_factors = []
        risk_score = 0.0

        if avg_per_day > HEALTHY_MAX_HOURS_PER_DAY:
            excess = avg_per_day - HEALTHY_MAX_HOURS_PER_DAY
            risk_factors.append(f"Averaging {avg_per_day:.1f} hours/day in meetings ({excess:.1f}h above healthy limit)")
            risk_score += min(excess / HEALTHY_MAX_HOURS_PER_DAY, 1.0)

        if total_hours > HEALTHY_MAX_TOTAL_WEEKLY:
            excess = total_hours - HEALTHY_MAX_TOTAL_WEEKLY
            risk_factors.append(f"{total_hours:.1f} hours of meetings ({excess:.1f}h above healthy weekly limit)")
            risk_score += min(excess / HEALTHY_MAX_TOTAL_WEEKLY, 1.0)

        back_to_back_count = self._detect_back_to_back_meetings(calendar_data.get("events", []))
        if back_to_back_count > 3:
            risk_factors.append(f"{back_to_back_count} instances of back-to-back meetings")
            risk_score += 0.3

        risk_score = min(risk_score / 2.0, 1.0)

        return {
            "success": True,
            "entity_id": entity_id,
            "analysis_period_days": days,
            "metrics": {
                "total_meetings": event_count,
                "total_hours": round(total_hours, 1),
                "average_hours_per_day": round(avg_per_day, 1),
                "back_to_back_count": back_to_back_count,
            },
            "risk_assessment": {
                "score": round(risk_score, 2),
                "level": self._get_risk_level(risk_score),
                "factors": risk_factors,
            },
            "comparison_to_baseline": {
                "healthy_max_daily": HEALTHY_MAX_HOURS_PER_DAY,
                "healthy_max_weekly": HEALTHY_MAX_TOTAL_WEEKLY,
                "current_daily": round(avg_per_day, 1),
                "current_weekly": round(total_hours, 1),
                "percentage_above_baseline": round(((avg_per_day / HEALTHY_MAX_HOURS_PER_DAY) - 1) * 100, 1)
                    if avg_per_day > HEALTHY_MAX_HOURS_PER_DAY else 0,
            },
        }

    def _detect_back_to_back_meetings(self, events: List[Dict]) -> int:
        """Count back-to-back meetings (less than 15min gap)."""
        if not events:
            return 0
        back_to_back = 0
        sorted_events = sorted(events, key=lambda e: e.get("start", {}).get("dateTime", ""))
        for i in range(len(sorted_events) - 1):
            current_end = sorted_events[i].get("end", {}).get("dateTime")
            next_start = sorted_events[i + 1].get("start", {}).get("dateTime")
            if not current_end or not next_start:
                continue
            try:
                end_time = datetime.fromisoformat(current_end.replace("Z", "+00:00"))
                start_time = datetime.fromisoformat(next_start.replace("Z", "+00:00"))
                if 0 <= (start_time - end_time).total_seconds() / 60 < 15:
                    back_to_back += 1
            except Exception:
                continue
        return back_to_back

    def _get_risk_level(self, score: float) -> str:
        if score >= 0.7:
            return "HIGH"
        elif score >= 0.4:
            return "MODERATE"
        elif score >= 0.2:
            return "LOW"
        return "HEALTHY"

    # ========================================================================
    # SLACK INTEGRATION
    # ========================================================================

    async def get_slack_activity(self, entity_id: str, days: int = 7) -> Dict[str, Any]:
        """Fetch Slack activity metrics for a user."""
        if not self.is_available():
            return {"error": "Composio not configured"}

        try:
            result = await asyncio.to_thread(
                self._execute,
                "SLACK_SEARCH_MESSAGES",
                {"query": f"after:{days}d", "count": 100, "sort": "timestamp"},
                entity_id,
            )

            if not result.get("successful"):
                raise Exception(result.get("error", "Tool execution failed"))
            data = result.get("data", {})
            messages = data.get("messages", {}).get("matches", [])

            return {
                "success": True,
                "total_messages": len(messages),
                "period_days": days,
                "average_per_day": len(messages) / days if days > 0 else 0,
            }
        except Exception as e:
            logger.error(f"Slack activity fetch failed: {e}")
            return {"success": False, "error": str(e)}

    # ========================================================================
    # EMAIL (GMAIL) INTEGRATION -- MULTI-STEP FETCH
    # ========================================================================

    async def get_emails(
        self,
        entity_id: str,
        max_results: int = 10,
        query: str = "",
    ) -> Dict[str, Any]:
        """Fetch emails with full details (subject, sender, date, snippet).

        Performs a two-step fetch:
          Step 1: List message IDs via GMAIL_LIST_MESSAGES
          Step 2: For each message, get full details via GMAIL_GET_MESSAGE

        Args:
            entity_id: Composio entity/user ID.
            max_results: Maximum number of emails to return.
            query: Optional Gmail search query (e.g. "is:unread").

        Returns:
            Dict with success flag, list of detailed emails, and counts.
        """
        if not self.is_available():
            return {"success": False, "error": "Composio not configured"}

        try:
            loop = asyncio.get_running_loop()

            def _sync() -> Dict[str, Any]:
                # Step 1: List message IDs
                list_args: Dict[str, Any] = {
                    "max_results": max_results,
                    "label_ids": ["INBOX"],
                }
                if query:
                    list_args["q"] = query

                list_result = self._execute(
                    slug="GMAIL_LIST_MESSAGES",
                    arguments=list_args,
                    entity_id=entity_id,
                )

                if not list_result.get("successful", False):
                    return {
                        "success": False,
                        "error": list_result.get("error", "Failed to list messages"),
                    }

                messages_data = list_result.get("data", {})
                message_list = messages_data.get("messages", [])
                total = messages_data.get("resultSizeEstimate", len(message_list))

                # Step 2: Get full details for each message
                detailed_emails: list[Dict[str, Any]] = []
                for msg in message_list[:max_results]:
                    msg_id = msg.get("id", "")
                    if not msg_id:
                        continue
                    try:
                        detail = self._execute(
                            slug="GMAIL_GET_MESSAGE",
                            arguments={"message_id": msg_id, "format": "metadata"},
                            entity_id=entity_id,
                        )
                        if not detail.get("successful", False):
                            continue

                        payload = detail.get("data", {})

                        # Extract headers (subject, from, date, to)
                        headers: Dict[str, str] = {}
                        for h in payload.get("payload", {}).get("headers", []):
                            name = h.get("name", "").lower()
                            if name in ("subject", "from", "date", "to"):
                                headers[name] = h.get("value", "")

                        # Determine read/unread status from labelIds
                        label_ids = payload.get("labelIds", [])
                        is_unread = "UNREAD" in label_ids

                        detailed_emails.append({
                            "id": msg_id,
                            "subject": headers.get("subject", "(No Subject)"),
                            "from": headers.get("from", "Unknown"),
                            "date": headers.get("date", ""),
                            "to": headers.get("to", ""),
                            "snippet": payload.get("snippet", ""),
                            "is_unread": is_unread,
                        })
                    except Exception:
                        continue

                return {
                    "success": True,
                    "emails": detailed_emails,
                    "total_count": total,
                    "fetched_count": len(detailed_emails),
                }

            return await loop.run_in_executor(None, _sync)

        except Exception as e:
            logger.error(
                "get_emails failed for %s: %s",
                _safe_entity(entity_id),
                e,
            )
            return {"success": False, "error": str(e)}

    # ========================================================================
    # CONNECTED ACCOUNTS
    # ========================================================================

    async def get_connected_integrations(self, entity_id: str) -> List[str]:
        """Return list of toolkit slugs the user has ACTIVE connected accounts for."""
        if not self.is_available():
            return []
        try:
            accounts = await asyncio.to_thread(
                lambda: self._composio.connected_accounts.list(
                    user_ids=[entity_id], statuses=["ACTIVE"]
                )
            )
            return list({acc.toolkit.slug for acc in accounts.items})
        except Exception as e:
            logger.error(f"Failed to get connected integrations for {_safe_entity(entity_id)}: {e}")
            return []

    # ========================================================================
    # CONNECTION MANAGEMENT (OAuth)
    # ========================================================================

    async def initiate_connection(
        self,
        tool_slug: str,
        entity_id: str,
        callback_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Initiate an OAuth connection for a toolkit.

        Uses the KaraX pattern:
        1. Create an auth config with Composio-managed auth
        2. Link the user to obtain a redirect URL for the OAuth flow

        Args:
            tool_slug: Toolkit identifier (e.g. 'gmail', 'slack')
            entity_id: Composio user/entity ID
            callback_url: URL Composio redirects to after OAuth completes

        Returns:
            Dict with success, redirect_url, connection_id, and optional no_auth flag
        """
        if not self.is_available():
            return {"success": False, "error": "Composio not configured"}

        def _sync() -> Dict[str, Any]:
            toolkit = tool_slug.lower()

            # Step 1: Create auth config for Composio-managed auth
            try:
                auth_config = self._composio.auth_configs.create(
                    toolkit=toolkit,
                    options={"type": "use_composio_managed_auth"},
                )
            except Exception as exc:
                error_msg = str(exc).lower()
                if "no auth" in error_msg or "noauth" in error_msg:
                    return {"success": True, "redirect_url": None, "no_auth": True}
                raise

            # Step 2: Link user to the auth config
            link_kwargs: Dict[str, Any] = {
                "user_id": entity_id,
                "auth_config_id": auth_config.id,
            }
            if callback_url:
                link_kwargs["callback_url"] = callback_url

            link_response = self._composio.connected_accounts.link(**link_kwargs)

            # Step 3: Extract redirect URL and connection ID
            redirect_url = None
            connection_id = None

            if hasattr(link_response, "redirect_url"):
                redirect_url = link_response.redirect_url
            elif hasattr(link_response, "redirectUrl"):
                redirect_url = link_response.redirectUrl
            elif isinstance(link_response, dict):
                redirect_url = link_response.get("redirect_url") or link_response.get("redirectUrl")

            if hasattr(link_response, "id"):
                connection_id = link_response.id
            elif isinstance(link_response, dict):
                connection_id = link_response.get("id")

            return {
                "success": True,
                "redirect_url": redirect_url,
                "connection_id": connection_id,
            }

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _sync)
        except Exception as e:
            logger.error(f"initiate_connection failed for {tool_slug}/{_safe_entity(entity_id)}: {e}")
            return {"success": False, "error": str(e)}

    async def remove_connection(
        self,
        tool_slug: str,
        entity_id: str,
    ) -> Dict[str, Any]:
        """
        Remove all connected accounts for a toolkit and user.

        Uses the Composio REST API to list connected accounts, then
        deletes those matching the target toolkit slug.

        Args:
            tool_slug: Toolkit identifier to disconnect (e.g. 'gmail')
            entity_id: Composio user/entity ID

        Returns:
            Dict with success flag and deleted_count
        """
        if not self.is_available():
            return {"success": False, "error": "Composio not configured", "deleted_count": 0}

        def _sync() -> Dict[str, Any]:
            api_key = settings.composio_api_key
            base_api = "https://backend.composio.dev/api/v3/connected_accounts"
            headers = {"x-api-key": api_key}
            target = tool_slug.lower()
            deleted = 0
            cursor: Optional[str] = None

            while True:
                params: Dict[str, Any] = {
                    "user_ids": [entity_id],
                    "limit": 100,
                }
                if cursor:
                    params["cursor"] = cursor

                resp = http_requests.get(base_api, headers=headers, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()

                items = data.get("items", data.get("data", []))
                if not items:
                    break

                for acc in items:
                    # Extract toolkit name from various response shapes
                    toolkit_name = ""
                    if "toolkit" in acc:
                        tk = acc["toolkit"]
                        if isinstance(tk, dict):
                            toolkit_name = tk.get("slug", "").lower()
                        elif isinstance(tk, str):
                            toolkit_name = tk.lower()
                    if not toolkit_name and "appName" in acc:
                        toolkit_name = acc["appName"].lower()
                    if not toolkit_name and "integrationId" in acc:
                        toolkit_name = acc["integrationId"].lower()

                    if toolkit_name == target:
                        acc_id = acc.get("id")
                        if acc_id:
                            try:
                                self._composio.connected_accounts.delete(acc_id)
                                deleted += 1
                            except Exception as del_err:
                                logger.warning(
                                    f"Failed to delete connected account {acc_id}: {del_err}"
                                )

                cursor = data.get("next_cursor")
                if not cursor:
                    break

            return {"success": deleted > 0, "deleted_count": deleted}

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _sync)
        except Exception as e:
            logger.error(f"remove_connection failed for {tool_slug}/{_safe_entity(entity_id)}: {e}")
            return {"success": False, "error": str(e), "deleted_count": 0}

    # ========================================================================
    # GENERIC TOOL EXECUTION
    # ========================================================================

    async def execute_tool(
        self, tool: str, action: str, params: Dict[str, Any], entity_id: str
    ) -> Dict[str, Any]:
        """
        Generic tool execution endpoint.

        Args:
            tool: Tool name (e.g., 'calendar', 'slack', 'github')
            action: Action to perform (e.g., 'list_events', 'search_messages')
            params: Action parameters
            entity_id: Composio entity/user ID

        Returns:
            Tool execution result
        """
        if not self.is_available():
            return {"success": False, "error": "Composio integration not configured"}

        # Map tool+action to Composio action slug
        action_map = {
            "calendar": {"list_events": "GOOGLECALENDAR_LIST_EVENTS"},
            "email": {
                "list_inbox": "GMAIL_LIST_MESSAGES",
                "get_message": "GMAIL_GET_MESSAGE",
                "send": "GMAIL_SEND_EMAIL",
            },
            "gmail": {
                "list_inbox": "GMAIL_LIST_MESSAGES",
                "get_message": "GMAIL_GET_MESSAGE",
                "send": "GMAIL_SEND_EMAIL",
            },
            "slack": {
                "search_messages": "SLACK_SEARCH_MESSAGES",
                "get_user": "SLACK_GET_USER_BY_ID",
            },
            "github": {
                "list_commits": "GITHUB_LIST_COMMITS",
                "list_repos": "GITHUB_LIST_USER_REPOS",
                "get_pull_request": "GITHUB_GET_A_PULL_REQUEST",
                "list_notifications": "GITHUB_LIST_NOTIFICATIONS",
            },
        }

        if tool not in action_map or action not in action_map[tool]:
            return {"success": False, "error": f"Unknown tool/action: {tool}/{action}"}

        try:
            slug = action_map[tool][action]
            result = await asyncio.to_thread(self._execute, slug, params, entity_id)
            return {"success": True, "result": result, "tool": tool, "action": action}
        except Exception as e:
            logger.error(f"Tool execution failed ({tool}/{action}): {e}")
            return {"success": False, "error": str(e), "tool": tool, "action": action}


# Global singleton
composio_client = ComposioClient()
