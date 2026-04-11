"""
General Agent — pure conversational LLM with no data access and no tool access.

Handles greetings, knowledge questions, meta-questions about Sentinel, and any
message that does not require organisational data or external tool execution.

When the user asks about org data or tools the agent nudges them toward asking
more specifically so the intent classifier can route the follow-up correctly.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import AsyncGenerator

from sqlalchemy.orm import Session

from app.models.identity import UserIdentity
from app.models.tenant import TenantMember
from app.services.llm import llm_service
from app.services.agents._helpers import sse as _sse, next_chunk as _next_chunk
from app.services.sentinel_chat import _MAX_HISTORY_TURNS

logger = logging.getLogger("sentinel.agents.general")

_SYSTEM_PROMPT = """\
You are Sentinel, an AI-powered workplace wellbeing assistant built to support \
employees, managers, and HR teams.

Your purpose is to be a helpful, empathetic conversational partner. In this \
mode you focus on:
- Answering general knowledge questions clearly and concisely
- Discussing workplace wellbeing, mental health, and career growth topics
- Explaining what Sentinel is and how its three engines work:
    * Safety Valve — a confidential channel for employees to raise concerns
    * Talent Scout — identifies growth opportunities and retention signals
    * Culture Thermometer — measures team belonging, velocity, and engagement
- Greeting users warmly and handling casual conversation naturally

Organisational data and tool access:
- You do NOT have access to any organisational data (metrics, risk scores, \
  team analytics) in this conversation. If the user asks about specific team \
  data, burnout scores, safety valve alerts, or any analytics, let them know \
  Sentinel can help and encourage them to ask directly — for example \
  "Who on my team is at risk?" or "Show me the culture thermometer" — so \
  Sentinel can route the request to the right engine.
- You do NOT have access to external tools (email, calendar, Slack, GitHub). \
  If the user asks to check email, view their calendar, or interact with any \
  connected tool, encourage them to ask directly — for example \
  "Check my emails" or "What meetings do I have today?" — so Sentinel can \
  route the request to the appropriate tool agent.

Guidelines:
- Be warm, professional, and non-judgmental
- Keep responses concise unless depth is clearly needed
- Never fabricate organisational data or tool results
- Never claim capabilities you do not have in this conversation
- When redirecting to org data or tools, be natural — frame it as Sentinel \
  being able to help, not as a limitation
"""

_SUGGESTION_INSTRUCTION = (
    "\n\nIMPORTANT: At the very end of your response, on a new line, include exactly 3 brief "
    "follow-up questions the user might want to ask next. Format them as:\n"
    "<suggestions>\n- First suggestion\n- Second suggestion\n- Third suggestion\n</suggestions>\n"
    "Do NOT mention these suggestions in your main response. Keep each suggestion under 60 characters."
)


class GeneralAgent:
    """Pure conversational LLM agent.

    No organisational data access, no external tool execution.
    Streams responses as SSE-formatted strings and always emits a terminal
    ``done`` event with ``agent: "general_agent"``.
    """

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
        """Stream a conversational LLM response as SSE events.

        Yields:
            ``token`` events for each streamed chunk.
            ``error`` event on any exception (never raises).
            ``done``  event as the terminal event (always emitted).
        """
        messages = self._build_messages(message, conversation_history)

        try:
            loop = asyncio.get_running_loop()
            stream = llm_service.generate_chat_response_stream(messages)
            _sentinel = object()

            while True:
                chunk = await loop.run_in_executor(
                    None, _next_chunk, stream, _sentinel
                )
                if chunk is _sentinel:
                    break
                yield _sse({"type": "token", "content": chunk})

        except Exception as exc:
            logger.error(
                "GeneralAgent streaming error (session=%s): %s",
                session_id,
                exc,
                exc_info=True,
            )
            yield _sse(
                {
                    "type": "error",
                    "content": "An error occurred while generating the response.",
                }
            )

        yield _sse(
            {
                "type": "done",
                "agent": "general_agent",
                "session_id": session_id,
                "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            }
        )

    def _build_messages(
        self,
        message: str,
        conversation_history: list[dict],
    ) -> list[dict]:
        """Build the message list for the LLM.

        Prepends the system prompt, appends the last
        ``_MAX_HISTORY_TURNS`` turns from history, then the current message.
        """
        full_system = _SYSTEM_PROMPT + _SUGGESTION_INSTRUCTION

        messages: list[dict] = [{"role": "system", "content": full_system}]

        ALLOWED_ROLES = {"user", "assistant"}
        recent_history = conversation_history[-_MAX_HISTORY_TURNS:]
        for entry in recent_history:
            if isinstance(entry, dict) and "role" in entry and "content" in entry:
                role = entry.get("role", "")
                if role in ALLOWED_ROLES:
                    messages.append(
                        {"role": role, "content": entry["content"]}
                    )
                # Skip system/tool/other injected roles

        messages.append({"role": "user", "content": message})
        return messages


general_agent = GeneralAgent()
