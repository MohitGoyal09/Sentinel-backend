from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from app.services.safety_valve import SafetyValve
from app.services.talent_scout import TalentScout
from app.services.culture_temp import CultureThermometer
from app.services.simulation import RealTimeSimulator
from app.models.analytics import Event
from app.core.vault import VaultManager
from app.api.deps import get_db
from app.core.database import SessionLocal
from app.schemas.engines import (
    CreatePersonaRequest,
    InjectEventRequest,
    AnalyzeTeamRequest,
    SimulationResponse,
    SafetyValveResponse,
    TalentScoutResponse,
    CultureThermometerResponse,
    RealtimeInjectionResponse,
    UserListResponse,
    RiskHistoryResponse,
    NudgeResponse,
    ActivityEventResponse,
)
from datetime import datetime, timedelta
from app.services.context import ContextEnricher
from typing import Optional
from app.models.identity import UserIdentity
from app.core.security import privacy
from app.services.permission_service import PermissionService, PermissionDenied

# Auth dependencies - import conditionally to avoid breaking if Supabase not configured
try:
    from app.api.deps.auth import get_current_user, get_current_user_identity

    AUTH_ENABLED = True
except Exception:
    AUTH_ENABLED = False

    async def get_current_user():
        return {"id": "demo", "email": "demo@algoquest.ai"}

    async def get_current_user_identity():
        return None


router = APIRouter()


# Permission check helper
def check_user_data_access(
    db: Session,
    current_user: UserIdentity,
    target_user_hash: str,
) -> None:
    """
    Check if current user has permission to access target user's data.
    Raises HTTPException 403 if access is denied.
    """
    if not current_user:
        # Demo mode - allow access
        return

    perm_service = PermissionService(db)
    can_view, reason = perm_service.can_view_user_data(current_user, target_user_hash)

    # Log the access attempt
    accessor_hash = getattr(current_user, "user_hash", None)
    if accessor_hash is not None:
        accessor_hash = str(accessor_hash)
    else:
        accessor_hash = "unknown"
    perm_service.log_data_access(
        accessor_hash=accessor_hash,
        target_hash=target_user_hash,
        action="view_engine_data",
        details={
            "granted": can_view,
            "reason": reason,
            "endpoint": "engines",
        },
    )

    if not can_view:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: {reason}",
        )


# Background task wrapper
def run_all_engines(user_hash: str):
    with SessionLocal() as db:
        SafetyValve(db).analyze(user_hash)
        TalentScout(db).analyze_network()


# ============ SIMULATION / PERSONAS ============


