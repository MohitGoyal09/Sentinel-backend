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
    from app.api.deps import get_current_user
    from app.api.deps import get_optional_user as get_current_user_identity

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
    """Analyze network centrality for a specific user"""

    # RBAC Check: Verify user has permission to access this data
    check_user_data_access(db, current_user, user_hash)

    engine = TalentScout(db)
    result = engine.analyze(user_hash)
    return TalentScoutResponse(success=True, data=result)


@router.post("/teams/culture", response_model=CultureThermometerResponse)
def analyze_team_culture(
    request: AnalyzeTeamRequest,
    db: Session = Depends(get_db),
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """Analyze culture and contagion risk for a team"""

    team_hashes = request.team_hashes

    if not team_hashes:
        # Analyze all users if no specific team provided
        from app.models.identity import UserIdentity

        users = db.query(UserIdentity).all()
        team_hashes = [u.user_hash for u in users]

    engine = CultureThermometer(db)
    result = engine.analyze_team(team_hashes)
    return CultureThermometerResponse(success=True, data=result)


@router.get("/users/{user_hash}/nudge", response_model=NudgeResponse)
def get_nudge(
    user_hash: str,
    db: Session = Depends(get_db),
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """Generate a personalized nudge for a user based on their risk profile"""
    # RBAC Check: Verify user has permission to access this data
    check_user_data_access(db, current_user, user_hash)

    engine = SafetyValve(db)
    result = engine.suggest_nudge(user_hash)
    return NudgeResponse(success=True, data=result)


@router.get("/events", response_model=ActivityEventResponse)
def list_events(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """Get recent activity stream"""
    events = db.query(Event).order_by(Event.timestamp.desc()).offset(offset).limit(limit).all()
    return ActivityEventResponse(
        success=True, data=[e.to_dict() for e in events], count=len(events)
    )


# ============ PAGINATED USERS LIST ============


@router.get("/users", response_model=UserListResponse)
def list_users(
    db: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 50,
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """
    List all users with their current risk scores (paginated)
    
    Query params:
    - skip: Number of records to skip (pagination offset)
    - limit: Maximum number of records to return (default: 50)
    """
    from app.models.analytics import RiskScore
    from app.models.identity import UserIdentity
    from sqlalchemy import desc
    
    # Use efficient pagination with JOIN in a single query
    # Get users with their latest risk scores in one query using subquery
    latest_risk = db.query(
        RiskScore.user_hash,
        RiskScore.risk_level,
        RiskScore.velocity,
        RiskScore.confidence,
        RiskScore.updated_at
    ).distinct(RiskScore.user_hash).order_by(
        RiskScore.user_hash, desc(RiskScore.updated_at)
    ).subquery()
    
    # Join with users
    users = db.query(
        UserIdentity.user_hash,
        UserIdentity.email_encrypted,
        latest_risk.c.risk_level,
        latest_risk.c.velocity,
        latest_risk.c.confidence,
        latest_risk.c.updated_at
    ).outerjoin(
        latest_risk,
        UserIdentity.user_hash == latest_risk.c.user_hash
    ).offset(skip).limit(limit).all()
    
    result = []
    for user in users:
        user_hash, email_encrypted, risk_level, velocity, confidence, updated_at = user
        
        # Attempt to derive name from encrypted email
        name = f"User {user_hash[:4]}"
        role = "Engineer"
        try:
            # Try proper decryption
            decrypted = privacy.decrypt(email_encrypted)
            name = decrypted.split("@")[0].title()
        except Exception:
            # Handle mock seeded data (fallback)
            try:
                raw = email_encrypted.decode()
                if "encrypted_" in raw:
                    name = raw.replace("encrypted_", "").split("@")[0].title()
            except Exception:
                pass
        
        if "Alex" in name:
            role = "Senior Engineer"
        if "Sarah" in name:
            role = "Tech Lead"
        
        result.append(
            {
                "user_hash": user_hash,
                "name": name,
                "role": role,
                "risk_level": risk_level or "CALIBRATING",
                "velocity": velocity or 0.0,
                "confidence": confidence or 0.0,
                "updated_at": updated_at.isoformat() if updated_at else None,
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
    """Get historical risk scores for a user"""
    from app.models.analytics import RiskScore

    # RBAC Check
    check_user_data_access(db, current_user, user_hash)

    cutoff = datetime.utcnow() - timedelta(days=days)
    history = (
        db.query(RiskScore)
        .filter(RiskScore.user_hash == user_hash)
        .filter(RiskScore.updated_at >= cutoff)
        .order_by(RiskScore.updated_at.asc())
        .all()
    )

    return RiskHistoryResponse(
        success=True,
        data={
            "user_hash": user_hash,
            "history": [
                {
                    "timestamp": r.updated_at.isoformat(),
                    "risk_level": r.risk_level,
                    "velocity": r.velocity,
                }
                for r in history
            ],
        },
    )


# ============ REAL-TIME EVENTS ============


@router.post("/events/inject", response_model=RealtimeInjectionResponse)
def inject_event(
    request: InjectEventRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Inject a real-time event for demo purposes"""
    sim = RealTimeSimulator(db)
    vault = VaultManager(db, db)

    # Ensure user exists in our system
    try:
        user_hash = vault.store_identity(request.user_email)
    except Exception:
        # Fallback for demo users
        user_hash = f"demo_{request.user_email.split('@')[0]}"

    # Inject event
    event = sim._create_synthetic_event(
        user_hash=user_hash,
        event_type=request.event_type,
        metadata=request.metadata or {},
    )
    db.add(event)
    db.commit()

    # Trigger background analysis
    background_tasks.add_task(run_all_engines, user_hash)

    return RealtimeInjectionResponse(
        success=True,
        data={
            "event_id": event.id,
            "user_hash": user_hash,
            "event_type": request.event_type,
        },
    )


@router.get("/network/global/talent")
def get_global_talent(
    db: Session = Depends(get_db),
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """Get global talent network analysis"""
    engine = TalentScout(db)
    return {"success": True, "data": engine.analyze_network()}


@router.get("/global/network")
def get_global_network(
    db: Session = Depends(get_db),
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """Get global network metrics"""
    engine = TalentScout(db)
    return {"success": True, "data": engine.get_network_metrics()}


# ============ DASHBOARD AGGREGATES ============


@router.get("/dashboard/summary")
def get_dashboard_summary(
    db: Session = Depends(get_db),
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """Get summary metrics for dashboard"""
    from app.models.analytics import RiskScore
    from sqlalchemy import func

    total_users = db.query(func.count(UserIdentity.user_hash)).scalar() or 0

    # Count risk levels
    risk_counts = dict(
        db.query(RiskScore.risk_level, func.count(RiskScore.id))
        .group_by(RiskScore.risk_level)
        .all()
    )

    # Avg velocity
    avg_velocity = db.query(func.avg(RiskScore.velocity)).scalar() or 0.0

    result = {
        "total_users": total_users,
        "risk_distribution": {
            "critical": risk_counts.get("CRITICAL", 0),
            "elevated": risk_counts.get("ELEVATED", 0),
            "low": risk_counts.get("LOW", 0),
            "calibrating": risk_counts.get("CALIBRATING", 0),
        },
        "avg_velocity": round(float(avg_velocity), 2),
        "total_events": sum(user["events_ingested"] for user in result["users"])
        if "users" in locals()
        else 0,
    }

    return {"success": True, "data": result}
