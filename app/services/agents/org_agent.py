"""
Org Agent -- handles ALL organisational data queries for Ask Sentinel.

Refactored core of ``sentinel_chat.py`` into the 3-agent architecture.
Pipeline: RefusalClassifier -> DataBoundaryEnforcer -> format context -> stream LLM.

Entity identifier: ``user.user_hash`` (analytics data is keyed by hash, not email).
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from sqlalchemy.orm import Session

from app.models.identity import UserIdentity
from app.models.tenant import TenantMember
from app.services.audit_service import AuditService, AuditAction
from app.services.data_boundary import BoundaryContext, DataBoundaryEnforcer
from app.services.llm import llm_service
from app.services.refusal_classifier import RefusalClassifier
from app.services.sentinel_chat import (
    ROLE_SYSTEM_PROMPTS,
    _MAX_HISTORY_TURNS,
    _SUGGESTION_INSTRUCTION,
)
from app.services.agents._helpers import sse as _sse, next_chunk as _next_chunk

logger = logging.getLogger("sentinel.agents.org")


# ---------------------------------------------------------------------------
# Manager Coaching Mode instructions (appended to the manager system prompt)
# ---------------------------------------------------------------------------

_MANAGER_COACHING_INSTRUCTIONS = """

## COACHING MODE

Your team members and their data are listed in the context below. When asked about a specific person, match their name (even partial/first name) to the team member data.

When a manager asks about how to approach a conversation with a team member, prepare a 1:1 agenda, or requests coaching advice for a specific person:

1. Look up that person's data from the team context provided
2. Generate a structured coaching response:

### Conversation Guide for [Employee Name]

**Risk Context:** [risk_level], attrition probability [X]%, velocity [Y]

**3 Conversation Openers** (empathetic, NOT mentioning monitoring/tracking):
1. [opener based on their indicators]
2. [opener based on their indicators]
3. [opener based on their indicators]

**Key Points to Address:**
- [based on their specific indicators and risk signals]

**Things to AVOID Saying:**
- Don't mention that you're tracking their work hours or patterns
- Don't use the word "burnout" directly -- ask about their experience instead
- Don't make promises you can't keep about workload changes

**Suggested Action Items:**
1. [specific, actionable step]
2. [specific, actionable step]
3. [specific, actionable step]

