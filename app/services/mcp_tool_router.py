"""
MCP Tool Router -- manages Composio Tool Router sessions per user.

Creates an MCP server connection that gives the LLM function-calling
access to all of a user's connected tools (Gmail, Calendar, Slack, etc.).

Architecture:
  - In-memory dict cache with TTL (no Redis dependency for now).
  - Thread-safe session creation via asyncio.Lock per user.
  - Lazy Composio SDK initialization.

Based on KaraX ToolRouterSessionCache but simplified for Sentinel.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

from app.config import get_settings

logger = logging.getLogger("sentinel.mcp_tool_router")

settings = get_settings()

# ---------------------------------------------------------------------------
# Graceful imports -- server must start even without these packages
# ---------------------------------------------------------------------------

try:
    from composio import Composio as ComposioSDK
    _composio_available = True
except ImportError:
    ComposioSDK = None  # type: ignore[misc,assignment]
    _composio_available = False
    logger.warning("composio package not installed -- MCP Tool Router disabled")


@dataclass(frozen=True)
class MCPSession:
    """Immutable snapshot of a Tool Router session's MCP connection details."""

    url: str
    headers: dict
    created_at: float
    user_id: str


class MCPToolRouter:
    """Manages per-user MCP Tool Router sessions with in-memory caching.

    Each session is a Composio Tool Router session that exposes an MCP
    endpoint (URL + auth headers).  The LLM connects to this endpoint
    to discover and call the user's connected tools autonomously.

    Thread safety: one asyncio.Lock per user prevents duplicate session
    creation when multiple requests race for the same user.
    """

    def __init__(self, ttl_seconds: int = 1800) -> None:  # 30 min default
        self._cache: dict[str, MCPSession] = {}
        self._ttl: int = ttl_seconds
        self._composio: Optional[ComposioSDK] = None
        self._user_locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if the MCP Tool Router can be used."""
        return _composio_available and bool(settings.composio_api_key)

    async def get_session(
        self,
        user_id: str,
        *,
        force_new: bool = False,
    ) -> MCPSession:
        """Get or create an MCP session for *user_id*.

        Returns a cached session if it is still within its TTL, otherwise
        creates a fresh Tool Router session via the Composio SDK.

        Args:
            user_id: Composio entity ID (e.g. ``"user@co.com-development"``).
            force_new: If True, discard the cached session and create a new one.

        Raises:
            RuntimeError: If Composio is not configured or the SDK is missing.
        """
        if not self.is_available():
            raise RuntimeError(
                "MCP Tool Router unavailable -- ensure composio is installed "
                "and COMPOSIO_API_KEY is set"
            )

        # Per-user lock prevents duplicate session creation
        lock = self._user_locks.setdefault(user_id, asyncio.Lock())

        async with lock:
            if not force_new:
                cached = self._cache.get(user_id)
                if cached and (time.time() - cached.created_at) < self._ttl:
                    logger.debug(
                        "Reusing cached MCP session for user %s (age %.0fs)",
                        user_id[:8],
                        time.time() - cached.created_at,
                    )
                    return cached

            # Create new Tool Router session (blocking SDK call)
            composio = self._get_composio()

            loop = asyncio.get_running_loop()
            url, headers = await loop.run_in_executor(
                None,
                self._create_session_sync,
                composio,
                user_id,
            )

            session = MCPSession(
                url=url,
                headers=headers,
                created_at=time.time(),
                user_id=user_id,
            )
            self._cache[user_id] = session
            logger.info(
                "Created MCP Tool Router session for user %s", user_id[:8]
            )
            return session

    def invalidate(self, user_id: str) -> bool:
        """Remove the cached session for *user_id*.

        Call this after a user connects or disconnects a tool so the next
        request creates a fresh session that includes the new tool set.

        Returns True if an entry was removed.
        """
        removed = self._cache.pop(user_id, None) is not None
        if removed:
            logger.info(
                "Invalidated MCP session cache for user %s", user_id[:8]
            )
        else:
            logger.info(
                "No MCP cache entry found for user %s (keys: %s)",
                user_id[:8],
                [k[:8] for k in self._cache.keys()],
            )
        return removed

    def invalidate_all(self) -> int:
        """Clear ALL cached sessions. Used as a fallback when entity_id matching fails."""
        count = len(self._cache)
        self._cache.clear()
        if count:
            logger.info("Invalidated ALL %d MCP sessions", count)
        return count

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_composio(self) -> "ComposioSDK":
        if self._composio is None:
            if not settings.composio_api_key:
                raise RuntimeError("COMPOSIO_API_KEY not set")
            self._composio = ComposioSDK(api_key=settings.composio_api_key)
        return self._composio

    @staticmethod
    def _create_session_sync(
        composio: "ComposioSDK",
        user_id: str,
    ) -> tuple[str, dict]:
        """Blocking call to create a Tool Router session.

        Fetches the user's connected accounts from Composio and passes
        them explicitly so the Tool Router sees ALL connected tools.
        This is the KaraX pattern — without this, the MCP session may
        not include tools connected via the marketplace.
        """
        import requests as http_requests

        # Fetch connected accounts for this user (KaraX pattern)
        connected_map: dict[str, str] = {}
        try:
            api_key = composio._api_key if hasattr(composio, '_api_key') else settings.composio_api_key
            url = "https://backend.composio.dev/api/v3/connected_accounts"
            headers = {"x-api-key": api_key}
            all_accounts: list[dict] = []
            cursor = None

            while True:
                params: dict = {"user_ids": [user_id], "limit": 100}
                if cursor:
                    params["cursor"] = cursor
                resp = http_requests.get(url, headers=headers, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                all_accounts.extend(data.get("items", []))
                cursor = data.get("next_cursor")
                if not cursor:
                    break

            for acc in all_accounts:
                status = str(acc.get("status", "")).upper()
                if status not in ("ACTIVE", "CONNECTED", "ENABLED"):
                    continue
                acc_id = acc.get("id")
                if not acc_id:
                    continue
                # Extract toolkit name
                toolkit_name = None
                for field in ("integrationId", "appName", "toolkit"):
                    val = acc.get(field)
                    if val:
                        toolkit_name = val.get("slug") if isinstance(val, dict) else str(val)
                        break
                if toolkit_name and toolkit_name not in connected_map:
                    connected_map[toolkit_name] = acc_id

            logger.info(
                "MCP session: found %d connected tools for %s: %s",
                len(connected_map),
                user_id[:8],
                list(connected_map.keys()),
            )
        except Exception as exc:
            logger.warning("Failed to fetch connected accounts for MCP session: %s", exc)

        session = composio.tool_router.create(
            user_id=user_id,
            connected_accounts=connected_map if connected_map else None,
            toolkits={"disable": ["MEM0"]},
        )
        return session.mcp.url, session.mcp.headers


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

mcp_tool_router = MCPToolRouter(
    ttl_seconds=settings.mcp_session_ttl_seconds,
)
