import json
import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import AsyncGenerator, Optional, List, Dict, Any

from app.core.database import get_db
from app.models.analytics import RiskScore, Event, RiskHistory, CentralityScore
from app.models.identity import UserIdentity
from app.models.tenant import TenantMember
from app.api.deps.auth import get_current_user_identity, get_tenant_member, require_role
from app.services.llm import llm_service
from app.services.sentinel_chat import sentinel_chat_service
from app.services.chat_history_service import ChatHistoryService
from app.models.chat_history import ChatSession
from app.services.permission_service import PermissionService
from app.schemas.ai import (
    ChatRequest,
    ChatResponse,
    ChatContextUsed,
    AgendaRequest,
    AgendaResponse,
    TalkingPoint,
    SuggestedAction,
    QueryRequest,
    QueryResult,
    QueryResponse,
    NarrativeReportResponse,
    TeamReportResponse,
)

logger = logging.getLogger("sentinel.api.ai")

router = APIRouter()


# Alias for /narratives/team/{team_hash} -> /report/team/{team_hash}
@router.get("/narratives/team/{team_hash}")
async def get_team_narrative_alias(
    team_hash: str,
    days: int = 30,
    member: TenantMember = Depends(require_role("manager", "admin")),
    db: Session = Depends(get_db),
):
    """
    Generate team health narrative (alias endpoint).
    Provides aggregated team insights with privacy protection.
    """
    return await generate_team_narrative_report(
        team_hash=team_hash, days=days, member=member, db=db
    )


def get_user_risk_context(db: Session, user_hash: str) -> dict:
    """Fetch risk data for a user from Safety Valve"""
    risk_score = db.query(RiskScore).filter_by(user_hash=user_hash).first()

    if not risk_score:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No risk data found for user {user_hash}",
        )

    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    recent_events = (
        db.query(Event)
        .filter(Event.user_hash == user_hash, Event.timestamp >= thirty_days_ago)
        .order_by(Event.timestamp.desc())
        .limit(100)
        .all()
    )

    risk_history = (
        db.query(RiskHistory)
        .filter(
            RiskHistory.user_hash == user_hash, RiskHistory.timestamp >= thirty_days_ago
        )
        .order_by(RiskHistory.timestamp.asc())
        .all()
    )

    velocity = risk_score.velocity or 0.0
    belongingness = risk_score.thwarted_belongingness or 0.5

    if velocity > 2.5:
        pattern_summary = "Erratic schedule with late nights"
    elif velocity > 1.5:
        pattern_summary = "Increasing hours, less recovery time"
    elif velocity > 0.5:
        pattern_summary = "Slightly elevated activity"
    else:
        pattern_summary = "Stable work patterns"

    late_night_count = (
        sum(
            1
            for e in recent_events
            if e.event_type == "commit" and e.metadata_.get("after_hours", False)
        )
        if recent_events
        else 0
    )

    if late_night_count > 3:
        pattern_summary += f", {late_night_count} late nights this month"

    return {
        "risk_level": risk_score.risk_level or "LOW",
        "velocity": velocity,
        "belongingness": belongingness,
        "confidence": risk_score.confidence or 0.0,
        "pattern_summary": pattern_summary,
    }


def parse_query_intent(query: str) -> dict:
    """
    Parse natural language query to determine intent and filters.
    Returns dict with query_type, filters, and sort order.
    """
    query_lower = query.lower()

    if (
        "at risk" in query_lower
        or "risk" in query_lower
        and ("who" in query_lower or "which" in query_lower)
    ):
        return {
            "query_type": "at_risk",
            "filters": {"risk_level": ["ELEVATED", "CRITICAL"]},
            "sort_by": "velocity",
            "sort_desc": True,
        }

    if "burned out" in query_lower or "burnout" in query_lower:
        if (
            "isn't burned" in query_lower
            or "not burned" in query_lower
            or "isn't" in query_lower
        ):
            return {
                "query_type": "not_burned_with_skill",
                "filters": {"risk_level": ["LOW", "ELEVATED"]},
                "sort_by": "betweenness",
                "sort_desc": True,
            }
        return {
            "query_type": "burned_out",
            "filters": {"risk_level": ["CRITICAL", "ELEVATED"]},
            "sort_by": "velocity",
            "sort_desc": True,
        }

    if "hidden gem" in query_lower or "high impact" in query_lower:
        return {
            "query_type": "hidden_gems",
            "filters": {"min_betweenness": 0.3, "min_unblocking": 5},
            "sort_by": "betweenness",
            "sort_desc": True,
        }

    if "might leave" in query_lower or "flight risk" in query_lower:
        return {
            "query_type": "flight_risk",
            "filters": {"risk_level": ["ELEVATED", "CRITICAL"]},
            "sort_by": "velocity",
            "sort_desc": True,
        }

    if "postgresql" in query_lower or "python" in query_lower or "skill" in query_lower:
        if "isn't burned" in query_lower or "not burned" in query_lower:
            return {
                "query_type": "skilled_not_burned",
                "filters": {"risk_level": ["LOW", "ELEVATED"]},
                "sort_by": "betweenness",
                "sort_desc": True,
            }
        return {
            "query_type": "skilled_people",
            "filters": {},
            "sort_by": "betweenness",
            "sort_desc": True,
        }

    return {
        "query_type": "general",
        "filters": {},
        "sort_by": "risk_level",
        "sort_desc": True,
    }


def apply_role_filter(db: Session, user_role: str, results: List[dict]) -> List[dict]:
    """
    Apply privacy filters based on user role.
    - employees: see only own data
    - managers: see team members with consent
    - admins: see all data
    """
    if user_role == "admin":
        return results

    if user_role == "manager":
        filtered = []
        for r in results:
            if r.get("consent_share_with_manager", False):
                r_filtered = {
                    k: v
                    for k, v in r.items()
                    if k not in ["email", "slack_id", "team_id"]
                }
                filtered.append(r_filtered)
            elif r.get("risk_level") == "CRITICAL":
                r_filtered = {
                    k: v
                    for k, v in r.items()
                    if k not in ["email", "slack_id", "team_id"]
                }
                filtered.append(r_filtered)
        return filtered

    return []


