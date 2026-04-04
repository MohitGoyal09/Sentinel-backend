"""
Intent Classifier -- LLM-based routing for the 3-agent orchestrator.

Routes every user message to one of three agents:
  - org_agent:     Organisational data queries (team health, burnout, metrics)
  - task_agent:    External tool requests (email, calendar, Slack, GitHub)
  - general_agent: Greetings, knowledge, chitchat, meta-questions
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from app.config import get_settings

logger = logging.getLogger("sentinel.intent_classifier")

_VALID_AGENTS: frozenset[str] = frozenset({"org_agent", "task_agent", "general_agent"})

_CLASSIFICATION_SYSTEM_PROMPT = """\
You are a message router for Sentinel, an AI-powered employee insight platform.
Your job is to classify every user message into exactly ONE of three agents.

## Agents

### org_agent
Routes to the organisational data agent. Use when the message asks about:
- Team health, burnout risk, wellbeing scores, safety valve alerts
- Talent insights, retention risk, flight risk, performance trends
- Culture thermometer data, belongingness, velocity, engagement
- Network analysis, collaboration patterns, team dynamics
- Any workforce analytics, metrics, dashboards, or engine data
- Questions scoped by role (e.g. "How is my team doing?", "Show risk scores")
- Comparisons, trends, or historical data about people/teams

Examples:
- "Who on my team is at risk of burnout?"
- "Show me the culture thermometer for Q3"
- "What's the team velocity trend?"
- "Are there any safety valve alerts?"
- "How is employee engagement this month?"
- "What does the talent scout say about retention?"

### task_agent
Routes to the external tool execution agent. Use when the message asks to:
- Check, read, send, or manage emails
- View, create, or modify calendar events and meetings
- Read or send Slack messages
- Check GitHub PRs, issues, commits, or repositories
- Connect, disconnect, or list tool integrations
- Any action requiring Composio tool execution

Examples:
- "Check my emails"
- "Send an email to the team about the meeting"
- "What meetings do I have tomorrow?"
- "Show my Slack messages"
- "List my connected tools"
- "Connect my Gmail"
- "Check my open PRs on GitHub"

### general_agent
Routes to the general conversation agent. Use when the message is:
- A greeting or farewell ("Hello", "Thanks", "Goodbye")
- A general knowledge question unrelated to org data or tools
- A question about Sentinel itself ("What can you do?", "How does Sentinel work?")
- A joke, chitchat, or casual conversation
- A meta-question about the conversation
- Anything that does NOT need organisational data or tool execution

Examples:
- "Hello"
- "What is Sentinel?"
- "Tell me a joke"
- "How does the safety valve engine work?"
- "Thanks for the help"
- "What can you help me with?"

## Instructions

1. Read the user message carefully.
2. Consider the conversation history for context (follow-up detection).
3. Choose the single best-matching agent.
4. If the message is a follow-up to a previous topic, route to the same agent \
as the original topic.
5. When in doubt, choose general_agent.

Respond with a JSON object containing:
- "agent": one of "org_agent", "task_agent", "general_agent"
- "confidence": float between 0.0 and 1.0
- "reasoning": brief explanation (one sentence)
- "is_followup": boolean indicating if this is a follow-up to a previous message
"""


@dataclass(frozen=True)
class ClassificationResult:
    """Immutable result of intent classification."""

    agent: str
    confidence: float
    reasoning: str
    is_followup: bool


class IntentClassifier:
    """LLM-based intent classifier using Gemini 2.5 Flash.

    Classifies user messages into one of three agents (org_agent,
    task_agent, general_agent) by calling the Gemini API through the
    OpenAI-compatible endpoint.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._client = OpenAI(
            api_key=settings.gemini_api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )

    async def classify(
        self,
        message: str,
        role: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> ClassificationResult:
        """Classify a user message into one of the three agents.

        Args:
            message:              The user's message text.
            role:                 The caller's role (employee, manager, admin).
            conversation_history: Recent conversation messages for follow-up
                                  detection. Only the last 6 messages (3 turns)
                                  are used.

        Returns:
            A frozen ``ClassificationResult`` with the chosen agent, confidence,
            reasoning, and follow-up flag. Falls back to ``general_agent`` on
            any error.
        """
        try:
            raw = await self._call_llm(message, role, conversation_history)
            return self._parse_response(raw)
        except Exception as exc:
            logger.error("Intent classification failed: %s", exc)
            return ClassificationResult(
                agent="general_agent",
                confidence=0.2,
                reasoning=f"Fallback due to classification error: {exc}",
                is_followup=False,
            )

    async def _call_llm(
        self,
        message: str,
        role: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Call Gemini 2.5 Flash for classification.

        The OpenAI client is synchronous, so the call is dispatched to a
        thread-pool executor to avoid blocking the event loop.
        """
        messages = self._build_messages(message, role, conversation_history)

        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, self._sync_call, messages)

        content = response.choices[0].message.content
        return json.loads(content)

    def _sync_call(self, messages: list[dict[str, str]]) -> Any:
        """Synchronous OpenAI chat completion call."""
        return self._client.chat.completions.create(
            model="gemini-2.5-flash",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=256,
        )

    def _build_messages(
        self,
        message: str,
        role: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        """Build the message list for the LLM call.

        Includes the system prompt, the last 6 messages of conversation
        history (3 turns), and the current user message.
        """
        messages: list[dict[str, str]] = [
            {"role": "system", "content": _CLASSIFICATION_SYSTEM_PROMPT},
        ]

        # Include last 6 messages (3 turns) for follow-up detection
        if conversation_history:
            recent = conversation_history[-6:]
            for msg in recent:
                messages.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", ""),
                })

        # Current message with role context
        user_content = (
            f"[User role: {role}]\n"
            f"Message: {message}"
        )
        messages.append({"role": "user", "content": user_content})

        return messages

    def _parse_response(self, raw: dict[str, Any]) -> ClassificationResult:
        """Parse and validate the LLM JSON response.

        Maps any invalid agent name to ``general_agent`` with reduced
        confidence.
        """
        agent = raw.get("agent", "general_agent")
        confidence = float(raw.get("confidence", 0.5))
        reasoning = str(raw.get("reasoning", "No reasoning provided"))
        is_followup = bool(raw.get("is_followup", False))

        # Validate agent name
        if agent not in _VALID_AGENTS:
            logger.warning(
                "LLM returned invalid agent '%s', mapping to general_agent",
                agent,
            )
            agent = "general_agent"
            confidence = min(confidence, 0.3)
            reasoning = f"Mapped from invalid agent: {reasoning}"

        # Clamp confidence
        confidence = max(0.0, min(1.0, confidence))

        return ClassificationResult(
            agent=agent,
            confidence=confidence,
            reasoning=reasoning,
            is_followup=is_followup,
        )
