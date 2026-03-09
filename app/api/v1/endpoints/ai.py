from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Optional, List

from app.core.database import get_db
from app.models.analytics import RiskScore, Event, RiskHistory, CentralityScore
from app.models.identity import UserIdentity
from app.api.deps.auth import get_current_user_identity
from app.services.llm import llm_service
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

router = APIRouter()


# Alias for /narratives/team/{team_hash} -> /report/team/{team_hash}
@router.get("/narratives/team/{team_hash}")
async def get_team_narrative_alias(
    team_hash: str,
    days: int = 30,
    current_user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """
    Generate team health narrative (alias endpoint).
    Provides aggregated team insights with privacy protection.
    """
    return await generate_team_narrative_report(
        team_hash=team_hash, days=days, current_user=current_user, db=db
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
                    if k not in ["email", "slack_id", "manager_hash"]
                }
                filtered.append(r_filtered)
            elif r.get("risk_level") == "CRITICAL":
                r_filtered = {
                    k: v
                    for k, v in r.items()
                    if k not in ["email", "slack_id", "manager_hash"]
                }
                filtered.append(r_filtered)
        return filtered

    return []


def execute_semantic_query(
    db: Session, intent: dict, user_role: str, current_user_hash: str
) -> List[dict]:
    """
    Execute query based on parsed intent.
    Returns list of user data matching filters.
    """
    results = []

    if intent["query_type"] == "at_risk":
        risk_levels = intent["filters"].get("risk_level", ["ELEVATED", "CRITICAL"])
        users = db.query(RiskScore).filter(RiskScore.risk_level.in_(risk_levels)).all()

        for u in users:
            identity = db.query(UserIdentity).filter_by(user_hash=u.user_hash).first()
            centrality = (
                db.query(CentralityScore).filter_by(user_hash=u.user_hash).first()
            )

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
                    "manager_hash": identity.manager_hash if identity else None,
                }
            )

    elif intent["query_type"] == "not_burned_with_skill":
        users = (
            db.query(RiskScore)
            .filter(RiskScore.risk_level.in_(["LOW", "ELEVATED"]))
            .all()
        )

        centrality_users = (
            db.query(CentralityScore).filter(CentralityScore.betweenness > 0.2).all()
        )

        centrality_hashes = {c.user_hash for c in centrality_users}

        for u in users:
            if u.user_hash in centrality_hashes:
                identity = (
                    db.query(UserIdentity).filter_by(user_hash=u.user_hash).first()
                )
                centrality = (
                    db.query(CentralityScore).filter_by(user_hash=u.user_hash).first()
                )

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
                        "manager_hash": identity.manager_hash if identity else None,
                    }
                )

    elif intent["query_type"] == "hidden_gems":
        min_betweenness = intent["filters"].get("min_betweenness", 0.3)
        min_unblocking = intent["filters"].get("min_unblocking", 5)

        users = (
            db.query(CentralityScore)
            .filter(
                CentralityScore.betweenness >= min_betweenness,
                CentralityScore.unblocking_count >= min_unblocking,
            )
            .all()
        )

        for c in users:
            risk = db.query(RiskScore).filter_by(user_hash=c.user_hash).first()
            identity = db.query(UserIdentity).filter_by(user_hash=c.user_hash).first()

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
                    "manager_hash": identity.manager_hash if identity else None,
                }
            )

    elif intent["query_type"] == "flight_risk":
        users = (
            db.query(RiskScore)
            .filter(RiskScore.risk_level.in_(["ELEVATED", "CRITICAL"]))
            .all()
        )

        for u in users:
            identity = db.query(UserIdentity).filter_by(user_hash=u.user_hash).first()
            centrality = (
                db.query(CentralityScore).filter_by(user_hash=u.user_hash).first()
            )

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
                    "manager_hash": identity.manager_hash if identity else None,
                }
            )

    else:
        users = db.query(RiskScore).all()

        for u in users:
            identity = db.query(UserIdentity).filter_by(user_hash=u.user_hash).first()
            centrality = (
                db.query(CentralityScore).filter_by(user_hash=u.user_hash).first()
            )

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
                    "manager_hash": identity.manager_hash if identity else None,
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
    """Fetch team data for narrative generation with privacy protection"""
    from app.models.identity import UserIdentity

    team_members = db.query(UserIdentity).filter_by(manager_hash=team_hash).all()

    if not team_members:
        team_members = db.query(UserIdentity).filter_by(user_hash=team_hash).all()
        if team_members and team_members[0].manager_hash:
            team_hash = team_members[0].manager_hash
            team_members = (
                db.query(UserIdentity).filter_by(manager_hash=team_hash).all()
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
    current_user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """
    Generate narrative report for individual user risk.

    Converts raw scores into human-readable insights:
    - "Velocity: 2.83" → "Alex's schedule became unpredictable—3 late nights after sprint"
    - "Risk: CRITICAL" → "Consider a supportive check-in about workload"

    Applies privacy filters automatically.
    """
    try:
        data = get_risk_narrative_data(db, user_hash, time_range)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error fetching risk data: {str(e)}",
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
    current_user: UserIdentity = Depends(get_current_user_identity),
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error fetching team data: {str(e)}",
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
    current_user: UserIdentity = Depends(get_current_user_identity),
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
    current_user: UserIdentity = Depends(get_current_user_identity),
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
    user_role = request.user_role or current_user.role

    intent = parse_query_intent(query)

    results = execute_semantic_query(
        db=db,
        intent=intent,
        user_role=user_role,
        current_user_hash=current_user.user_hash,
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


# Role-aware system prompts for chat
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


def get_user_context_data(db: Session, user_hash: str) -> dict:
    """Fetch relevant context data for chat based on user's role and data."""
    risk_score = db.query(RiskScore).filter_by(user_hash=user_hash).first()
    identity = db.query(UserIdentity).filter_by(user_hash=user_hash).first()
    centrality = db.query(CentralityScore).filter_by(user_hash=user_hash).first()

    context = {
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

    # Add team context for managers
    if identity and identity.role == "manager":
        team_members = db.query(UserIdentity).filter_by(manager_hash=user_hash).all()
        if team_members:
            member_hashes = [m.user_hash for m in team_members]
            team_risks = (
                db.query(RiskScore).filter(RiskScore.user_hash.in_(member_hashes)).all()
            )

            at_risk_count = sum(
                1 for r in team_risks if r.risk_level in ["ELEVATED", "CRITICAL"]
            )
            critical_count = sum(1 for r in team_risks if r.risk_level == "CRITICAL")

            context["team_size"] = len(team_members)
            context["team_at_risk_count"] = at_risk_count
            context["team_critical_count"] = critical_count

    # Add organization context for admins
    if identity and identity.role == "admin":
        all_risks = db.query(RiskScore).all()
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
    current_user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """
    Role-aware AI chat endpoint.

    Provides personalized responses based on user role:
    - Employee: Focus on personal wellbeing, growth, 1:1 prep
    - Manager: Focus on team insights, risk analysis
    - Admin: Focus on organization analytics

    Includes user's actual data context (risk level, etc.) in responses.
    """
    try:
        # Get user's role and context
        user_role = current_user.role or "employee"
        user_context = get_user_context_data(db, current_user.user_hash)

        # Merge request context with user context
        if request.context:
            user_context.update(request.context)

        # Build role-aware prompt with system message and context
        system_prompt = ROLE_SYSTEM_PROMPTS.get(user_role, ROLE_SYSTEM_PROMPTS["employee"])
        context_str = format_context_for_role(user_context, user_role)
        full_system = f"{system_prompt}\n\nUSER CONTEXT:\n{context_str}"

        # Build message list for multi-turn conversation
        messages = [{"role": "system", "content": full_system}]

        # Include conversation history if provided via context
        history = (request.context or {}).get("conversation_history", [])
        if isinstance(history, list):
            for msg in history[-10:]:  # Last 10 messages for context window safety
                if isinstance(msg, dict) and "role" in msg and "content" in msg:
                    messages.append({"role": msg["role"], "content": msg["content"]})

        messages.append({"role": "user", "content": request.message})

        # Generate response using LLM with conversation support
        llm_response = llm_service.generate_chat_response(messages)

        # Generate conversation ID if not provided (for tracking)
        conversation_id = (
            request.conversation_id
            or f"chat_{current_user.user_hash}_{datetime.utcnow().timestamp()}"
        )

        return ChatResponse(
            response=llm_response,
            role=user_role,
            conversation_id=conversation_id,
            context_used=ChatContextUsed(
                risk_level=user_context.get("risk_level"),
                velocity=user_context.get("velocity"),
                belongingness=user_context.get("belongingness"),
                team_size=user_context.get("team_size"),
                org_total_users=user_context.get("org_total_users"),
            ),
            generated_at=datetime.utcnow().isoformat(),
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing chat request: {str(e)}",
        )