def execute_semantic_query(
    db: Session, intent: dict, user_role: str, current_user_hash: str,
    tenant_id: str = None,
) -> List[dict]:
    """
    Execute query based on parsed intent.
    Returns list of user data matching filters.
    Scoped to the caller's tenant when tenant_id is provided.
    """
    # Build set of tenant-scoped user hashes for filtering
    tenant_hashes: set | None = None
    if tenant_id:
        tenant_hashes = {
            tm.user_hash
            for tm in db.query(TenantMember.user_hash).filter_by(
                tenant_id=tenant_id
            ).all()
        }

    results = []

    if intent["query_type"] == "at_risk":
        risk_levels = intent["filters"].get("risk_level", ["ELEVATED", "CRITICAL"])
        query = db.query(RiskScore).filter(RiskScore.risk_level.in_(risk_levels))
        if tenant_hashes is not None:
            query = query.filter(RiskScore.user_hash.in_(tenant_hashes))
        users = query.all()

        for u in users:
            identity = db.query(UserIdentity).filter_by(user_hash=u.user_hash).first()
            centrality = (
                db.query(CentralityScore).filter_by(user_hash=u.user_hash).first()
            )
            tm = db.query(TenantMember).filter_by(user_hash=u.user_hash).first()

            results.append(
                {
                    "user_hash": u.user_hash,
                    "risk_level": u.risk_level,
                    "velocity": u.velocity,
                    "betweenness": centrality.betweenness if centrality else None,
                    "eigenvector": centrality.eigenvector if centrality else None,
                    "consent_share_with_manager": identity.consent_share_with_manager
                    if identity
                    else False,
                    "team_id": str(tm.team_id) if tm and tm.team_id else None,
                }
            )

    elif intent["query_type"] == "not_burned_with_skill":
        not_burned_query = db.query(RiskScore).filter(
            RiskScore.risk_level.in_(["LOW", "ELEVATED"])
        )
        if tenant_hashes is not None:
            not_burned_query = not_burned_query.filter(
                RiskScore.user_hash.in_(tenant_hashes)
            )
        users = not_burned_query.all()

        centrality_query = db.query(CentralityScore).filter(
            CentralityScore.betweenness > 0.2
        )
        if tenant_hashes is not None:
            centrality_query = centrality_query.filter(
                CentralityScore.user_hash.in_(tenant_hashes)
            )
        centrality_users = centrality_query.all()

        centrality_hashes = {c.user_hash for c in centrality_users}

        for u in users:
            if u.user_hash in centrality_hashes:
                identity = (
                    db.query(UserIdentity).filter_by(user_hash=u.user_hash).first()
                )
                centrality = (
                    db.query(CentralityScore).filter_by(user_hash=u.user_hash).first()
                )
                tm = db.query(TenantMember).filter_by(user_hash=u.user_hash).first()

                results.append(
                    {
                        "user_hash": u.user_hash,
                        "risk_level": u.risk_level,
                        "velocity": u.velocity,
                        "betweenness": centrality.betweenness if centrality else None,
                        "eigenvector": centrality.eigenvector if centrality else None,
                        "unblocking_count": centrality.unblocking_count
                        if centrality
                        else 0,
                        "consent_share_with_manager": identity.consent_share_with_manager
                        if identity
                        else False,
                        "team_id": str(tm.team_id) if tm and tm.team_id else None,
                    }
                )

    elif intent["query_type"] == "hidden_gems":
        min_betweenness = intent["filters"].get("min_betweenness", 0.3)
        min_unblocking = intent["filters"].get("min_unblocking", 5)

        gems_query = db.query(CentralityScore).filter(
            CentralityScore.betweenness >= min_betweenness,
            CentralityScore.unblocking_count >= min_unblocking,
        )
        if tenant_hashes is not None:
            gems_query = gems_query.filter(
                CentralityScore.user_hash.in_(tenant_hashes)
            )
        users = gems_query.all()

        for c in users:
            risk = db.query(RiskScore).filter_by(user_hash=c.user_hash).first()
            identity = db.query(UserIdentity).filter_by(user_hash=c.user_hash).first()
            tm = db.query(TenantMember).filter_by(user_hash=c.user_hash).first()

            results.append(
                {
                    "user_hash": c.user_hash,
                    "risk_level": risk.risk_level if risk else None,
                    "velocity": risk.velocity if risk else None,
                    "betweenness": c.betweenness,
                    "eigenvector": c.eigenvector,
                    "unblocking_count": c.unblocking_count,
                    "consent_share_with_manager": identity.consent_share_with_manager
                    if identity
                    else False,
                    "team_id": str(tm.team_id) if tm and tm.team_id else None,
                }
            )

    elif intent["query_type"] == "flight_risk":
        flight_query = db.query(RiskScore).filter(
            RiskScore.risk_level.in_(["ELEVATED", "CRITICAL"])
        )
        if tenant_hashes is not None:
            flight_query = flight_query.filter(
                RiskScore.user_hash.in_(tenant_hashes)
            )
        users = flight_query.all()

        for u in users:
            identity = db.query(UserIdentity).filter_by(user_hash=u.user_hash).first()
            centrality = (
                db.query(CentralityScore).filter_by(user_hash=u.user_hash).first()
            )
            tm = db.query(TenantMember).filter_by(user_hash=u.user_hash).first()

            results.append(
                {
                    "user_hash": u.user_hash,
                    "risk_level": u.risk_level,
                    "velocity": u.velocity,
                    "betweenness": centrality.betweenness if centrality else None,
                    "eigenvector": centrality.eigenvector if centrality else None,
                    "unblocking_count": centrality.unblocking_count
                    if centrality
                    else 0,
                    "consent_share_with_manager": identity.consent_share_with_manager
                    if identity
                    else False,
                    "team_id": str(tm.team_id) if tm and tm.team_id else None,
                }
            )

    else:
        general_query = db.query(RiskScore)
        if tenant_hashes is not None:
            general_query = general_query.filter(
                RiskScore.user_hash.in_(tenant_hashes)
            )
        users = general_query.all()

        for u in users:
            identity = db.query(UserIdentity).filter_by(user_hash=u.user_hash).first()
            centrality = (
                db.query(CentralityScore).filter_by(user_hash=u.user_hash).first()
            )
            tm = db.query(TenantMember).filter_by(user_hash=u.user_hash).first()

            results.append(
                {
                    "user_hash": u.user_hash,
                    "risk_level": u.risk_level,
                    "velocity": u.velocity,
                    "betweenness": centrality.betweenness if centrality else None,
                    "eigenvector": centrality.eigenvector if centrality else None,
                    "consent_share_with_manager": identity.consent_share_with_manager
                    if identity
                    else False,
                    "team_id": str(tm.team_id) if tm and tm.team_id else None,
                }
            )

    return apply_role_filter(db, user_role, results)


