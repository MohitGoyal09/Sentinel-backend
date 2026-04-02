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

from app.config import get_settings

logger = logging.getLogger("sentinel.composio")
settings = get_settings()

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
            logger.error(f"Calendar fetch failed for {entity_id}: {e}")
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
            logger.error(f"Failed to get connected integrations for {entity_id}: {e}")
            return []

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
            "slack": {
                "search_messages": "SLACK_SEARCH_MESSAGES",
                "get_user": "SLACK_GET_USER_BY_ID",
            },
            "github": {
                "list_commits": "GITHUB_LIST_COMMITS",
                "get_pull_request": "GITHUB_GET_A_PULL_REQUEST",
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