Important: Use warm, human language. Reference specific signals (like "I noticed your schedule has been unusual lately" rather than "your circadian entropy is 2.1"). Translate technical metrics into human observations. Never reveal the existence of monitoring systems or tracking tools."""


# ---------------------------------------------------------------------------
# Context conversion helpers
# ---------------------------------------------------------------------------


def _boundary_to_context(
    boundary_ctx: BoundaryContext,
    user_hash: str,
    tenant_id: str,
    role: str,
) -> dict:
    """Convert a ``BoundaryContext`` into a flat dict suitable for prompt building."""
    ud = boundary_ctx.user_data or {}
    context: dict = {
        "user_hash": user_hash,
        "tenant_id": tenant_id,
        "role": role,
        "risk_level": ud.get("risk_level", "LOW"),
        "velocity": ud.get("velocity", 0.0),
        "belongingness": ud.get("thwarted_belongingness", 0.5),
        "confidence": ud.get("confidence", 0.0),
        "betweenness": ud.get("betweenness", 0.0),
        "eigenvector": ud.get("eigenvector", 0.0),
        "unblocking_count": ud.get("unblocking_count", 0),
    }

    if boundary_ctx.team_aggregates:
        for k, v in boundary_ctx.team_aggregates.items():
            context[f"team_{k}"] = v

    if boundary_ctx.org_aggregates:
        for k, v in boundary_ctx.org_aggregates.items():
            context[f"org_{k}"] = v

    return context


def _format_context(context: dict, role: str) -> str:
    """Render role-appropriate context into a human-readable string for the system prompt."""
    if role == "employee":
        return (
            f"- Personal Risk Level: {context['risk_level']}\n"
            f"- Work Pattern Velocity: {context['velocity']:.2f} (higher = more variable schedule)\n"
            f"- Social Engagement: {context['belongingness']:.2f} (higher = more connected)\n"
            f"- Network Influence: {context.get('betweenness', 0.0):.2f} (how much you unblock others)"
        )

    if role == "manager":
        team_str = ""
        if "team_team_size" in context:
            team_str = (
                f"\n- Team Size: {context['team_team_size']}"
                f"\n- Team Members At Risk: {context.get('team_at_risk_count', 0)}"
            )
        elif "team_size" in context:
            team_str = (
                f"\n- Team Size: {context['team_size']}"
                f"\n- Team Members At Risk: {context.get('team_at_risk_count', 0)}"
                f"\n- Critical Cases: {context.get('team_critical_count', 0)}"
            )

        # Per-member coaching detail
        members = context.get("team_team_members", [])
        members_str = ""
        if members:
            members_str = "\n\n--- TEAM MEMBER DETAILS (sorted by risk) ---"
            for emp in members[:20]:
                indicators = emp.get("indicators", {})
                indicator_flags = []
                if indicators.get("chaotic_hours"):
                    indicator_flags.append("chaotic_hours")
                if indicators.get("social_withdrawal"):
                    indicator_flags.append("social_withdrawal")
                if indicators.get("sustained_intensity"):
                    indicator_flags.append("sustained_intensity")
                indicator_str = (
                    ", ".join(indicator_flags) if indicator_flags else "none"
                )
                members_str += (
                    f"\n  {emp['name']} | Role: {emp['role']} | "
                    f"Risk: {emp['risk_level']} | "
                    f"Velocity: {emp['velocity']} | "
                    f"Attrition: {emp['attrition_probability']}% | "
                    f"Belongingness: {emp['belongingness_score']} | "
                    f"Indicators: [{indicator_str}]"
                )
            if len(members) > 20:
                members_str += f"\n  ... and {len(members) - 20} more members"

        return (
            f"- Your Role: Manager\n"
            f"- Personal Metrics: Risk {context['risk_level']}, "
            f"Velocity {context['velocity']:.2f}{team_str}{members_str}"
        )

    if role == "admin":
        org_str = ""
        total = context.get("org_total_employees", 0)
        at_risk = context.get("org_at_risk_count", 0)
        critical = context.get("org_critical_count", 0)
        risk_pct = context.get("org_risk_percentage", 0)

        if total:
            org_str = (
                f"\n- Organization Size: {total} employees"
                f"\n- Total Teams: {context.get('org_total_teams', 0)}"
                f"\n- At Risk: {at_risk} ({risk_pct:.1f}%)"
                f"\n- Critical: {critical}"
            )

        # Include individual employee details for admin
        employees = context.get("org_employees", [])
        if employees:
            org_str += "\n\n--- EMPLOYEE DETAILS (sorted by risk) ---"
            for emp in employees[:20]:  # Cap at 20 to keep prompt reasonable
                org_str += (
                    f"\n  {emp['name']} | Role: {emp['role']} | "
                    f"Risk: {emp['risk_level']} | "
                    f"Velocity: {emp['velocity']} | "
                    f"Confidence: {emp['confidence']}"
                )
            if len(employees) > 20:
                org_str += f"\n  ... and {len(employees) - 20} more employees"

        return (
            f"- Your Role: Administrator (FULL ACCESS to individual data)\n"
            f"- Personal Risk Level: {context['risk_level']}{org_str}"
        )

    return f"- Risk Level: {context['risk_level']}\n- Role: {role}"


# ---------------------------------------------------------------------------
# OrgAgent
# ---------------------------------------------------------------------------


class OrgAgent:
    """Organisational data query agent.

    Executes the refusal -> boundary -> LLM pipeline for every inbound
    message that involves org/team/personal analytics data.

    Streams responses as SSE-formatted strings and always emits a terminal
    ``done`` event with ``agent: "org_agent"``.
    """

    # ------------------------------------------------------------------
    # Public API  (satisfies the Agent protocol)
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
        """Stream an org-data-backed LLM response as SSE events.

        Pipeline:
            1. RefusalClassifier -- block out-of-RBAC-scope queries
            2. DataBoundaryEnforcer -- build role-scoped context
            3. Format context into system prompt
            4. Stream LLM response

        Yields:
            ``refusal`` event if the query is refused.
            ``token``   events for each streamed chunk.
            ``error``   event on any exception (never raises).
            ``done``    event as the terminal event (always emitted).
        """
        role = member.role if member else "employee"
        context_used: dict = {}

        # ---- Step 1: Refusal check ------------------------------------
        refusal = self._classify_refusal(
            message=message,
            role=role,
            tenant_id=tenant_id,
            user_hash=user.user_hash,
            db=db,
        )
        if refusal is not None:
            self._log_refusal(
                db=db,
                user_hash=user.user_hash,
                role=role,
                tenant_id=tenant_id,
                message=message,
                refusal=refusal,
            )
            yield _sse(
                {
                    "type": "refusal",
                    "content": refusal.message,
                    "session_id": session_id,
                }
            )
            yield _sse(
                {
                    "type": "done",
                    "agent": "org_agent",
                    "role": role,
                    "session_id": session_id,
                    "context_used": {},
                    "generated_at": datetime.now(tz=timezone.utc).isoformat(),
                }
            )
            return

        # ---- Step 2: Build role-scoped context -------------------------
        boundary_ctx = self._enforce_data_boundary(
            user_hash=user.user_hash,
            role=role,
            tenant_id=tenant_id,
            member=member,
            db=db,
        )

        context = _boundary_to_context(
            boundary_ctx=boundary_ctx,
            user_hash=user.user_hash,
            tenant_id=tenant_id,
            role=role,
        )

        context_used = {
            "risk_level": context.get("risk_level"),
            "velocity": context.get("velocity"),
            "available_actions": (
                list(boundary_ctx.available_actions)
                if boundary_ctx.available_actions
                else []
            ),
        }

        # ---- Step 3: Format context into system prompt -----------------
        messages = self._build_messages(
            message=message,
            context=context,
            role=role,
            conversation_history=conversation_history,
        )

        # ---- Step 4: Stream LLM response -------------------------------
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
                "OrgAgent streaming error (session=%s): %s",
                session_id,
                exc,
                exc_info=True,
            )
            yield _sse(
                {
                    "type": "error",
                    "content": "An error occurred generating the response.",
                }
            )

        # ---- Always emit done -----------------------------------------
        yield _sse(
            {
                "type": "done",
                "agent": "org_agent",
                "role": role,
                "session_id": session_id,
                "context_used": context_used,
                "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            }
        )

    # ------------------------------------------------------------------
    # Private helpers -- pipeline steps
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_refusal(
        *,
        message: str,
        role: str,
        tenant_id: str,
        user_hash: str,
        db: Session,
    ):
        """Run ``RefusalClassifier`` and return result (``None`` = allowed)."""
        classifier = RefusalClassifier(db=db)
        return classifier.classify(
            message=message,
            role=role,
            user_hash=user_hash,
            tenant_id=tenant_id,
        )

    @staticmethod
    def _enforce_data_boundary(
        *,
        user_hash: str,
        role: str,
        tenant_id: str,
        member: Optional[TenantMember],
        db: Session,
    ) -> BoundaryContext:
        """Build a ``BoundaryContext`` scoped to the caller's role."""
        enforcer = DataBoundaryEnforcer(db=db)
        return enforcer.build_context(
            user_hash=user_hash,
            role=role,
            tenant_id=tenant_id,
            team_id=(
                str(member.team_id) if member and member.team_id else None
            ),
        )

    @staticmethod
    def _log_refusal(
        *,
        db: Session,
        user_hash: str,
        role: str,
        tenant_id: str,
        message: str,
        refusal,
    ) -> None:
        """Write an audit entry for a refused query."""
        AuditService(db).log(
            actor_hash=user_hash,
            actor_role=role,
            action=AuditAction.OUT_OF_SCOPE_QUERY,
            details={
                "query_snippet": message[:120],
                "reason_code": (
                    refusal.reason_code
                    if hasattr(refusal, "reason_code")
                    else "refusal"
                ),
            },
            tenant_id=tenant_id,
        )

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_messages(
        *,
        message: str,
        context: dict,
        role: str,
        conversation_history: list[dict],
    ) -> list[dict]:
        """Build the message list for the LLM.

        Prepends the role-appropriate system prompt with formatted context,
        appends the last ``_MAX_HISTORY_TURNS`` turns from history, then the
        current user message.
        """
        system_prompt = ROLE_SYSTEM_PROMPTS.get(
            role, ROLE_SYSTEM_PROMPTS["employee"]
        )
        context_str = _format_context(context, role)

        coaching_section = ""
        if role == "manager":
            coaching_section = _MANAGER_COACHING_INSTRUCTIONS

        full_system = (
            f"{system_prompt}{coaching_section}\n\nUSER CONTEXT:\n{context_str}"
            f"{_SUGGESTION_INSTRUCTION}"
        )

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


org_agent = OrgAgent()