def build_query_response_prompt(
    query: str, results: List[dict], query_type: str
) -> str:
    """Build prompt for LLM to generate natural response"""

    if not results:
        return f"""You are a helpful AI assistant. The user asked: "{query}"

No matching results were found. Provide a brief, helpful response explaining this.
Keep it concise and friendly."""

    result_summary = []
    for r in results[:5]:
        summary = {
            "user": r.get("user_hash", "Unknown")[:8] + "...",
            "risk": r.get("risk_level", "Unknown"),
            "betweenness": round(r.get("betweenness", 0), 2)
            if r.get("betweenness")
            else "N/A",
            "velocity": round(r.get("velocity", 0), 2) if r.get("velocity") else "N/A",
        }
        result_summary.append(summary)

    return f"""You are a helpful AI assistant answering a manager's query about team members.

User Query: "{query}"
Query Type: {query_type}

Results:
{result_summary}

Generate a natural language response that:
1. Summarizes the findings
2. Mentions key metrics (betweenness, risk level, velocity)
3. Is concise but informative
4. Focuses on actionable insights
5. Maintains privacy (don't mention specific user hashes)

Format your response as 1-2 sentences followed by a bullet list of key findings.
"""


def build_copilot_prompt(risk_data: dict) -> str:
    """Build the prompt for the LLM"""
    return f"""You are a supportive manager copilot. Generate a brief, caring 1:1 agenda.

Risk Data:
- Risk Level: {risk_data["risk_level"]}
- Velocity: {risk_data["velocity"]} (higher = more erratic hours)
- Belongingness: {risk_data["belongingness"]} (lower = less social interaction)
- Recent Pattern: {risk_data["pattern_summary"]}

Generate 3 talking points that are:
- Brief (1 sentence each)
- Protective (focus on support, not problems)
- Actionable (include specific suggestions)

DO NOT mention: "burnout", "monitoring", "AI detection"
DO: Frame positively, protect employee dignity

Respond in JSON format:
{{
  "talking_points": [
    {{"text": "your point here", "type": "supportive|question|action"}},
    ...
  ],
  "suggested_actions": [
    {{"label": "Schedule 1:1", "action": "calendar_invite"}},
    {{"label": "Block focus time", "action": "protect_schedule"}},
    {{"label": "Offer resources", "action": "show_resources"}}
  ]
}}
"""


def get_risk_narrative_data(db: Session, user_hash: str, time_range: int = 30) -> dict:
    """Fetch comprehensive data for risk narrative generation"""
    risk_score = db.query(RiskScore).filter_by(user_hash=user_hash).first()

    if not risk_score:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No risk data found for user {user_hash}",
        )

    start_date = datetime.utcnow() - timedelta(days=time_range)
    recent_events = (
        db.query(Event)
        .filter(Event.user_hash == user_hash, Event.timestamp >= start_date)
        .order_by(Event.timestamp.desc())
        .limit(500)
        .all()
    )

    risk_history = (
        db.query(RiskHistory)
        .filter(RiskHistory.user_hash == user_hash, RiskHistory.timestamp >= start_date)
        .order_by(RiskHistory.timestamp.asc())
        .all()
    )

    late_night_events = [
        e
        for e in recent_events
        if e.event_type == "commit" and e.metadata_.get("after_hours", False)
    ]

    social_events = [
        e
        for e in recent_events
        if e.event_type in ["slack_message", "pr_review"]
        and e.metadata_.get("is_reply", False)
    ]

    history_velocities = [h.velocity for h in risk_history if h.velocity is not None]
    trend_direction = "stable"
    if len(history_velocities) >= 2:
        if history_velocities[-1] > history_velocities[0] * 1.2:
            trend_direction = "increasing"
        elif history_velocities[-1] < history_velocities[0] * 0.8:
            trend_direction = "decreasing"

    return {
        "user_hash": user_hash,
        "risk_level": risk_score.risk_level or "LOW",
        "velocity": risk_score.velocity or 0.0,
        "belongingness": risk_score.thwarted_belongingness or 0.5,
        "confidence": risk_score.confidence or 0.0,
        "trend": trend_direction,
        "late_night_count": len(late_night_events),
        "social_interaction_count": len(social_events),
        "total_events": len(recent_events),
        "history_points": len(risk_history),
    }


def build_risk_narrative_prompt(data: dict, time_range: int) -> str:
    """Build prompt for generating risk narrative"""

    risk_level = data["risk_level"]
    velocity = data["velocity"]
    belongingness = data["belongingness"]
    trend = data["trend"]
    late_nights = data["late_night_count"]
    social_count = data["social_interaction_count"]
    total_events = data["total_events"]

    if velocity > 2.5:
        velocity_desc = "highly erratic"
    elif velocity > 1.5:
        velocity_desc = "moderately variable"
    elif velocity > 0.5:
        velocity_desc = "slightly elevated"
    else:
        velocity_desc = "stable"

    if belongingness < 0.3:
        belonging_desc = "significantly reduced"
    elif belongingness < 0.5:
        belonging_desc = "somewhat reduced"
    else:
        belonging_desc = "maintained"

    return f"""You are a supportive AI assistant that generates human-readable narratives about work patterns and wellbeing.

Generate a personal narrative report based on the following data from the past {time_range} days:

WORK PATTERN ANALYSIS:
- Schedule variability: {velocity_desc} (velocity score: {velocity:.2f})
- Late night sessions (after 10PM): {late_nights}
- Total activity events: {total_events}

SOCIAL ENGAGEMENT:
- Social interaction level: {belonging_desc} (score: {belongingness:.2f})
- Recent social interactions: {social_count}

RISK ASSESSMENT:
- Current risk level: {risk_level}
- Trend: {trend}

Generate a narrative that:
1. Converts scores into human-readable descriptions (NOT "Velocity: 2.83" but "schedule became unpredictable")
2. Explains patterns in 1-2 sentences
3. Identifies 3 key insights about the person's wellbeing
4. Frames everything supportively - this is for the person's own awareness
5. Does NOT mention "burnout", "monitoring", "AI detection", or "surveillance"
6. Uses phrases like "focus sessions" instead of "work hours"

Respond in JSON format:
{{
  "narrative": "Your 2-3 sentence narrative here...",
  "trend": "{trend}",
  "key_insights": [
    "Insight 1 about work patterns",
    "Insight 2 about social engagement", 
    "Insight 3 about recommendations"
  ]
}}
"""


