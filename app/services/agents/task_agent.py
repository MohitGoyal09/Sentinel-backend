"""
Task Agent -- autonomous tool execution via Composio MCP Tool Router.

Uses the Composio Tool Router to give the LLM (Google Gemini) direct
function-calling access to the user's connected tools.  The LLM discovers
available tools automatically, decides which to call, and executes them
via the MCP session -- no manual tool detection or regex matching needed.

Flow:
  1. Obtain an MCP session for the user (cached, TTL-managed)
  2. Connect to the MCP server via ``streamablehttp_client``
  3. List available tools and pass them to Gemini as function declarations
  4. Gemini autonomously calls tools -> results feed back -> Gemini responds
  5. Stream text and tool-call events as SSE to the frontend

Falls back gracefully to an LLM-only response when MCP / Composio / Gemini
are not configured.
"""

import asyncio
import json
import logging
import re
import warnings
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.security import privacy
from app.models.identity import UserIdentity
from app.models.tenant import TenantMember
from app.services.agents._helpers import sse as _sse, next_chunk as _next_chunk
from app.services.sentinel_chat import _MAX_HISTORY_TURNS

logger = logging.getLogger("sentinel.agents.task")

settings = get_settings()

# Suppress known GenAI SDK warning when using MCP tools
warnings.filterwarnings(
    "ignore",
    message=".*is not a valid FinishReason.*",
    category=UserWarning,
)

# ---------------------------------------------------------------------------
# Graceful imports -- server must start even without optional packages
# ---------------------------------------------------------------------------

_mcp_available = False
_genai_available = False

try:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    _mcp_available = True
except ImportError:
    logger.warning("mcp package not installed -- MCP tool execution disabled")

try:
    from google import genai
    from google.genai import types
    _genai_available = True
except ImportError:
    logger.warning(
        "google-genai package not installed -- Gemini function calling disabled"
    )

# Late import to avoid circular dependency (uses composio SDK)
from app.services.mcp_tool_router import mcp_tool_router  # noqa: E402

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

TASK_AGENT_SYSTEM_PROMPT = """\
You are **Sentinel's Task Agent**, an intelligent AI assistant that reliably \
executes user tasks using connected tools (Gmail, Google Calendar, Slack, \
GitHub, and more).

CRITICAL: YOU MUST CALL TOOLS TO EXECUTE TASKS.  Text-only responses for \
action requests are failures.

## Core Rules

1. **NO FOLLOW-UP QUESTIONS** -- unless the task is impossible without them.
2. **IMMEDIATE ACTION** -- do not ask for permission, just call the tool.
3. **ZERO CHATTER** -- execute the tool silently; show results concisely.
4. **ASSUME APPROVAL** -- if the user asks for X, do X now.
5. **ACTUAL EXECUTION ONLY** -- never pretend to complete tasks; you must \
   actually call the tool and show the real result.

## Safety: Confirmation Required

Before executing CRITICAL actions, STOP and ask for confirmation:
- Sending emails or messages to others
- Deleting any data (files, emails, events, contacts)
- Making payments or financial transactions
- Sharing files or granting permissions
- Posting to social media or public platforms
- Any irreversible action

## Formatting

- Use **Markdown** for structured output (headers, bullets, tables).
- When presenting email data, number each email with subject, sender, date, \
  and a short preview.
- When presenting calendar data, use bullets with event name, time, and \
  attendees.
- At the end of your response include exactly 3 brief follow-up suggestions:

<suggestions>
- First suggestion
- Second suggestion
- Third suggestion
</suggestions>

Keep each suggestion under 60 characters.  Do NOT mention the suggestions \
in your main response.

## Connection Handling

When COMPOSIO_MANAGE_CONNECTIONS returns an OAuth URL:
- DO NOT include the connection URL in your text response
- DO NOT write "Connect [app]" or show clickable links
- Simply say: "I need you to authenticate with [App] to continue. \
Please use the connection button above."
- The connection card with the button is displayed automatically by the system
"""