@router.post(
    "/personas", response_model=SimulationResponse, status_code=status.HTTP_201_CREATED
)
def create_persona(
    request: CreatePersonaRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Create a persona with 30 days of synthetic behavioral data"""
    sim = RealTimeSimulator(db)
    vault = VaultManager(db, db)

    user_hash = vault.store_identity(request.email)
    events = sim.create_persona(request.persona_type, user_hash)

    for event in events:
        db.add(event)

    if request.persona_type in ["sarah_gem", "maria_contagion"]:
        team = ["alex_hash", "sarah_hash", "jordan_hash"]
        edges = sim._create_team_network(team)
        for edge in edges:
            db.add(edge)

    db.commit()
    background_tasks.add_task(run_all_engines, user_hash)

    return SimulationResponse(
        success=True,
        data={
            "user_hash": user_hash,
            "events_count": len(events),
            "persona": request.persona_type,
        },
    )


# ============ ENGINE ANALYSIS ============


@router.get("/users/{user_hash}/context")
async def check_user_context(
    user_hash: str,
    timestamp: str = None,
    db: Session = Depends(get_db),
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """Check contextual explanation for a specific timestamp"""

    # RBAC Check: Verify user has permission to access this data
    check_user_data_access(db, current_user, user_hash)

    enricher = ContextEnricher(db)

    # Get email
    user = db.query(UserIdentity).filter_by(user_hash=user_hash).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    email = privacy.decrypt(user.email_encrypted)

    if timestamp:
        try:
            check_time = datetime.fromisoformat(timestamp)
        except ValueError:
            check_time = datetime.utcnow()
    else:
        check_time = datetime.utcnow()

    context = await enricher.is_explained(email, check_time)

    # Envelope pattern manually here, or we could add schema
    return {
        "success": True,
        "data": {"user_hash": user_hash, "timestamp": check_time, "context": context},
    }


@router.get("/users/{user_hash}/safety", response_model=SafetyValveResponse)
def analyze_user_safety(
    user_hash: str,
    db: Session = Depends(get_db),
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """Analyze burnout risk for a specific user"""
    # RBAC Check: Verify user has permission to access this data
    check_user_data_access(db, current_user, user_hash)

    engine = SafetyValve(db)
    result = engine.analyze(user_hash)
    return SafetyValveResponse(success=True, data=result)


@router.get("/users/{user_hash}/talent", response_model=TalentScoutResponse)
def analyze_user_network(
    user_hash: str,
    db: Session = Depends(get_db),
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """Analyze network centrality and hidden gem potential"""
    # RBAC Check: Verify user has permission to access this data
    check_user_data_access(db, current_user, user_hash)

    engine = TalentScout(db)
    result = engine.analyze_network()
    return TalentScoutResponse(success=True, data=result)


@router.post("/teams/culture", response_model=CultureThermometerResponse)
def analyze_team_culture(request: AnalyzeTeamRequest, db: Session = Depends(get_db)):
    """Analyze team-level contagion risk"""
    engine = CultureThermometer(db)

    team_hashes = request.team_hashes
    if not team_hashes:
        # Analyze all users if no specific team provided
        from app.models.identity import UserIdentity

        users = db.query(UserIdentity).all()
        team_hashes = [u.user_hash for u in users]

    result = engine.analyze_team(team_hashes)
    return CultureThermometerResponse(success=True, data=result)


@router.post("/teams/forecast")
def forecast_contagion(
    request: AnalyzeTeamRequest, days: int = 30, db: Session = Depends(get_db)
):
    """
    Forecast team risk contagion using SIR epidemic model.
    Returns predicted spread of burnout risk over time.
    """
    from app.services.sir_model import predict_contagion_risk
    from app.models.analytics import RiskScore, GraphEdge
    from app.models.identity import UserIdentity

    team_hashes = request.team_hashes
    if not team_hashes:
        users = db.query(UserIdentity).all()
        team_hashes = [u.user_hash for u in users]

    total_members = len(team_hashes)

    # Count infected (elevated/critical)
    infected_count = (
        db.query(RiskScore)
        .filter(
            RiskScore.user_hash.in_(team_hashes),
            RiskScore.risk_level.in_(["ELEVATED", "CRITICAL"]),
        )
        .count()
    )

    # Calculate average connections from graph
    edge_count = (
        db.query(GraphEdge).filter(GraphEdge.source_hash.in_(team_hashes)).count()
    )
    avg_connections = edge_count / total_members if total_members > 0 else 2.0

    # Calculate average risk score
    risks = db.query(RiskScore).filter(RiskScore.user_hash.in_(team_hashes)).all()
    risk_map = {"LOW": 0.2, "ELEVATED": 0.6, "CRITICAL": 0.9, "CALIBRATING": 0.3}
    avg_risk = (
        sum(risk_map.get(r.risk_level, 0.3) for r in risks) / len(risks)
        if risks
        else 0.3
    )

    result = predict_contagion_risk(
        total_members=total_members,
        infected_count=infected_count,
        avg_connections=avg_connections,
        avg_risk_score=avg_risk,
        days=days,
    )

    return {"success": True, "data": result}


# ============ REALTIME EVENTS ============


@router.post(
    "/events",
    response_model=RealtimeInjectionResponse,
    status_code=status.HTTP_201_CREATED,
)
def inject_event(request: InjectEventRequest, db: Session = Depends(get_db)):
    """Inject a realtime behavioral event for live demo"""
    sim = RealTimeSimulator(db)
    event_data = sim.generate_realtime_event(request.user_hash, request.current_risk)

    event = Event(**event_data)
    db.add(event)
    db.commit()

    safety = SafetyValve(db)
    result = safety.analyze(request.user_hash)

    # Convert for API response (schema expects 'metadata' as str, 'timestamp' as ISO string)
    response_event = {
        "user_hash": event_data["user_hash"],
        "timestamp": event_data["timestamp"].isoformat(),
        "event_type": event_data["event_type"],
        "metadata": event_data.get("metadata_", {}),
    }

    return RealtimeInjectionResponse(
        success=True, data={"new_event": response_event, "updated_risk": result}
    )


@router.get("/events", response_model=ActivityEventResponse)
def get_recent_events(limit: int = 50, db: Session = Depends(get_db)):
    """Get recent activity stream for all users"""
    events = db.query(Event).order_by(Event.timestamp.desc()).limit(limit).all()

    data = []
    for e in events:
        desc = f"Event {e.event_type}"
        risk = "neutral"
        # Try to extract from metadata if available
        if e.metadata_:
            if isinstance(e.metadata_, dict):
                if "description" in e.metadata_:
                    desc = e.metadata_["description"]
                if "risk_impact" in e.metadata_:
                    risk = e.metadata_["risk_impact"]

        data.append(
            {
                "user_hash": e.user_hash,
                "timestamp": e.timestamp.isoformat(),
                "event_type": e.event_type or "unknown",
                "metadata": e.metadata_ or {},
                "description": desc,
                "risk_impact": risk,
            }
        )
    return ActivityEventResponse(success=True, data=data)


# ============ USER LISTING ============


@router.get("/users", response_model=UserListResponse)
def list_users(db: Session = Depends(get_db)):
    """List all users with their current risk scores"""
    from app.models.analytics import RiskScore
    from app.models.identity import UserIdentity

    users = db.query(UserIdentity).all()
    result = []
    for user in users:
        # Attempt to derive name from encrypted email
        name = f"User {user.user_hash[:4]}"
        role = "Engineer"
        try:
            # Try proper decryption
            decrypted = privacy.decrypt(user.email_encrypted)
            name = decrypted.split("@")[0].title()
        except:
            # Handle mock seeded data (fallback)
            try:
                raw = user.email_encrypted.decode()
                if "encrypted_" in raw:
                    name = raw.replace("encrypted_", "").split("@")[0].title()
            except:
                pass

        if "Alex" in name:
            role = "Senior Engineer"
        if "Sarah" in name:
            role = "Tech Lead"

        risk = db.query(RiskScore).filter_by(user_hash=user.user_hash).first()
        result.append(
            {
                "user_hash": user.user_hash,
                "name": name,
                "role": role,
                "risk_level": risk.risk_level if risk else "CALIBRATING",
                "velocity": risk.velocity if risk else 0.0,
                "confidence": risk.confidence if risk else 0.0,
                "updated_at": risk.updated_at.isoformat()
                if risk and risk.updated_at
                else None,
            }
        )
    return UserListResponse(success=True, data=result)


# ============ RISK HISTORY ============


@router.get("/users/{user_hash}/history", response_model=RiskHistoryResponse)
def get_risk_history(
    user_hash: str,
    days: int = 30,
    db: Session = Depends(get_db),
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """Get risk score history for timeline charts"""
    # RBAC Check: Verify user has permission to access this data
    check_user_data_access(db, current_user, user_hash)

    from app.models.analytics import RiskHistory

    cutoff = datetime.utcnow() - timedelta(days=days)
    history = (
        db.query(RiskHistory)
        .filter(RiskHistory.user_hash == user_hash, RiskHistory.timestamp >= cutoff)
        .order_by(RiskHistory.timestamp.asc())
        .all()
    )

    result = [
        {
            "timestamp": h.timestamp.isoformat(),
            "risk_level": h.risk_level,
            "velocity": h.velocity,
            "confidence": h.confidence,
            "belongingness_score": h.belongingness_score or 0.0,
        }
        for h in history
    ]

    return RiskHistoryResponse(success=True, data=result)


# ============ NUDGE ENDPOINT ============


@router.get("/users/{user_hash}/nudge", response_model=NudgeResponse)
def get_nudge(
    user_hash: str,
    db: Session = Depends(get_db),
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """Get a context-aware nudge recommendation for a user"""
    # RBAC Check: Verify user has permission to access this data
    check_user_data_access(db, current_user, user_hash)

    from app.models.analytics import RiskScore

    risk = db.query(RiskScore).filter_by(user_hash=user_hash).first()
    if not risk:
        # Trigger analysis if missing (e.g. fresh persona)
        SafetyValve(db).analyze(user_hash)
        risk = db.query(RiskScore).filter_by(user_hash=user_hash).first()

    if not risk:
        raise HTTPException(status_code=404, detail="No risk data found for user")

    # Generate nudge based on risk level
    if risk.risk_level == "CRITICAL":
        nudge = {
            "user_hash": user_hash,
            "nudge_type": "urgent_wellbeing",
            "message": "Your workload patterns suggest high stress levels. Consider taking a break or speaking with your manager about workload redistribution.",
            "risk_level": risk.risk_level,
            "actions": [
                {"label": "Schedule 1:1", "action": "schedule_meeting"},
                {"label": "Take Break", "action": "suggest_break"},
                {"label": "Dismiss", "action": "dismiss"},
            ],
        }
    elif risk.risk_level == "ELEVATED":
        nudge = {
            "user_hash": user_hash,
            "nudge_type": "gentle_reminder",
            "message": "We've noticed some changes in your work patterns. Remember to maintain work-life balance and take regular breaks.",
            "risk_level": risk.risk_level,
            "actions": [
                {"label": "View Insights", "action": "view_insights"},
                {"label": "Dismiss", "action": "dismiss"},
            ],
        }
    else:
        nudge = {
            "user_hash": user_hash,
            "nudge_type": "positive_reinforcement",
            "message": "Great job maintaining healthy work patterns! Keep up the good balance.",
            "risk_level": risk.risk_level,
            "actions": [
                {"label": "View Dashboard", "action": "view_dashboard"},
            ],
        }

    return NudgeResponse(success=True, data=nudge)


# ============ HYBRID DATA INGESTION ============


@router.post("/ingest/demo", response_model=SimulationResponse)
async def ingest_demo_data(
    email: str,
    persona_type: str = "jordan_steady",
    days: int = 30,
    db: Session = Depends(get_db),
):
    """
    Ingest simulated demo data for a user.

    Args:
        email: User email address
        persona_type: One of 'alex_burnout', 'sarah_gem', 'jordan_steady', 'maria_contagion'
        days: Days of history to generate

    Returns:
        Ingestion summary
    """
    from app.services.ingestion import QuickIngestor
    from app.services.safety_valve import SafetyValve

    result = await QuickIngestor.demo_user(db, email, persona_type, days)

    # Trigger analysis for the user
    if result["events_ingested"] > 0:
        user_hash = result["user_hash"]
        SafetyValve(db).analyze_and_notify(user_hash)

    return SimulationResponse(
        success=len(result["errors"]) == 0,
        data={
            "email": email,
            "user_hash": result["user_hash"],
            "events_ingested": result["events_ingested"],
            "sources_processed": result["sources_processed"],
            "errors": result["errors"],
        },
    )


@router.post("/ingest/team-demo", response_model=SimulationResponse)
async def ingest_team_demo(
    scenario: str = "burnout_crisis", days: int = 30, db: Session = Depends(get_db)
):
    """
    Ingest demo data for an entire team scenario.

    Args:
        scenario: One of 'burnout_crisis', 'team_contagion', 'healthy_team'
        days: Days of history to generate

    Returns:
        Team ingestion summary
    """
    from app.services.ingestion import seed_demo_data, DEMO_SCENARIOS
    from app.services.talent_scout import TalentScout

    if scenario not in DEMO_SCENARIOS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scenario. Use one of: {list(DEMO_SCENARIOS.keys())}",
        )

    result = await seed_demo_data(db, scenario)

    # Run network analysis after team ingestion
    TalentScout(db).analyze_network()

    total_events = sum(user["events_ingested"] for user in result["users"])

    return SimulationResponse(
        success=True,
        data={
            "scenario": scenario,
            "team_size": result["team_size"],
            "total_events": total_events,
            "users": [user["user_hash"] for user in result["users"]],
        },
    )


@router.post("/ingest/production")
async def ingest_production_data(
    email: str,
    slack_token: Optional[str] = None,
    github_token: Optional[str] = None,
    days: int = 30,
    db: Session = Depends(get_db),
):
    """
    Ingest production data with simulation fallback.

    This endpoint attempts to connect to real integrations (Slack, GitHub).
    If integrations are unavailable or unconfigured, it falls back to simulation.

    Args:
        email: User email address
        slack_token: Slack bot token (optional)
        github_token: GitHub personal access token (optional)
        days: Days of history to fetch

    Returns:
        Ingestion summary showing which sources were used
    """
    from app.services.ingestion import QuickIngestor
    from app.services.safety_valve import SafetyValve

    try:
        result = await QuickIngestor.production_user(
            db, email, slack_token, github_token, days
        )

        # Trigger analysis
        if result["events_ingested"] > 0:
            SafetyValve(db).analyze_and_notify(result["user_hash"])

        return {
            "success": len(result["errors"]) == 0,
            "data": {
                "email": email,
                "user_hash": result["user_hash"],
                "events_ingested": result["events_ingested"],
                "sources": result["source_details"],
                "using_fallback": any(
                    s.get("status") == "success" and s["type"] == "simulation"
                    for s in result["source_details"]
                ),
                "errors": result["errors"] if result["errors"] else None,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/data-sources/health")
async def check_data_sources_health(
    slack_token: Optional[str] = None, github_token: Optional[str] = None
):
    """
    Check health of available data sources.

    Args:
        slack_token: Slack bot token to test (optional)
        github_token: GitHub token to test (optional)

    Returns:
        Health status of each data source
    """
    from app.services.data_sources import (
        DataSourceFactory,
        DataSourceType,
        SimulationSource,
    )

    health_checks = []

    # Always check simulation (should always be healthy)
    sim_source = SimulationSource()
    sim_health = await sim_source.health_check()
    health_checks.append(sim_health)

    # Check Slack if token provided
    if slack_token:
        try:
            slack_source = DataSourceFactory.create_source(
                DataSourceType.SLACK, {"bot_token": slack_token}
            )
            slack_health = await slack_source.health_check()
            health_checks.append(slack_health)
        except Exception as e:
            health_checks.append(
                {"type": "slack", "status": "error", "message": str(e)}
            )

    # Check GitHub if token provided
    if github_token:
        try:
            github_source = DataSourceFactory.create_source(
                DataSourceType.GITHUB, {"access_token": github_token}
            )
            github_health = await github_source.health_check()
            health_checks.append(github_health)
        except Exception as e:
            health_checks.append(
                {"type": "github", "status": "error", "message": str(e)}
            )

    return {
        "success": True,
        "data": {
            "sources": health_checks,
            "can_use_real_data": any(
                h["type"] != "simulation" and h.get("connected") for h in health_checks
            ),
        },
    }