def get_team_narrative_data(db: Session, team_hash: str, days: int = 30) -> dict:
    """Fetch team data for narrative generation with privacy protection.

    team_hash may be either a Team.id (UUID string) or a user_hash that belongs
    to a TenantMember whose team we should look up.
    """
    import uuid as _uuid
    from app.models.tenant import TenantMember as _TenantMember
    from app.models.team import Team as _Team

    # Attempt to resolve team_hash to a set of member user_hashes via TenantMember
    resolved_member_hashes: list[str] = []

    # Try interpreting team_hash as a Team UUID first
    try:
        team_uuid = _uuid.UUID(team_hash)
        tenant_members = (
            db.query(_TenantMember.user_hash)
            .filter_by(team_id=team_uuid)
            .all()
        )
        resolved_member_hashes = [tm.user_hash for tm in tenant_members]
    except (ValueError, AttributeError):
        pass

    # If no members found via UUID, fall back: treat team_hash as a user_hash and
    # look up that user's team, then fetch all members of that team.
    if not resolved_member_hashes:
        source_member = (
            db.query(_TenantMember)
            .filter_by(user_hash=team_hash)
            .first()
        )
        if source_member and source_member.team_id:
            tenant_members = (
                db.query(_TenantMember.user_hash)
                .filter_by(
                    team_id=source_member.team_id,
                    tenant_id=source_member.tenant_id,
                )
                .all()
            )
            resolved_member_hashes = [tm.user_hash for tm in tenant_members]
            team_hash = str(source_member.team_id)

    if not resolved_member_hashes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No team found with id {team_hash}",
        )

    team_members = (
        db.query(UserIdentity)
        .filter(UserIdentity.user_hash.in_(resolved_member_hashes))
        .all()
    )

    if not team_members:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No team found with id {team_hash}",
        )

    start_date = datetime.utcnow() - timedelta(days=days)

    member_risks = []
    at_risk_count = 0

    for member in team_members:
        risk = db.query(RiskScore).filter_by(user_hash=member.user_hash).first()
        if risk:
            member_risks.append(
                {
                    "user_hash": member.user_hash,
                    "risk_level": risk.risk_level,
                    "velocity": risk.velocity or 0.0,
                    "belongingness": risk.thwarted_belongingness or 0.5,
                }
            )
            if risk.risk_level in ["ELEVATED", "CRITICAL"]:
                at_risk_count += 1

    critical_count = sum(1 for m in member_risks if m["risk_level"] == "CRITICAL")
    elevated_count = sum(1 for m in member_risks if m["risk_level"] == "ELEVATED")
    low_count = sum(1 for m in member_risks if m["risk_level"] == "LOW")

    avg_velocity = (
        sum(m["velocity"] for m in member_risks) / len(member_risks)
        if member_risks
        else 0
    )
    avg_belongingness = (
        sum(m["belongingness"] for m in member_risks) / len(member_risks)
        if member_risks
        else 0
    )

    high_velocity_members = [m for m in member_risks if m["velocity"] > 1.5]

    return {
        "team_hash": team_hash,
        "member_count": len(team_members),
        "at_risk_count": at_risk_count,
        "critical_count": critical_count,
        "elevated_count": elevated_count,
        "low_count": low_count,
        "avg_velocity": avg_velocity,
        "avg_belongingness": avg_belongingness,
        "high_velocity_members": len(high_velocity_members),
        "risk_breakdown": member_risks,
    }


def build_team_narrative_prompt(data: dict, days: int) -> str:
    """Build prompt for generating team health narrative"""

    return f"""You are a supportive AI assistant that generates team health narratives for managers.

Generate a team health narrative based on the following aggregated data from the past {days} days:

TEAM COMPOSITION:
- Team size: {data["member_count"]} members
- Members at risk (ELEVATED or CRITICAL): {data["at_risk_count"]}
- Critical risk: {data["critical_count"]}
- Elevated risk: {data["elevated_count"]}
- Low risk: {data["low_count"]}

TEAM METRICS:
- Average schedule variability: {data["avg_velocity"]:.2f}
- Average social engagement: {data["avg_belongingness"]:.2f}
- High variability members: {data["high_velocity_members"]}

Generate a narrative that:
1. Provides team health overview in 2-3 sentences
2. Identifies patterns without exposing individual names (use "some team members" or "X members")
3. Lists 3 actionable insights for the manager
4. Maintains privacy - do NOT mention specific user hashes or identifiable information
5. Suggests constructive actions like team retrospectives or workload rebalancing
6. Frames everything supportively - this is for helping the team, not criticizing

Respond in JSON format:
{{
  "narrative": "Your 2-3 sentence team narrative here...",
  "trend": "increasing|decreasing|stable",
  "key_insights": [
    "Insight 1 about team patterns",
    "Insight 2 about individual needs",
    "Insight 3 about recommendations"
  ]
}}
"""


