"""
Sentinel Chat Service

Orchestrates Ask Sentinel chat responses by:
  1. Running RefusalClassifier to block out-of-scope queries
  2. Running WorkflowIntentParser to detect actionable intents
  3. Building role-scoped context via DataBoundaryEnforcer (pre-LLM)
  4. Augmenting with external tool data (calendar, Slack) if needed
  5. Building a role-aware message list and calling the LLM
  6. Always emitting a terminal ``done`` event
"""

import json
import logging
from datetime import datetime
from typing import AsyncGenerator, Optional

from sqlalchemy.orm import Session

from app.models.identity import UserIdentity
from app.models.tenant import TenantMember
from app.schemas.ai import ChatRequest, ChatResponse, ChatContextUsed
from app.services.data_boundary import DataBoundaryEnforcer, BoundaryContext
from app.services.llm import llm_service
from app.services.audit_service import AuditService, AuditAction
from app.services.refusal_classifier import RefusalClassifier
from app.services.tool_augmented_llm import ToolAugmentedLLM
from app.services.workflow_intent import WorkflowIntentParser

logger = logging.getLogger("sentinel.chat")

ROLE_SYSTEM_PROMPTS = {
    "employee": """You are a supportive AI wellbeing companion for employees.

Your focus areas:
- Personal wellbeing and work-life balance
- Career growth and skill development
- Preparation for 1:1 conversations with managers
- Understanding personal work patterns and stress indicators
- Self-care recommendations and resources

Guidelines:
- Be encouraging and non-judgmental
- Focus on personal agency and control
- Provide actionable self-improvement suggestions
- Help interpret personal metrics in a positive light
- Suggest concrete steps for career development
- Never use surveillance or monitoring language
- Frame everything as self-discovery and growth

Tone: Supportive, empowering, personal growth focused""",
    "manager": """You are a management insights assistant focused on team health and performance.

Your focus areas:
- Team risk analysis and early warning indicators
- Individual team member support strategies
- Workload distribution and balance
- Team collaboration patterns and blockers
- 1:1 preparation and talking points
- Retention risk identification

Guidelines:
- Frame insights as opportunities for support, not criticism
- Respect privacy and consent boundaries
- Focus on actionable managerial interventions
- Balance team needs with individual care
- Provide context about when to escalate concerns
- Emphasize proactive leadership and team building
- Never frame data as surveillance

Tone: Professional, supportive, leadership focused""",
    "admin": """You are an organizational analytics assistant for HR and leadership.

Your focus areas:
- Organization-wide wellbeing trends
- Department-level risk aggregation
- Policy effectiveness and impact
- Resource allocation recommendations
- Compliance and audit insights
- Strategic workforce planning

Guidelines:
- Provide high-level strategic insights
- Focus on patterns across groups, not individuals
- Suggest policy and process improvements
- Identify systemic issues and opportunities
- Maintain strict privacy in all aggregations
- Support data-driven decision making
- Balance organizational needs with employee welfare

Tone: Strategic, analytical, organizational focus""",
}

_SUGGESTION_INSTRUCTION = (
    "\n\nIMPORTANT: At the very end of your response, on a new line, include exactly 3 brief "
    "follow-up questions the user might want to ask next. Format them as:\n"
    "<suggestions>\n- First suggestion\n- Second suggestion\n- Third suggestion\n</suggestions>\n"
    "Do NOT mention these suggestions in your main response. Keep each suggestion under 60 characters."
)

# Maximum conversation history turns to include in LLM context
_MAX_HISTORY_TURNS = 20