# ---------------------------------------------------------------------------
# Fallback prompts (used when MCP is not available)
# ---------------------------------------------------------------------------

_FALLBACK_SYSTEM_PROMPT = """\
You are Sentinel's tool assistant.  The user asked about external tools but \
tool integrations are not currently available on this Sentinel instance.

Provide a brief, helpful response:
1. Let them know the integration is not available right now
2. Suggest they contact their administrator to enable Composio integrations
3. Keep it to 2-3 sentences maximum

IMPORTANT: At the very end of your response, on a new line, include exactly \
3 brief follow-up questions the user might want to ask next. Format them as:
<suggestions>
- First suggestion
- Second suggestion
- Third suggestion
</suggestions>
Do NOT mention these suggestions in your main response.  \
Keep each suggestion under 60 characters.\
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_tool_name(raw_name: str) -> str:
    """Turn ``COMPOSIO_GMAIL_SEND_EMAIL`` into ``Gmail - Send Email``."""
    name = raw_name
    if name.startswith("COMPOSIO_"):
        name = name[len("COMPOSIO_"):]
    parts = name.split("_")
    if len(parts) >= 2:
        app = parts[0].capitalize()
        action = " ".join(w.capitalize() for w in parts[1:])
        return f"{app} - {action}"
    return " ".join(w.capitalize() for w in parts)


def _extract_app_slug(tool_name: str) -> str:
    """Extract the app slug from a Composio tool name for logo URLs."""
    name = tool_name
    if name.startswith("COMPOSIO_"):
        name = name[len("COMPOSIO_"):]
    return name.split("_")[0].lower() if name else "composio"


# ---------------------------------------------------------------------------
# Meta-tool friendly name mapping
# ---------------------------------------------------------------------------

# These are Composio's internal orchestration tools. Instead of showing raw
# names like "Search - Tools" or "Multi - Execute Tool", we display
# human-friendly descriptions.
META_TOOL_FRIENDLY_NAMES: dict[str, str] = {
    "COMPOSIO_SEARCH_TOOLS": "Searching for available tools",
    "COMPOSIO_MULTI_EXECUTE_TOOL": "Executing tool actions",
    "COMPOSIO_MANAGE_CONNECTIONS": "Managing tool connections",
    "COMPOSIO_REMOTE_WORKBENCH": "Processing tool results",
    "COMPOSIO_CREATE_PLAN": "Planning execution steps",
    "COMPOSIO_EXECUTE_CODE": "Running custom code",
    # Aliases without prefix (some SDK versions strip it)
    "SEARCH_TOOLS": "Searching for available tools",
    "MULTI_EXECUTE_TOOL": "Executing tool actions",
    "MANAGE_CONNECTIONS": "Managing tool connections",
    "REMOTE_WORKBENCH": "Processing tool results",
    "CREATE_PLAN": "Planning execution steps",
    "EXECUTE_CODE": "Running custom code",
}

# Regex to match Composio OAuth markdown links in LLM text output
_COMPOSIO_LINK_RE = re.compile(
    r'\[([^\]]*[Cc]onnect[^\]]*)\]\((https?://[^)]*composio[^)]*)\)'
)

# Regex to detect when the LLM mentions needing authentication but no
# connection_link event was emitted (the "missing button" scenario).
# Two-phase approach: first check if auth is needed, then extract app name.
_AUTH_NEEDED_RE = re.compile(
    r'authenticate with \w+|connect \w+|connection button|'
    r'need.*?to.*?connect|not.*?connected|authorize.*?access',
    re.IGNORECASE,
)

# Separate regex to extract the app/tool name from auth-needed text.
# Tries the most specific patterns first (those that capture a name).
_AUTH_APP_NAME_RE = re.compile(
    r'(?:authenticate|connect|authorize)\s+(?:with\s+)?(\w+)',
    re.IGNORECASE,
)


def _get_display_name(raw_name: str) -> str:
    """Return a friendly display name for a tool, using meta-tool mapping."""
    friendly = META_TOOL_FRIENDLY_NAMES.get(raw_name)
    if friendly:
        return friendly
    return _format_tool_name(raw_name)


def _detect_connection_urls_in_text(text: str) -> list[dict]:
    """Detect Composio OAuth connection URLs embedded in LLM text output.

    Matches markdown links like ``[Connect gmail](https://...composio...)``.

    Returns a list of dicts with ``app_name`` and ``url`` keys.
    """
    results: list[dict] = []
    for match in _COMPOSIO_LINK_RE.finditer(text):
        label, url = match.group(1), match.group(2)
        app_name = re.sub(r'[Cc]onnect\s*', '', label).strip() or "Application"
        results.append({"app_name": app_name, "url": url})
    return results


def _strip_connection_urls_from_text(text: str) -> str:
    """Remove Composio OAuth markdown links from text."""
    return _COMPOSIO_LINK_RE.sub('', text).strip()


# ---------------------------------------------------------------------------
# TaskAgent
# ---------------------------------------------------------------------------


class TaskAgent:
    """Composio MCP Tool Router agent with Gemini function calling.

    Satisfies the ``Agent`` protocol defined in ``__init__.py``.

    When MCP + Gemini are available, the agent connects to the user's
    Tool Router MCP session and lets Gemini autonomously discover and
    call tools.  When they are not available, it falls back to the
    existing LLM service with a helpful message.
    """

    # ------------------------------------------------------------------
    # Public API (Agent protocol)
    # ------------------------------------------------------------------

    async def respond_stream(
        self,
        message: str,
        user: UserIdentity,
        member: TenantMember,
        tenant_id: str,
        session_id: str,
        conversation_history: list[dict],
        db: Session,
    ) -> AsyncGenerator[str, None]:
        """Stream tool execution results as SSE events.

        Yields:
            ``tool_call``       -- starting / complete / error status
            ``connection_link`` -- when a tool needs OAuth
            ``token``           -- LLM response chunks
            ``error``           -- runtime errors
            ``done``            -- terminal event (always emitted)
        """
        tool_used: Optional[str] = None

        try:
            can_use_mcp = (
                _mcp_available
                and _genai_available
                and mcp_tool_router.is_available()
                and bool(settings.gemini_api_key)
            )

            if can_use_mcp:
                async for event in self._stream_with_mcp(
                    message=message,
                    user=user,
                    session_id=session_id,
                    conversation_history=conversation_history,
                ):
                    # Track tool usage from events for the done payload
                    if '"type": "tool_call"' in event or '"type":"tool_call"' in event:
                        tool_used = "mcp_tool_router"
                    yield event
            else:
                # Graceful fallback -- stream LLM-only response
                async for event in self._stream_fallback(
                    message, conversation_history
                ):
                    yield event

        except Exception as exc:
            logger.error(
                "TaskAgent error (session=%s): %s",
                session_id,
                exc,
                exc_info=True,
            )
            yield _sse(
                {
                    "type": "error",
                    "content": "An unexpected error occurred while accessing the tool.",
                }
            )

        # Always emit done
        yield _sse(
            {
                "type": "done",
                "agent": "task_agent",
                "tool_used": tool_used,
                "session_id": session_id,
                "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            }
        )

    # ------------------------------------------------------------------
    # MCP + Gemini streaming
    # ------------------------------------------------------------------

    async def _stream_with_mcp(
        self,
        message: str,
        user: UserIdentity,
        session_id: str,
        conversation_history: list[dict],
    ) -> AsyncGenerator[str, None]:
        """Core streaming loop: MCP Tool Router + Gemini AFC.

        1. Get MCP session for the user
        2. Connect via streamablehttp_client
        3. Pass MCP session as a tool to Gemini with AFC enabled
        4. Stream text chunks + tool-call events
        5. Handle connection_link cards when tools need OAuth
        """
        entity_id = self._build_entity_id(user)

        # 1. Obtain MCP session credentials
        mcp_session_info = await mcp_tool_router.get_session(entity_id)

        # 2. Connect to MCP server
        try:
            async with asyncio.timeout(30):
                async with streamablehttp_client(
                    url=mcp_session_info.url,
                    headers=mcp_session_info.headers,
                ) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as mcp_session:
                        await mcp_session.initialize()
                        logger.debug("MCP session initialized for user %s", entity_id[:8])

                        # 3. Build Gemini contents from conversation history
                        contents = self._build_gemini_contents(
                            message, conversation_history
                        )

                        # 4. Build system instruction with personalization
                        system_instruction = self._build_system_instruction(user)

                        # 5. Call Gemini with MCP session as tool + AFC
                        client = genai.Client(
                            api_key=settings.gemini_api_key,
                            http_options={"api_version": "v1beta"},
                        )

                        model_name = settings.llm_model or "gemini-2.5-flash"

                        async with asyncio.timeout(300):  # 5 min for complex multi-tool queries
                            response_stream = await client.aio.models.generate_content_stream(
                                model=model_name,
                                contents=contents,
                                config=types.GenerateContentConfig(
                                    system_instruction=system_instruction,
                                    temperature=0.7,
                                    tools=[mcp_session],
                                    automatic_function_calling=types.AutomaticFunctionCallingConfig(
                                        disable=False,
                                        maximum_remote_calls=20,
                                    ),
                                ),
                            )

                            # 6. Stream the response -- text + AFC tool calls
                            processed_tool_calls: set[str] = set()
                            tool_args_store: dict[str, dict] = {}
                            connection_link_emitted = False
                            accumulated_text = ""

                            async for chunk in response_stream:
                                # --- AFC history (tool calls executed by the SDK) ---
                                async for event in self._process_afc_history(
                                    chunk,
                                    processed_tool_calls,
                                    tool_args_store,
                                ):
                                    yield event
                                    # Invalidate MCP cache when a connection_link
                                    # is detected so the next request picks up
                                    # newly connected tools
                                    if '"type": "connection_link"' in event:
                                        connection_link_emitted = True
                                        mcp_tool_router.invalidate(entity_id)

                                # --- Text chunks ---
                                if chunk.candidates:
                                    for candidate in chunk.candidates:
                                        if not (candidate.content and candidate.content.parts):
                                            continue
                                        for part in candidate.content.parts:
                                            if hasattr(part, "text") and part.text:
                                                text = part.text
                                                accumulated_text += text

                                                # Detect connection URLs embedded
                                                # in LLM text and emit as
                                                # connection_link events instead
                                                conn_urls = _detect_connection_urls_in_text(text)
                                                if conn_urls:
                                                    text = _strip_connection_urls_from_text(text)
                                                    for conn in conn_urls:
                                                        app = conn["app_name"]
                                                        slug = app.lower().replace(" ", "")
                                                        yield _sse(
                                                            {
                                                                "type": "connection_link",
                                                                "tool_name": app,
                                                                "tool_slug": slug,
                                                                "tool_logo": f"https://logos.composio.dev/api/{slug}",
                                                                "connection_url": conn["url"],
                                                                "message": f"Connect {app} to continue",
                                                            }
                                                        )
                                                    connection_link_emitted = True
                                                    # Invalidate MCP cache so reconnection picks up new tools
                                                    mcp_tool_router.invalidate(entity_id)

                                                # Emit remaining text (if any)
                                                if text:
                                                    yield _sse(
                                                        {
                                                            "type": "token",
                                                            "content": text,
                                                        }
                                                    )

                            # 7. Post-stream: if the LLM mentioned needing auth
                            # but no connection_link event was emitted, proactively
                            # fetch an OAuth URL and emit the card.
                            logger.info(
                                "Post-stream check: connection_link_emitted=%s, text_len=%d",
                                connection_link_emitted,
                                len(accumulated_text),
                            )
                            if not connection_link_emitted and accumulated_text:
                                auth_match = _AUTH_NEEDED_RE.search(accumulated_text)
                                if auth_match:
                                    # Extract app name using the dedicated
                                    # name-extraction regex so alternatives
                                    # like "not connected" don't shadow it.
                                    name_match = _AUTH_APP_NAME_RE.search(accumulated_text)
                                    app_name = name_match.group(1) if name_match else None
                                    if app_name:
                                        slug = app_name.lower()
                                        try:
                                            from app.integrations.composio_client import composio_client
                                            conn_result = await composio_client.initiate_connection(
                                                tool_slug=slug,
                                                entity_id=entity_id,
                                                callback_url=None,
                                            )
                                            if conn_result.get("success") and conn_result.get("redirect_url"):
                                                yield _sse(
                                                    {
                                                        "type": "connection_link",
                                                        "tool_name": app_name,
                                                        "tool_slug": slug,
                                                        "tool_logo": f"https://logos.composio.dev/api/{slug}",
                                                        "connection_url": conn_result["redirect_url"],
                                                        "message": f"Connect {app_name} to continue",
                                                    }
                                                )
                                                connection_link_emitted = True
                                                mcp_tool_router.invalidate(entity_id)
                                        except Exception as exc:
                                            logger.warning(
                                                "Post-stream connection initiation failed for %s: %s",
                                                slug,
                                                exc,
                                            )

        except asyncio.TimeoutError:
            logger.error("MCP/Gemini timeout for user %s", entity_id[:8])
            yield _sse(
                {
                    "type": "error",
                    "content": (
                        "The request timed out while executing tools. "
                        "Please try again."
                    ),
                }
            )
        except Exception as exc:
            error_msg = str(exc)
            logger.error(
                "MCP stream error for user %s: %s",
                entity_id[:8],
                error_msg,
                exc_info=True,
            )

            # If the error looks like a connection issue, invalidate cache
            if any(
                kw in error_msg.lower()
                for kw in ("connect", "refused", "reset", "timeout", "closed")
            ):
                mcp_tool_router.invalidate(entity_id)

            yield _sse(
                {
                    "type": "error",
                    "content": "An error occurred while accessing your connected tools.",
                }
            )

    # ------------------------------------------------------------------
    # AFC history processing
    # ------------------------------------------------------------------

    @staticmethod
    async def _process_afc_history(
        chunk: "types.GenerateContentResponse",
        processed_tool_calls: set[str],
        tool_args_store: dict[str, dict],
    ) -> AsyncGenerator[str, None]:
        """Extract tool_call SSE events from Gemini's AFC history.

        The ``automatic_function_calling_history`` attribute on each chunk
        contains ``Content`` objects with ``function_call`` and
        ``function_response`` parts.  We emit ``tool_call`` starting/complete
        events in order, deduplicating by tool name.
        """
        if not hasattr(chunk, "automatic_function_calling_history"):
            return

        afc_history = chunk.automatic_function_calling_history
        if not afc_history:
            return

        for content in afc_history:
            if not hasattr(content, "parts"):
                continue

            for part in content.parts:
                # --- Function call (starting) ---
                if hasattr(part, "function_call") and part.function_call:
                    fn = part.function_call
                    tool_id = f"call_{fn.name}"

                    if tool_id in processed_tool_calls:
                        continue
                    processed_tool_calls.add(tool_id)

                    # Extract args safely
                    raw_args = fn.args if hasattr(fn, "args") else {}
                    tool_args: dict = {}
                    try:
                        if isinstance(raw_args, dict):
                            tool_args = {k: str(v) for k, v in raw_args.items()}
                        else:
                            tool_args = {"args": str(raw_args)}
                    except Exception:
                        pass

                    tool_args_store[fn.name] = tool_args

                    display_name = _get_display_name(fn.name)
                    is_meta = fn.name in META_TOOL_FRIENDLY_NAMES
                    app_slug = "composio" if is_meta else _extract_app_slug(fn.name)

                    yield _sse(
                        {
                            "type": "tool_call",
                            "status": "starting",
                            "tool_name": display_name,
                            "raw_tool_name": fn.name,
                            "tool_slug": app_slug,
                            "description": display_name,
                            "tool_args": tool_args,
                        }
                    )
                    logger.info("Tool executing: %s", display_name)
                    await asyncio.sleep(0.3)

                # --- Function response (complete) ---
                elif hasattr(part, "function_response") and part.function_response:
                    fr = part.function_response
                    tool_id = f"response_{fr.name}"

                    if tool_id in processed_tool_calls:
                        continue
                    processed_tool_calls.add(tool_id)

                    display_name = _get_display_name(fr.name)
                    is_meta = fr.name in META_TOOL_FRIENDLY_NAMES
                    app_slug = "composio" if is_meta else _extract_app_slug(fr.name)
                    tool_args = tool_args_store.get(fr.name, {})

                    # Check for connection_link in the response
                    result_data = fr.response if hasattr(fr, "response") else {}
                    connection_event = _extract_connection_link(
                        fr.name, result_data
                    )
                    if connection_event:
                        yield _sse(connection_event)

                    yield _sse(
                        {
                            "type": "tool_call",
                            "status": "complete",
                            "tool_name": display_name,
                            "raw_tool_name": fr.name,
                            "tool_slug": app_slug,
                            "description": f"Completed: {display_name}",
                            "tool_args": tool_args,
                        }
                    )
                    logger.info("Tool completed: %s", display_name)
                    await asyncio.sleep(0.2)

    # ------------------------------------------------------------------
    # Fallback: LLM-only response
    # ------------------------------------------------------------------

    async def _stream_fallback(
        self,
        message: str,
        conversation_history: list[dict],
    ) -> AsyncGenerator[str, None]:
        """Stream an LLM-only response when MCP is not available.

        Uses the existing LLM service (Portkey/Groq/Gemini via OpenAI
        compatible endpoint).
        """
        from app.services.llm import llm_service

        messages = self._build_openai_messages(
            message, conversation_history, _FALLBACK_SYSTEM_PROMPT
        )

        try:
            loop = asyncio.get_running_loop()
            stream = llm_service.generate_chat_response_stream(messages)
            sentinel_obj = object()

            while True:
                chunk = await loop.run_in_executor(
                    None, _next_chunk, stream, sentinel_obj
                )
                if chunk is sentinel_obj:
                    break
                yield _sse({"type": "token", "content": chunk})

        except Exception as exc:
            logger.error("Fallback LLM streaming error: %s", exc, exc_info=True)
            yield _sse(
                {
                    "type": "error",
                    "content": "An error occurred while generating the response.",
                }
            )

    # ------------------------------------------------------------------
    # Message building
    # ------------------------------------------------------------------

    @staticmethod
    def _build_gemini_contents(
        message: str,
        conversation_history: list[dict],
    ) -> list:
        """Convert conversation history + current message to Gemini format."""
        contents = []

        recent = conversation_history[-_MAX_HISTORY_TURNS:]
        for msg in recent:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if not content or not isinstance(content, str):
                continue

            if role == "user":
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=content)],
                    )
                )
            elif role == "assistant":
                contents.append(
                    types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=content)],
                    )
                )

        # Current user message
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=message)],
            )
        )
        return contents

    @staticmethod
    def _build_openai_messages(
        message: str,
        conversation_history: list[dict],
        system_content: str,
    ) -> list[dict]:
        """Build OpenAI-compatible message list for the fallback LLM."""
        messages: list[dict] = [{"role": "system", "content": system_content}]

        recent = conversation_history[-_MAX_HISTORY_TURNS:]
        for entry in recent:
            if isinstance(entry, dict) and "role" in entry and "content" in entry:
                messages.append(
                    {"role": entry["role"], "content": entry["content"]}
                )

        messages.append({"role": "user", "content": message})
        return messages

    @staticmethod
    def _build_system_instruction(user: UserIdentity) -> str:
        """Build the Gemini system instruction with user personalization."""
        instruction = TASK_AGENT_SYSTEM_PROMPT

        # Add user context
        try:
            email = privacy.decrypt(user.email_encrypted)
            name = privacy.decrypt(user.name_encrypted)
            personalization = []
            if name:
                personalization.append(f"**Current User:** {name}")
            if email:
                personalization.append(f"**User Email:** {email}")
            if personalization:
                instruction += "\n\n" + "\n".join(personalization)
        except Exception:
            pass  # Proceed without personalization on decryption failure

        return instruction

    # ------------------------------------------------------------------
    # Entity ID
    # ------------------------------------------------------------------

    @staticmethod
    def _build_entity_id(user: UserIdentity) -> str:
        """Build Composio entity_id from the user's real email.

        Format: ``"{email}-{environment}"``
        e.g. ``"sarah@company.com-development"``
        """
        email = privacy.decrypt(user.email_encrypted)
        environment = settings.environment or "development"
        return f"{email}-{environment}"


# ---------------------------------------------------------------------------
# Connection link extraction
# ---------------------------------------------------------------------------


def _extract_connection_link(
    tool_name: str,
    result_data: object,
) -> Optional[dict]:
    """Extract a connection_link SSE payload from a MANAGE_CONNECTIONS result.

    When the Tool Router's ``COMPOSIO_MANAGE_CONNECTIONS`` tool returns a
    redirect URL, we emit a ``connection_link`` event so the frontend can
    render an OAuth button.

    Returns None if no connection URL was found.
    """
    if "MANAGE_CONNECTIONS" not in tool_name.upper():
        return None

    if not result_data:
        return None

    # Parse result_data into a dict
    data: Optional[dict] = None
    if isinstance(result_data, dict):
        data = result_data
    elif isinstance(result_data, str):
        try:
            data = json.loads(result_data)
        except (json.JSONDecodeError, TypeError):
            return None
    else:
        return None

    if data is None:
        return None

    # Try nested data.results (multiple toolkits)
    nested_results = (data.get("data") or {}).get("results")
    if isinstance(nested_results, dict):
        for toolkit_name, toolkit_data in nested_results.items():
            if not isinstance(toolkit_data, dict):
                continue
            redirect = (
                toolkit_data.get("redirect_url")
                or toolkit_data.get("redirectUrl")
            )
            if redirect and isinstance(redirect, str) and redirect.startswith("http"):
                app_name = toolkit_data.get("toolkit") or toolkit_name
                app_slug = app_name.lower().replace(" ", "")
                return {
                    "type": "connection_link",
                    "tool_name": app_name,
                    "tool_slug": app_slug,
                    "tool_logo": f"https://logos.composio.dev/api/{app_slug}",
                    "connection_url": redirect,
                    "message": f"Connect {app_name} to continue",
                }

    # Fallback: top-level fields
    connection_url = (
        data.get("url")
        or data.get("connection_url")
        or data.get("oauth_url")
        or data.get("redirect_url")
        or data.get("redirectUrl")
    )
    if connection_url and isinstance(connection_url, str) and connection_url.startswith("http"):
        app_name = (
            data.get("app")
            or data.get("app_name")
            or data.get("toolkit")
            or "Application"
        )
        app_slug = app_name.lower().replace(" ", "")
        return {
            "type": "connection_link",
            "tool_name": app_name,
            "tool_slug": app_slug,
            "tool_logo": f"https://logos.composio.dev/api/{app_slug}",
            "connection_url": connection_url,
            "message": f"Connect {app_name} to continue",
        }

    return None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

task_agent = TaskAgent()