@router.get("/report/risk/{user_hash}", response_model=NarrativeReportResponse)
async def generate_risk_report(
    user_hash: str,
    time_range: int = 30,
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
):
    """
    Generate narrative report for individual user risk.

    Converts raw scores into human-readable insights:
    - "Velocity: 2.83" → "Alex's schedule became unpredictable—3 late nights after sprint"
    - "Risk: CRITICAL" → "Consider a supportive check-in about workload"

    Applies privacy filters automatically.
    """
    # Permission check
    if member.role == "employee":
        if member.user_hash != user_hash:
            raise HTTPException(status_code=403, detail="Employees can only view their own risk report")
    elif member.role == "manager":
        # Managers can only view risk reports for members on the same team
        if member.user_hash != user_hash:
            target_member = (
                db.query(TenantMember)
                .filter_by(user_hash=user_hash, tenant_id=member.tenant_id)
                .first()
            )
            if not target_member or target_member.team_id != member.team_id:
                raise HTTPException(status_code=403, detail="Managers can only view risk reports for their team")
    # admin: can view anyone

    try:
        data = get_risk_narrative_data(db, user_hash, time_range)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching risk data for %s: %s", user_hash, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to process request. Please try again.",
        )

    prompt = build_risk_narrative_prompt(data, time_range)

    try:
        response_text = llm_service.generate_insight(prompt)

        import json
        import re

        json_match = re.search(r"\{[\s\S]*\}", response_text)
        if json_match:
            parsed = json.loads(json_match.group())
        else:
            parsed = None

        if parsed:
            narrative = parsed.get("narrative", "")
            trend = parsed.get("trend", data["trend"])
            key_insights = parsed.get("key_insights", [])
        else:
            narrative = f"Your work pattern shows {data['trend']} activity. Consider reviewing your schedule patterns."
            trend = data["trend"]
            key_insights = [
                "Late night sessions have increased",
                "Social interactions may be declining",
                "Consider protecting recovery time",
            ]

    except Exception as e:
        narrative = f"Your work pattern shows {data['trend']} activity. Consider reviewing your schedule patterns."
        trend = data["trend"]
        key_insights = [
            "Late night sessions have increased",
            "Social interactions may be declining",
            "Consider protecting recovery time",
        ]

    return NarrativeReportResponse(
        user_hash=user_hash,
        narrative=narrative,
        trend=trend,
        key_insights=key_insights,
        generated_at=datetime.utcnow().isoformat(),
    )


@router.get("/report/team/{team_hash}", response_model=TeamReportResponse)
async def generate_team_report(
    team_hash: str,
    days: int = 30,
    member: TenantMember = Depends(require_role("manager", "admin")),
    db: Session = Depends(get_db),
):
    """
    Generate team health narrative.

    Provides aggregated team insights with privacy protection:
    - Team health overview
    - Risk distribution
    - Actionable manager recommendations

    Individual data is anonymized in the narrative.
    """
    try:
        data = get_team_narrative_data(db, team_hash, days)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching team data for %s: %s", team_hash, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to process request. Please try again.",
        )

    prompt = build_team_narrative_prompt(data, days)

    try:
        response_text = llm_service.generate_insight(prompt)

        import json
        import re

        json_match = re.search(r"\{[\s\S]*\}", response_text)
        if json_match:
            parsed = json.loads(json_match.group())
        else:
            parsed = None

        if parsed:
            narrative = parsed.get("narrative", "")
            trend = parsed.get("trend", "stable")
            key_insights = parsed.get("key_insights", [])
        else:
            risk_percent = (
                (data["at_risk_count"] / data["member_count"] * 100)
                if data["member_count"] > 0
                else 0
            )
            narrative = f"Team of {data['member_count']} members with {data['at_risk_count']} at elevated risk ({risk_percent:.0f}%). Consider reviewing workload distribution."
            trend = "stable"
            key_insights = [
                "Some team members showing elevated risk",
                "Consider team retrospective",
                "Review workload distribution",
            ]

    except Exception as e:
        risk_percent = (
            (data["at_risk_count"] / data["member_count"] * 100)
            if data["member_count"] > 0
            else 0
        )
        narrative = f"Team of {data['member_count']} members with {data['at_risk_count']} at elevated risk ({risk_percent:.0f}%). Consider reviewing workload distribution."
        trend = "stable"
        key_insights = [
            "Some team members showing elevated risk",
            "Consider team retrospective",
            "Review workload distribution",
        ]

    return TeamReportResponse(
        team_id=team_hash,
        narrative=narrative,
        trend=trend,
        key_insights=key_insights,
        member_count=data["member_count"],
        at_risk_count=data["at_risk_count"],
        generated_at=datetime.utcnow().isoformat(),
    )


@router.post("/copilot/agenda", response_model=AgendaResponse)
async def generate_agenda(
    request: AgendaRequest,
    member: TenantMember = Depends(require_role("manager", "admin")),
    db: Session = Depends(get_db),
):
    """
    Generate AI-powered 1:1 talking points for a user.

    Uses Safety Valve risk data to create supportive manager context.
    """
    user_hash = request.user_hash

    risk_data = get_user_risk_context(db, user_hash)

    prompt = build_copilot_prompt(risk_data)

    try:
        response_text = llm_service.generate_insight(prompt)

        import json
        import re

        json_match = re.search(r"\{[\s\S]*\}", response_text)
        if json_match:
            parsed = json.loads(json_match.group())
        else:
            parsed = {
                "talking_points": [
                    {
                        "text": "How are you feeling about your workload lately?",
                        "type": "question",
                    },
                    {
                        "text": "Let's make sure you're taking enough breaks.",
                        "type": "supportive",
                    },
                    {
                        "text": "Want to block some focus time this week?",
                        "type": "action",
                    },
                ],
                "suggested_actions": [
                    {"label": "Schedule 1:1", "action": "calendar_invite"},
                    {"label": "Block focus time", "action": "protect_schedule"},
                    {"label": "Offer resources", "action": "show_resources"},
                ],
            }

        talking_points = [
            TalkingPoint(text=tp["text"], type=tp["type"])
            for tp in parsed.get("talking_points", [])
        ]

        suggested_actions = [
            SuggestedAction(label=sa["label"], action=sa["action"])
            for sa in parsed.get("suggested_actions", [])
        ]

    except Exception as e:
        talking_points = [
            TalkingPoint(
                text="How are you feeling about your workload lately?", type="question"
            ),
            TalkingPoint(
                text="Let's make sure you're taking enough breaks.", type="supportive"
            ),
            TalkingPoint(
                text="Want to block some focus time this week?", type="action"
            ),
        ]
        suggested_actions = [
            SuggestedAction(label="Schedule 1:1", action="calendar_invite"),
            SuggestedAction(label="Block focus time", action="protect_schedule"),
            SuggestedAction(label="Offer resources", action="show_resources"),
        ]

    return AgendaResponse(
        user_hash=user_hash,
        risk_level=risk_data["risk_level"],
        talking_points=talking_points,
        suggested_actions=suggested_actions,
        generated_at=datetime.utcnow().isoformat(),
    )


