"""
Sentinel Chat Service

Orchestrates Ask Sentinel chat responses by:
  1. Gathering user context from DB (risk, identity, centrality)
  2. Augmenting with external tool data (calendar, Slack) if needed
  3. Building a role-aware message list and calling the LLM
"""

import json
import logging
from datetime import datetime
from typing import AsyncGenerator
from sqlalchemy.orm import Session

from app.models.analytics import RiskScore, CentralityScore
from app.models.identity import UserIdentity
from app.schemas.ai import ChatRequest, ChatResponse, ChatContextUsed
from app.services.llm import llm_service
from app.services.tool_augmented_llm import ToolAugmentedLLM

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


class SentinelChatService:
    """
    Orchestrates Ask Sentinel chat: context gathering -> tool augmentation -> LLM response.

    Public methods:
        respond(request, user, db) -> ChatResponse
        respond_stream(request, user, db) -> AsyncGenerator[str, None]  (yields SSE lines)
    """

    def _gather_context(self, user_hash: str, db: Session) -> dict:
        """Fetch user risk/identity/centrality from DB."""
        risk_score = db.query(RiskScore).filter_by(user_hash=user_hash).first()
        identity = db.query(UserIdentity).filter_by(user_hash=user_hash).first()
        centrality = db.query(CentralityScore).filter_by(user_hash=user_hash).first()

        context: dict = {
            "user_hash": user_hash,
            "risk_level": risk_score.risk_level if risk_score else "LOW",
            "velocity": risk_score.velocity if risk_score else 0.0,
            "belongingness": risk_score.thwarted_belongingness if risk_score else 0.5,
            "confidence": risk_score.confidence if risk_score else 0.0,
            "role": identity.role if identity else "employee",
            "betweenness": centrality.betweenness if centrality else 0.0,
            "eigenvector": centrality.eigenvector if centrality else 0.0,
            "unblocking_count": centrality.unblocking_count if centrality else 0,
        }

        if identity and identity.role == "manager":
            team_members = db.query(UserIdentity).filter_by(manager_hash=user_hash).all()
            if team_members:
                member_hashes = [m.user_hash for m in team_members]
                team_risks = (
                    db.query(RiskScore)
                    .filter(RiskScore.user_hash.in_(member_hashes))
                    .all()
                )
                context["team_size"] = len(team_members)
                context["team_at_risk_count"] = sum(
                    1 for r in team_risks if r.risk_level in ["ELEVATED", "CRITICAL"]
                )
                context["team_critical_count"] = sum(
                    1 for r in team_risks if r.risk_level == "CRITICAL"
                )

        if identity and identity.role == "admin":
            all_risks = db.query(RiskScore).all()
            total_users = len(all_risks)
            org_at_risk = sum(
                1 for r in all_risks if r.risk_level in ["ELEVATED", "CRITICAL"]
            )
            context["org_total_users"] = total_users
            context["org_at_risk_count"] = org_at_risk
            context["org_critical_count"] = sum(
                1 for r in all_risks if r.risk_level == "CRITICAL"
            )
            context["org_risk_percentage"] = (
                (org_at_risk / total_users * 100) if total_users > 0 else 0
            )

        return context

    def _format_context(self, context: dict, role: str) -> str:
        if role == "employee":
            return (
                f"- Personal Risk Level: {context['risk_level']}\n"
                f"- Work Pattern Velocity: {context['velocity']:.2f} (higher = more variable schedule)\n"
                f"- Social Engagement: {context['belongingness']:.2f} (higher = more connected)\n"
                f"- Network Influence: {context['betweenness']:.2f} (how much you unblock others)"
            )
        if role == "manager":
            team_str = ""
            if "team_size" in context:
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
            if "org_total_users" in context:
                org_str = (
                    f"\n- Organization Size: {context['org_total_users']}"
                    f"\n- Users At Risk: {context['org_at_risk_count']} "
                    f"({context.get('org_risk_percentage', 0):.1f}%)"
                    f"\n- Critical Cases: {context['org_critical_count']}"
                )
            return f"- Your Role: Administrator\n- Personal Risk Level: {context['risk_level']}{org_str}"

        return f"- Risk Level: {context['risk_level']}\n- Role: {role}"

    def _build_messages(self, request: ChatRequest, context: dict, role: str) -> list:
        system_prompt = ROLE_SYSTEM_PROMPTS.get(role, ROLE_SYSTEM_PROMPTS["employee"])
        context_str = self._format_context(context, role)
        tool_str = ToolAugmentedLLM.format_tool_data_for_llm(context)
        full_system = (
            f"{system_prompt}\n\nUSER CONTEXT:\n{context_str}{tool_str}{_SUGGESTION_INSTRUCTION}"
        )

        messages: list[dict] = [{"role": "system", "content": full_system}]

        history = (request.context or {}).get("conversation_history", [])
        if isinstance(history, list):
            for msg in history[-10:]:
                if isinstance(msg, dict) and "role" in msg and "content" in msg:
                    messages.append({"role": msg["role"], "content": msg["content"]})

        messages.append({"role": "user", "content": request.message})
        return messages

    async def respond(
        self, request: ChatRequest, user: UserIdentity, db: Session
    ) -> ChatResponse:
        role = user.role or "employee"

        context = self._gather_context(user.user_hash, db)
        if request.context:
            context.update(request.context)

        context = await ToolAugmentedLLM.augment_context_with_tools(request.message, context)

        messages = self._build_messages(request, context, role)
        llm_response = llm_service.generate_chat_response(messages)

        conversation_id = (
            request.conversation_id
            or f"chat_{user.user_hash}_{datetime.utcnow().timestamp()}"
        )

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
        self, request: ChatRequest, user: UserIdentity, db: Session
    ) -> AsyncGenerator[str, None]:
        role = user.role or "employee"

        context = self._gather_context(user.user_hash, db)
        if request.context:
            context.update(request.context)
        context = await ToolAugmentedLLM.augment_context_with_tools(request.message, context)

        messages = self._build_messages(request, context, role)
        conversation_id = (
            request.conversation_id
            or f"chat_{user.user_hash}_{datetime.utcnow().timestamp()}"
        )

        for chunk in llm_service.generate_chat_response_stream(messages):
            yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"

        metadata = {
            "type": "done",
            "role": role,
            "conversation_id": conversation_id,
            "context_used": {
                "risk_level": context.get("risk_level"),
                "velocity": context.get("velocity"),
                "belongingness": context.get("belongingness"),
                "team_size": context.get("team_size"),
                "org_total_users": context.get("org_total_users"),
            },
            "generated_at": datetime.utcnow().isoformat(),
        }
        yield f"data: {json.dumps(metadata)}\n\n"


sentinel_chat_service = SentinelChatService()