class SentinelChatService:
    """Orchestrates Ask Sentinel chat: refusal -> workflow -> boundary -> LLM.

    Public methods:
        respond(request, user, tenant_id, db) -> ChatResponse
        respond_stream(request, user, tenant_id, db) -> AsyncGenerator[str, None]
    """

    def __init__(self) -> None:
        self.workflow_parser = WorkflowIntentParser()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def respond(
        self,
        request: ChatRequest,
        user: UserIdentity,
        tenant_id: str,
        db: Session,
    ) -> ChatResponse:
        """Non-streaming response with refusal/workflow/boundary pipeline."""
        member = (
            db.query(TenantMember)
            .filter_by(user_hash=user.user_hash, tenant_id=tenant_id)
            .first()
        )
        role = member.role if member else "employee"

        conversation_id = (
            request.conversation_id
            or f"chat_{user.user_hash}_{datetime.utcnow().timestamp()}"
        )

        # 1. Refusal check
        refusal = self._classify_refusal(
            message=request.message,
            role=role,
            tenant_id=tenant_id,
            user_hash=user.user_hash,
            db=db,
        )
        if refusal is not None:
            AuditService(db).log(
                actor_hash=user.user_hash,
                actor_role=role,
                action=AuditAction.OUT_OF_SCOPE_QUERY,
                details={
                    "query_snippet": request.message[:120],
                    "reason_code": refusal.reason_code if hasattr(refusal, "reason_code") else "refusal",
                },
                tenant_id=tenant_id,
            )
            return ChatResponse(
                response=refusal.message,
                role=role,
                conversation_id=conversation_id,
                context_used=ChatContextUsed(risk_level=None),
                generated_at=datetime.utcnow().isoformat(),
            )

        # 2. Workflow intent check
        workflow = self.workflow_parser.parse(query=request.message, role=role)
        if workflow is not None:
            return ChatResponse(
                response=workflow.description,
                role=role,
                conversation_id=conversation_id,
                context_used=ChatContextUsed(risk_level=None),
                generated_at=datetime.utcnow().isoformat(),
            )

        # 3. Build scoped context via DataBoundaryEnforcer
        boundary_ctx = self._enforce_data_boundary(
            user_hash=user.user_hash,
            role=role,
            tenant_id=tenant_id,
            member=member,
            db=db,
        )

        context = self._boundary_to_context(
            boundary_ctx=boundary_ctx,
            user_hash=user.user_hash,
            tenant_id=tenant_id,
            role=role,
        )

        # 4. Optional tool augmentation
        if request.context:
            context.update(request.context)
        context = await ToolAugmentedLLM.augment_context_with_tools(
            request.message, context
        )

        # 5. Call LLM
        messages = self._build_messages(request, context, role)
        llm_response = llm_service.generate_chat_response(messages)

        return ChatResponse(
            response=llm_response,
            role=role,
            conversation_id=conversation_id,
            context_used=ChatContextUsed(
                risk_level=context.get("risk_level"),
                velocity=context.get("velocity"),
                belongingness=context.get("belongingness"),
                team_size=context.get("team_size"),
                org_total_users=context.get("org_total_users"),
            ),
            generated_at=datetime.utcnow().isoformat(),
        )

    async def respond_stream(
        self,
        request: ChatRequest,
        user: UserIdentity,
        tenant_id: str,
        db: Session,
    ) -> AsyncGenerator[str, None]:
        """Streaming response with typed SSE events.

        Event vocabulary:
          - ``token``    : LLM output chunk
          - ``refusal``  : query refused with redirect message
          - ``workflow`` : actionable workflow intent detected
          - ``error``    : LLM or internal error
          - ``done``     : terminal event (always emitted)
        """
        member = (
            db.query(TenantMember)
            .filter_by(user_hash=user.user_hash, tenant_id=tenant_id)
            .first()
        )
        role = member.role if member else "employee"

        conversation_id = (
            request.conversation_id
            or f"chat_{user.user_hash}_{datetime.utcnow().timestamp()}"
        )

        # 1. Refusal check
        refusal = self._classify_refusal(
            message=request.message,
            role=role,
            tenant_id=tenant_id,
            user_hash=user.user_hash,
            db=db,
        )
        if refusal is not None:
            AuditService(db).log(
                actor_hash=user.user_hash,
                actor_role=role,
                action=AuditAction.OUT_OF_SCOPE_QUERY,
                details={
                    "query_snippet": request.message[:120],
                    "reason_code": refusal.reason_code if hasattr(refusal, "reason_code") else "refusal",
                },
                tenant_id=tenant_id,
            )
            yield self._sse(
                {
                    "type": "refusal",
                    "content": refusal.message,
                    "conversation_id": conversation_id,
                }
            )
            yield self._sse(
                {"type": "done", "conversation_id": conversation_id}
            )
            return

        # 2. Workflow intent check
        workflow = self.workflow_parser.parse(query=request.message, role=role)
        if workflow is not None:
            yield self._sse(
                {
                    "type": "workflow",
                    "action": workflow.action,
                    "description": workflow.description,
                    "requires_confirmation": workflow.requires_confirmation,
                    "conversation_id": conversation_id,
                }
            )
            yield self._sse(
                {"type": "done", "conversation_id": conversation_id}
            )
            return

        # 3. Build scoped context via DataBoundaryEnforcer
        boundary_ctx = self._enforce_data_boundary(
            user_hash=user.user_hash,
            role=role,
            tenant_id=tenant_id,
            member=member,
            db=db,
        )

        context = self._boundary_to_context(
            boundary_ctx=boundary_ctx,
            user_hash=user.user_hash,
            tenant_id=tenant_id,
            role=role,
        )

        # 4. Optional tool augmentation
        if request.context:
            context.update(request.context)
        context = await ToolAugmentedLLM.augment_context_with_tools(
            request.message, context
        )

        # 5. Build messages and stream LLM response
        messages = self._build_messages(request, context, role)

        try:
            import asyncio

            def _next_chunk(it, sentinel):
                """Wrapper that converts StopIteration into sentinel (PEP 479 safe)."""
                try:
                    return next(it)
                except StopIteration:
                    return sentinel

            loop = asyncio.get_running_loop()
            stream = llm_service.generate_chat_response_stream(messages)
            _sentinel = object()
            while True:
                chunk = await loop.run_in_executor(
                    None, _next_chunk, stream, _sentinel
                )
                if chunk is _sentinel:
                    break
                yield self._sse({"type": "token", "content": chunk})
        except Exception as e:
            logger.error("LLM streaming error: %s", e, exc_info=True)
            yield self._sse(
                {
                    "type": "error",
                    "content": "An error occurred generating the response.",
                }
            )

        # 6. Always emit done
        metadata = {
            "type": "done",
            "role": role,
            "conversation_id": conversation_id,
            "context_used": {
                "risk_level": context.get("risk_level"),
                "velocity": context.get("velocity"),
                "available_actions": (
                    list(boundary_ctx.available_actions)
                    if boundary_ctx.available_actions
                    else []
                ),
            },
            "generated_at": datetime.utcnow().isoformat(),
        }
        yield self._sse(metadata)

    # ------------------------------------------------------------------
    # Private helpers — pipeline steps
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
        """Run ``RefusalClassifier`` and return result (None = allowed)."""
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
    def _boundary_to_context(
        *,
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

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _format_context(self, context: dict, role: str) -> str:
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
            return (
                f"- Your Role: Manager\n"
                f"- Personal Metrics: Risk {context['risk_level']}, "
                f"Velocity {context['velocity']:.2f}{team_str}"
            )
        if role == "admin":
            org_str = ""
            if "org_total_employees" in context:
                org_str = (
                    f"\n- Organization Size: {context['org_total_employees']}"
                    f"\n- Users At Risk: {context.get('org_at_risk_count', 0)}"
                )
            elif "org_total_users" in context:
                org_str = (
                    f"\n- Organization Size: {context['org_total_users']}"
                    f"\n- Users At Risk: {context.get('org_at_risk_count', 0)} "
                    f"({context.get('org_risk_percentage', 0):.1f}%)"
                    f"\n- Critical Cases: {context.get('org_critical_count', 0)}"
                )
            return (
                f"- Your Role: Administrator\n"
                f"- Personal Risk Level: {context['risk_level']}{org_str}"
            )

        return f"- Risk Level: {context['risk_level']}\n- Role: {role}"

    def _build_messages(
        self, request: ChatRequest, context: dict, role: str
    ) -> list:
        system_prompt = ROLE_SYSTEM_PROMPTS.get(
            role, ROLE_SYSTEM_PROMPTS["employee"]
        )
        context_str = self._format_context(context, role)
        tool_str = ToolAugmentedLLM.format_tool_data_for_llm(context)
        full_system = (
            f"{system_prompt}\n\nUSER CONTEXT:\n{context_str}"
            f"{tool_str}{_SUGGESTION_INSTRUCTION}"
        )

        messages: list[dict] = [{"role": "system", "content": full_system}]

        # Include conversation history (last N turns, exclude card messages)
        history = (request.context or {}).get("conversation_history", [])
        if isinstance(history, list):
            for msg in history[-_MAX_HISTORY_TURNS:]:
                if isinstance(msg, dict) and "role" in msg and "content" in msg:
                    messages.append(
                        {"role": msg["role"], "content": msg["content"]}
                    )

        messages.append({"role": "user", "content": request.message})
        return messages

    # ------------------------------------------------------------------
    # SSE helper
    # ------------------------------------------------------------------

    @staticmethod
    def _sse(payload: dict) -> str:
        """Format a dict as an SSE data line."""
        return f"data: {json.dumps(payload)}\n\n"


sentinel_chat_service = SentinelChatService()