@router.post("/query", response_model=QueryResponse)
async def semantic_query(
    request: QueryRequest,
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
):
    """
    Natural language query over employee data.

    Supports queries like:
    - "Who knows PostgreSQL and isn't burned out?"
    - "Which team members are at risk?"
    - "Show me high-impact people who might leave"
    - "Who are the hidden gems?"

    Applies role-based privacy filters automatically.
    """
    query = request.query
    user_role = member.role

    intent = parse_query_intent(query)

    results = execute_semantic_query(
        db=db,
        intent=intent,
        user_role=user_role,
        current_user_hash=member.user_hash,
        tenant_id=str(member.tenant_id),
    )

    prompt = build_query_response_prompt(query, results, intent["query_type"])

    try:
        llm_response = llm_service.generate_insight(prompt)
    except Exception as e:
        llm_response = f"Found {len(results)} matching team members."

    query_results = []
    for r in results[:10]:
        risk = r.get("risk_level", "healthy")
        vel = r.get("velocity")
        insights = []
        suggested_action = None

        if risk == "critical":
            insights.append("Showing critical burnout indicators requiring immediate attention")
            suggested_action = "Schedule an urgent 1:1 check-in"
        elif risk == "elevated":
            insights.append("Elevated risk detected — early intervention recommended")
            suggested_action = "Review workload and schedule a supportive conversation"
        elif risk == "healthy":
            insights.append("Currently in a healthy state with stable patterns")

        if vel is not None:
            if vel < 0.4:
                insights.append(f"Low velocity ({vel:.2f}) — potential overload or disengagement")
            elif vel > 0.8:
                insights.append(f"High velocity ({vel:.2f}) — strong output but monitor for sustainability")

        betw = r.get("betweenness")
        if betw is not None and betw > 0.6:
            insights.append(f"High betweenness centrality ({betw:.2f}) — key connector in team network")

        query_results.append(
            QueryResult(
                user_hash=r.get("user_hash", ""),
                name=r.get("display_name") or f"Team Member {r.get('user_hash', '')[:6]}",
                risk_level=risk,
                velocity=vel,
                betweenness=betw,
                eigenvector=r.get("eigenvector"),
                skills=r.get("skills", []),
                tenure_months=r.get("tenure_months"),
                insights=insights,
                suggested_action=suggested_action,
            )
        )

    return QueryResponse(
        query=query,
        response=llm_response,
        results=query_results,
        query_type=intent["query_type"],
    )


# Import shared prompts — avoid duplication with sentinel_chat.py
from app.services.sentinel_chat import ROLE_SYSTEM_PROMPTS


def get_user_context_data(db: Session, user_hash: str) -> dict:
    """Fetch relevant context data for chat based on user's role and data."""
    risk_score = db.query(RiskScore).filter_by(user_hash=user_hash).first()
    tenant_member = db.query(TenantMember).filter_by(user_hash=user_hash).first()
    centrality = db.query(CentralityScore).filter_by(user_hash=user_hash).first()

    member_role = tenant_member.role if tenant_member else "employee"

    context = {
        "user_hash": user_hash,
        "risk_level": risk_score.risk_level if risk_score else "LOW",
        "velocity": risk_score.velocity if risk_score else 0.0,
        "belongingness": risk_score.thwarted_belongingness if risk_score else 0.5,
        "confidence": risk_score.confidence if risk_score else 0.0,
        "role": member_role,
        "betweenness": centrality.betweenness if centrality else 0.0,
        "eigenvector": centrality.eigenvector if centrality else 0.0,
        "unblocking_count": centrality.unblocking_count if centrality else 0,
    }

    # Add team context for managers
    if tenant_member and member_role == "manager" and tenant_member.team_id:
        team_member_hashes = [
            tm.user_hash
            for tm in db.query(TenantMember.user_hash).filter_by(
                team_id=tenant_member.team_id,
                tenant_id=tenant_member.tenant_id,
            ).all()
        ]
        if team_member_hashes:
            team_risks = (
                db.query(RiskScore)
                .filter(RiskScore.user_hash.in_(team_member_hashes))
                .all()
            )

            at_risk_count = sum(
                1 for r in team_risks if r.risk_level in ["ELEVATED", "CRITICAL"]
            )
            critical_count = sum(1 for r in team_risks if r.risk_level == "CRITICAL")

            context["team_size"] = len(team_member_hashes)
            context["team_at_risk_count"] = at_risk_count
            context["team_critical_count"] = critical_count

    # Add organization context for admins
    if member_role == "admin":
        tenant_hashes = {
            tm.user_hash
            for tm in db.query(TenantMember.user_hash)
            .filter_by(tenant_id=tenant_member.tenant_id)
            .all()
        }
        all_risks = (
            db.query(RiskScore)
            .filter(RiskScore.user_hash.in_(tenant_hashes))
            .all()
        )
        total_users = len(all_risks)
        org_at_risk = sum(
            1 for r in all_risks if r.risk_level in ["ELEVATED", "CRITICAL"]
        )
        org_critical = sum(1 for r in all_risks if r.risk_level == "CRITICAL")

        context["org_total_users"] = total_users
        context["org_at_risk_count"] = org_at_risk
        context["org_critical_count"] = org_critical
        context["org_risk_percentage"] = (
            (org_at_risk / total_users * 100) if total_users > 0 else 0
        )

    return context


def build_chat_prompt(
    message: str, role: str, context: dict, conversation_history: Optional[str] = None
) -> str:
    """Build the complete prompt for the chat including system prompt and context."""
    system_prompt = ROLE_SYSTEM_PROMPTS.get(role, ROLE_SYSTEM_PROMPTS["employee"])

    # Format context based on role
    context_str = format_context_for_role(context, role)

    prompt = f"""{system_prompt}

USER CONTEXT:
{context_str}

{"PREVIOUS CONVERSATION:\n" + conversation_history + "\n" if conversation_history else ""}USER MESSAGE:
{message}

Provide a helpful, personalized response based on the user's role ({role}) and their context. Be concise but informative."""

    return prompt


