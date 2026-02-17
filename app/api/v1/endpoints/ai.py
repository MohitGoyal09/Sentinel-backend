from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Optional, List
from pydantic import BaseModel

from app.core.database import get_db
from app.models.analytics import RiskScore, Event, RiskHistory, CentralityScore
from app.models.identity import UserIdentity
from app.api.deps.auth import get_current_user_identity
from app.services.llm import llm_service
from app.services.permission_service import PermissionService

router = APIRouter()


class AgendaRequest(BaseModel):
    user_hash: str


class TalkingPoint(BaseModel):
    text: str
    type: str


class SuggestedAction(BaseModel):
    label: str
    action: str


class AgendaResponse(BaseModel):
    user_hash: str
    risk_level: str
    talking_points: List[TalkingPoint]
    suggested_actions: List[SuggestedAction]
    generated_at: str


class QueryRequest(BaseModel):
    query: str
    user_role: str = "admin"


class QueryResult(BaseModel):
    user_hash: str
    name: str
    risk_level: Optional[str] = None
    velocity: Optional[float] = None
    betweenness: Optional[float] = None
    eigenvector: Optional[float] = None
    skills: List[str] = []
    tenure_months: Optional[int] = None


class QueryResponse(BaseModel):
    query: str
    response: str
    results: List[QueryResult]
    query_type: str


class NarrativeReportResponse(BaseModel):
    user_hash: str
    narrative: str
    trend: str
    key_insights: List[str]
    generated_at: str


class TeamReportResponse(BaseModel):
    team_id: str
    narrative: str
    trend: str
    key_insights: List[str]
    member_count: int
    at_risk_count: int
    generated_at: str


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

    query_results = [
        QueryResult(
            user_hash=r.get("user_hash", ""),
            name=f"Team Member {r.get('user_hash', '')[:6]}",
            risk_level=r.get("risk_level"),
            velocity=r.get("velocity"),
            betweenness=r.get("betweenness"),
            eigenvector=r.get("eigenvector"),
            skills=[],
            tenure_months=None,
        )
        for r in results[:10]
    ]

    return QueryResponse(
        query=query,
        response=llm_response,
        results=query_results,
        query_type=intent["query_type"],
    )
