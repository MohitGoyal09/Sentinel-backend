"""
Sentinel Orchestrator -- main entry point for Ask Sentinel chat.

Routes every inbound message through:
  1. Intent classification (Gemini 2.5 Flash via ``IntentClassifier``)
  2. Agent selection (``org_agent`` / ``task_agent`` / ``general_agent``)
  3. SSE streaming from the selected agent

The orchestrator emits a ``classification`` SSE event before delegating to
the chosen agent, giving the frontend visibility into routing decisions.
"""

import logging
from typing import AsyncGenerator

from cachetools import TTLCache

from sqlalchemy.orm import Session

from app.models.identity import UserIdentity
from app.models.tenant import TenantMember
from app.services.intent_classifier import IntentClassifier
from app.services.agents._helpers import sse as _sse
from app.services.agents.general_agent import GeneralAgent
from app.services.agents.org_agent import OrgAgent
from app.services.agents.task_agent import TaskAgent

logger = logging.getLogger("sentinel.orchestrator")


class SentinelOrchestrator:
    """Routes messages to the appropriate agent based on intent classification.

    Lifecycle:
        1. Classify the user message via ``IntentClassifier``
        2. Emit a ``classification`` SSE event with agent name, confidence,
           and follow-up status
        3. Delegate to the selected agent's ``respond_stream``
        4. Yield all SSE events from the agent verbatim

    Falls back to ``general_agent`` when the classifier returns an unknown
    agent name.
    """

    def __init__(self) -> None:
        self.classifier = IntentClassifier()
        self._org_agent = OrgAgent()
        self._task_agent = TaskAgent()
        self._general_agent = GeneralAgent()
        self._agent_map: dict[str, OrgAgent | TaskAgent | GeneralAgent] = {
            "org_agent": self._org_agent,
            "task_agent": self._task_agent,
            "general_agent": self._general_agent,
        }
        # Track last agent per session for follow-up routing (bounded, auto-expires)
        self._last_agent: TTLCache = TTLCache(maxsize=10_000, ttl=3600)

    async def process_stream(
        self,
        message: str,
        user: UserIdentity,
        member: TenantMember,
        tenant_id: str,
        session_id: str,
        conversation_history: list[dict],
        db: Session,
    ) -> AsyncGenerator[str, None]:
        """Classify intent, select agent, and stream the response.

        Args:
            message:              The user's message text.
            user:                 The caller's ``UserIdentity`` record.
            member:               The caller's ``TenantMember`` record.
            tenant_id:            The caller's tenant ID (string).
            session_id:           Current chat session ID.
            conversation_history: Recent conversation turns for context.
            db:                   SQLAlchemy database session.

        Yields:
            SSE-formatted strings: a ``classification`` event followed by
            all events from the selected agent.
        """
        role = member.role if member else "employee"

        # Step 1: Classify intent
        classification = await self.classifier.classify(
            message=message,
            role=role,
            conversation_history=conversation_history,
        )

        logger.info(
            "Intent classified: agent=%s confidence=%.2f reasoning=%s",
            classification.agent,
            classification.confidence,
            classification.reasoning,
        )

        # Step 2: Emit classification event
        yield _sse(
            {
                "type": "classification",
                "agent": classification.agent,
                "confidence": classification.confidence,
                "is_followup": classification.is_followup,
            }
        )

        # Step 3: Select agent (with follow-up context preservation)
        resolved_agent_name = classification.agent

        if classification.is_followup and classification.agent == "general_agent" and session_id:
            previous = self._last_agent.get(session_id)
            if previous and previous != "general_agent":
                resolved_agent_name = previous
                logger.info(
                    "Follow-up detected — reusing previous agent '%s' instead of general_agent",
                    previous,
                )

        agent = self._agent_map.get(resolved_agent_name, self._general_agent)

        if resolved_agent_name not in self._agent_map:
            logger.warning(
                "Unknown agent '%s' from classifier, falling back to general_agent",
                resolved_agent_name,
            )

        # Step 4: Yield all SSE events from the selected agent
        async for event in agent.respond_stream(
            message=message,
            user=user,
            member=member,
            tenant_id=tenant_id,
            session_id=session_id,
            conversation_history=conversation_history,
            db=db,
        ):
            yield event

        # Track which agent handled this session for follow-up routing
        if session_id:
            self._last_agent[session_id] = resolved_agent_name


sentinel_orchestrator = SentinelOrchestrator()