def format_context_for_role(context: dict, role: str) -> str:
    """Format context data appropriately for the user's role."""
    if role == "employee":
        return f"""- Personal Risk Level: {context["risk_level"]}
- Work Pattern Velocity: {context["velocity"]:.2f} (higher = more variable schedule)
- Social Engagement: {context["belongingness"]:.2f} (higher = more connected)
- Network Influence: {context["betweenness"]:.2f} (how much you unblock others)"""

    elif role == "manager":
        team_context = ""
        if "team_size" in context:
            team_context = f"""
- Team Size: {context["team_size"]}
- Team Members At Risk: {context.get("team_at_risk_count", 0)}
- Critical Cases: {context.get("team_critical_count", 0)}"""

        return f"""- Your Role: Manager
- Personal Metrics: Risk {context["risk_level"]}, Velocity {context["velocity"]:.2f}{team_context}"""

    elif role == "admin":
        org_context = ""
        if "org_total_users" in context:
            org_context = f"""
- Organization Size: {context["org_total_users"]}
- Users At Risk: {context["org_at_risk_count"]} ({context.get("org_risk_percentage", 0):.1f}%)
- Critical Cases: {context["org_critical_count"]}"""

        return f"""- Your Role: Administrator
- Personal Risk Level: {context["risk_level"]}{org_context}"""

    return f"- Risk Level: {context['risk_level']}\n- Role: {role}"


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
):
    """
    Role-aware AI chat endpoint (Ask Sentinel).

    Delegates to SentinelChatService which runs the full pipeline:
    refusal check -> workflow intent -> data boundary -> LLM.
    Persists both user and assistant turns to ChatHistory after response.
    """
    # Build a lightweight UserIdentity-like object from the member's data
    current_user = (
        db.query(UserIdentity).filter_by(user_hash=member.user_hash).first()
    )
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User identity not found",
        )

    tenant_id = str(member.tenant_id)

    try:
        # Resolve or create session; map session_id -> conversation_id so the
        # chat service pipeline stays unchanged.
        chat_history_svc = ChatHistoryService(db)
        if request.session_id:
            session = chat_history_svc.get_session(
                member.user_hash, tenant_id, request.session_id
            )
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")
            effective_session_id = request.session_id
        else:
            session = chat_history_svc.create_session(
                user_hash=member.user_hash,
                tenant_id=tenant_id,
                title="Untitled Chat",
            )
            db.flush()
            db.commit()  # Fix C1: commit session immediately so it's durable
            effective_session_id = str(session.id)

        # Create a copy with conversation_id set (Fix H3: don't mutate the original)
        effective_request = request.model_copy(update={"conversation_id": effective_session_id})

        result = await sentinel_chat_service.respond(
            effective_request, current_user, tenant_id, db
        )

        # Persist turns if we got an actual response (Fix H4: don't use risk_level heuristic)
        conversation_id = result.conversation_id or effective_session_id
        if result.response:
            chat_history_svc.persist_turn(
                member.user_hash, tenant_id, conversation_id, "user", request.message,
            )
            chat_history_svc.persist_turn(
                member.user_hash, tenant_id, conversation_id, "assistant", result.response,
                metadata={"role": result.role},
            )
            db.commit()

            # Auto-title (Fix C1: run in thread to avoid blocking event loop)
            # Fix H2: remove stale ORM guard, let service method decide
            import asyncio
            try:
                await asyncio.to_thread(
                    chat_history_svc.auto_title_session,
                    member.user_hash, tenant_id, effective_session_id, effective_request.message,
                )
                db.commit()
            except Exception:
                pass  # Non-critical, don't break the response

        # Attach session_id to response so the frontend can store it
        result.conversation_id = effective_session_id
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error processing chat request for %s: %s",
            member.user_hash, e, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to process request. Please try again.",
        )


@router.post("/feedback")
async def submit_chat_feedback(
    body: dict,
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """
    Record user feedback on an AI response.

    Expected body fields:
    - conversation_id (str): Identifier of the conversation thread.
    - message_index (int): Zero-based index of the message being rated.
    - rating (str): "positive" or "negative".

    Feedback is logged for offline analysis.  Returns immediately with a
    confirmation so the UI can update optimistically.
    """
    conversation_id = body.get("conversation_id", "")
    message_index = body.get("message_index")
    rating = body.get("rating", "")

    if rating not in ("positive", "negative"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="rating must be 'positive' or 'negative'",
        )

    logger.info(
        "chat_feedback received: conversation_id=%s message_index=%s "
        "rating=%s user=%s",
        conversation_id,
        message_index,
        rating,
        current_user.user_hash,
    )

    return {"status": "ok", "message": "Feedback recorded"}


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
):
    """
    Streaming version of Ask Sentinel. Returns Server-Sent Events.

    Wraps the stream to accumulate the full response text and persist
    both user and assistant turns to ChatHistory after completion.
    """
    current_user = (
        db.query(UserIdentity).filter_by(user_hash=member.user_hash).first()
    )
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User identity not found",
        )

    tenant_id = str(member.tenant_id)

    # Resolve or create session before the stream starts so we can embed
    # session_id in the SSE 'done' event for the frontend to pick up.
    chat_history_svc = ChatHistoryService(db)
    if request.session_id:
        _session = chat_history_svc.get_session(
            member.user_hash, tenant_id, request.session_id
        )
        if not _session:
            raise HTTPException(status_code=404, detail="Session not found")
        effective_session_id = request.session_id
    else:
        _session = chat_history_svc.create_session(
            user_hash=member.user_hash,
            tenant_id=tenant_id,
            title="Untitled Chat",
        )
        db.flush()
        db.commit()
        effective_session_id = str(_session.id)

    # Create a copy with conversation_id set (Fix H3: don't mutate the original)
    effective_request = request.model_copy(update={"conversation_id": effective_session_id})

    async def _stream_and_persist() -> AsyncGenerator[str, None]:
        """Wrap the inner stream to capture accumulated text for persistence."""
        accumulated_response: list[str] = []
        conversation_id: Optional[str] = None

        async for chunk in sentinel_chat_service.respond_stream(
            effective_request, current_user, tenant_id, db
        ):
            # Rewrite the 'done' event to include session_id
            try:
                if chunk.startswith("data: "):
                    payload = json.loads(chunk[6:].strip())
                    event_type = payload.get("type")
                    if event_type == "token":
                        accumulated_response.append(payload.get("content", ""))
                    elif event_type == "done":
                        conversation_id = payload.get("conversation_id")
                        payload["session_id"] = effective_session_id
                        chunk = f"data: {json.dumps(payload)}\n\n"
            except (json.JSONDecodeError, AttributeError):
                pass

            yield chunk

        # After stream completes, persist both turns.
        # Always persist the user turn if we have a conversation_id,
        # even if the LLM errored (so the user message is not lost).
        full_response = "".join(accumulated_response)
        resolved_conv_id = conversation_id or effective_session_id
        if resolved_conv_id:
            try:
                chat_history_svc.persist_turn(
                    member.user_hash, tenant_id, resolved_conv_id,
                    "user", request.message,
                )
                if full_response:
                    chat_history_svc.persist_turn(
                        member.user_hash, tenant_id, resolved_conv_id,
                        "assistant", full_response,
                        metadata={"role": member.role},
                    )
                db.commit()

                # Auto-title after first message (runs sync — acceptable in stream cleanup)
                try:
                    chat_history_svc.auto_title_session(
                        member.user_hash, tenant_id, effective_session_id, effective_request.message
                    )
                    db.commit()
                except Exception:
                    pass  # Non-critical, don't break the stream
            except Exception as persist_err:
                logger.error(
                    "Failed to persist chat turns: %s", persist_err, exc_info=True,
                )

    try:
        return StreamingResponse(
            _stream_and_persist(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error processing streaming chat request for %s: %s",
            member.user_hash, e, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to process request. Please try again.",
        )


# ------------------------------------------------------------------
# Chat History endpoints (Task 8)
# ------------------------------------------------------------------


@router.get("/chat/history")
def get_chat_history(
    limit: int = QueryParam(default=20, le=50),
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
):
    """Get user's conversation list (most recent first)."""
    service = ChatHistoryService(db)
    conversations = service.get_conversations(
        user_hash=member.user_hash,
        tenant_id=str(member.tenant_id),
        limit=limit,
    )
    return {"conversations": conversations}


@router.get("/chat/history/{conversation_id}")
def get_conversation_turns(
    conversation_id: str,
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
):
    """Get all turns for a specific conversation."""
    service = ChatHistoryService(db)
    turns = service.get_conversation_turns(
        user_hash=member.user_hash,
        tenant_id=str(member.tenant_id),
        conversation_id=conversation_id,
    )
    if not turns:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Serialize turns for JSON response
    serialized_turns = [
        {
            "id": turn.id,
            "role": turn.role,
            "content": turn.content,
            "created_at": turn.created_at.isoformat() if turn.created_at else None,
            "metadata": turn.metadata_,
        }
        for turn in turns
    ]
    return {"conversation_id": conversation_id, "turns": serialized_turns}


# ------------------------------------------------------------------
# Chat Session CRUD endpoints (Task A3)
# ------------------------------------------------------------------


@router.post("/chat/sessions")
def create_chat_session(
    body: dict,
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
):
    """Create a new named chat session.

    Body fields:
    - title (str, optional): Display name for the session. Defaults to "Untitled Chat".
    """
    raw_title = body.get("title", "Untitled Chat")
    title = str(raw_title).strip()[:255] or "Untitled Chat"

    service = ChatHistoryService(db)
    session = service.create_session(
        user_hash=member.user_hash,
        tenant_id=str(member.tenant_id),
        title=title,
    )
    db.commit()
    return {
        "id": session.id,
        "title": session.title,
        "created_at": session.created_at.isoformat() if session.created_at else None,
    }


@router.get("/chat/sessions")
def list_chat_sessions(
    limit: int = QueryParam(default=20, ge=1, le=50),
    offset: int = QueryParam(default=0, ge=0),
    search: Optional[str] = QueryParam(default=None),
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
):
    """Return a paginated list of active sessions for the authenticated user.

    Query parameters:
    - limit  (int, 1-50): Number of sessions per page. Default: 20.
    - offset (int, >=0):  Sessions to skip. Default: 0.
    - search (str):       Case-insensitive substring filter on title.
    """
    service = ChatHistoryService(db)
    sessions = service.get_sessions(
        user_hash=member.user_hash,
        tenant_id=str(member.tenant_id),
        limit=limit,
        offset=offset,
        search=search,
    )
    return {
        "sessions": [
            {
                "id": s.id,
                "title": s.title,
                "is_favorite": s.is_favorite,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            }
            for s in sessions
        ]
    }


@router.get("/chat/sessions/{session_id}")
def get_chat_session(
    session_id: str,
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
):
    """Return a single session with its full message history."""
    service = ChatHistoryService(db)
    session = service.get_session(
        member.user_hash, str(member.tenant_id), session_id
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    turns = service.get_conversation_turns(
        user_hash=member.user_hash,
        tenant_id=str(member.tenant_id),
        conversation_id=session_id,
    )

    return {
        "id": session.id,
        "title": session.title,
        "is_favorite": session.is_favorite,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        "turns": [
            {
                "id": t.id,
                "role": t.role,
                "content": t.content,
                "type": getattr(t, "type", "message") or "message",
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "metadata": t.metadata_,
            }
            for t in turns
        ],
    }


@router.put("/chat/sessions/{session_id}")
def rename_chat_session(
    session_id: str,
    body: dict,
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
):
    """Rename an existing chat session.

    Body fields:
    - title (str, required): New display name for the session.
    """
    raw_title = body.get("title", "")
    title = str(raw_title).strip()[:255]
    if not title:
        raise HTTPException(status_code=422, detail="title must not be empty")

    service = ChatHistoryService(db)
    session = service.rename_session(
        user_hash=member.user_hash,
        tenant_id=str(member.tenant_id),
        session_id=session_id,
        title=title,
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    db.commit()
    return {
        "id": session.id,
        "title": session.title,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
    }


@router.delete("/chat/sessions/{session_id}")
def delete_chat_session(
    session_id: str,
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
):
    """Soft-delete a chat session (sets is_active=False).

    The underlying message history is preserved for audit purposes.
    """
    service = ChatHistoryService(db)
    deleted = service.delete_session(
        user_hash=member.user_hash,
        tenant_id=str(member.tenant_id),
        session_id=session_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")

    db.commit()
    return {"status": "deleted", "session_id": session_id}


@router.post("/chat/sessions/{session_id}/favorite")
def toggle_session_favorite(
    session_id: str,
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
):
    """Toggle the favorite/pin flag on a chat session."""
    service = ChatHistoryService(db)
    session = service.toggle_favorite(
        user_hash=member.user_hash,
        tenant_id=str(member.tenant_id),
        session_id=session_id,
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    db.commit()
    return {
        "id": session.id,
        "is_favorite": session.is_favorite,
    }
