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
    CreatePersonaRequest, InjectEventRequest, AnalyzeTeamRequest,
    SimulationResponse, SafetyValveResponse, TalentScoutResponse,
    CultureThermometerResponse, RealtimeInjectionResponse,
    UserListResponse, RiskHistoryResponse, NudgeResponse,
    ActivityEventResponse
)
from datetime import datetime, timedelta
from app.services.context import ContextEnricher
from typing import Optional
from app.models.identity import UserIdentity
from app.core.security import privacy

router = APIRouter()

# Background task wrapper
def run_all_engines(user_hash: str):
    with SessionLocal() as db:
        SafetyValve(db).analyze(user_hash)
        TalentScout(db).analyze_network()


# ============ SIMULATION / PERSONAS ============

@router.post("/personas", response_model=SimulationResponse, status_code=status.HTTP_201_CREATED)
def create_persona(request: CreatePersonaRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
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
        data={"user_hash": user_hash, "events_count": len(events), "persona": request.persona_type}
    )


# ============ ENGINE ANALYSIS ============



@router.get("/users/{user_hash}/context")
async def check_user_context(user_hash: str, timestamp: str = None, db: Session = Depends(get_db)):
    """Check contextual explanation for a specific timestamp"""
    
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
        "data": {
            "user_hash": user_hash,
            "timestamp": check_time,
            "context": context
        }
    }

@router.get("/users/{user_hash}/safety", response_model=SafetyValveResponse)
def analyze_user_safety(user_hash: str, db: Session = Depends(get_db)):
    """Analyze burnout risk for a specific user"""
    engine = SafetyValve(db)
    result = engine.analyze(user_hash)
    return SafetyValveResponse(success=True, data=result)


@router.get("/users/{user_hash}/talent", response_model=TalentScoutResponse)
def analyze_user_network(user_hash: str, db: Session = Depends(get_db)):
    """Analyze network centrality and hidden gem potential"""
    engine = TalentScout(db)
    result = engine.analyze_network()
    return TalentScoutResponse(success=True, data=result)


@router.post("/teams/culture", response_model=CultureThermometerResponse)
def analyze_team_culture(request: AnalyzeTeamRequest, db: Session = Depends(get_db)):
    """Analyze team-level contagion risk"""
    engine = CultureThermometer(db)
    result = engine.analyze_team(request.team_hashes)
    return CultureThermometerResponse(success=True, data=result)


# ============ REALTIME EVENTS ============

@router.post("/events", response_model=RealtimeInjectionResponse, status_code=status.HTTP_201_CREATED)
def inject_event(request: InjectEventRequest, db: Session = Depends(get_db)):
    """Inject a realtime behavioral event for live demo"""
    sim = RealTimeSimulator(db)
    event_data = sim.generate_realtime_event(request.user_hash, request.current_risk)
    
    event = Event(**event_data)
    db.add(event)
    db.commit()
    
    safety = SafetyValve(db)
    result = safety.analyze(request.user_hash)
    
    return RealtimeInjectionResponse(
        success=True,
        data={"new_event": event_data, "updated_risk": result}
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
        
        data.append({
            "user_hash": e.user_hash,
            "timestamp": e.timestamp.isoformat(),
            "event_type": e.event_type or "unknown",
            "metadata": e.metadata_ or {},
            "description": desc,
            "risk_impact": risk
        })
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
            name = decrypted.split('@')[0].title()
        except:
            # Handle mock seeded data (fallback)
            try:
                raw = user.email_encrypted.decode()
                if "encrypted_" in raw:
                    name = raw.replace("encrypted_", "").split("@")[0].title()
            except:
                pass
        
        if "Alex" in name: role = "Senior Engineer"
        if "Sarah" in name: role = "Tech Lead"

        risk = db.query(RiskScore).filter_by(user_hash=user.user_hash).first()
        result.append({
            "user_hash": user.user_hash,
            "name": name,
            "role": role,
            "risk_level": risk.risk_level if risk else "CALIBRATING",
            "velocity": risk.velocity if risk else 0.0,
            "confidence": risk.confidence if risk else 0.0,
            "updated_at": risk.updated_at.isoformat() if risk and risk.updated_at else None,
        })
    return UserListResponse(success=True, data=result)


# ============ RISK HISTORY ============

@router.get("/users/{user_hash}/history", response_model=RiskHistoryResponse)
def get_risk_history(user_hash: str, days: int = 30, db: Session = Depends(get_db)):
    """Get risk score history for timeline charts"""
    from app.models.analytics import RiskHistory

    cutoff = datetime.utcnow() - timedelta(days=days)
    history = db.query(RiskHistory).filter(
        RiskHistory.user_hash == user_hash,
        RiskHistory.timestamp >= cutoff
    ).order_by(RiskHistory.timestamp.asc()).all()

    result = [{
        "timestamp": h.timestamp.isoformat(),
        "risk_level": h.risk_level,
        "velocity": h.velocity,
        "confidence": h.confidence,
        "belongingness_score": h.belongingness_score or 0.0,
    } for h in history]

    return RiskHistoryResponse(success=True, data=result)


# ============ NUDGE ENDPOINT ============

@router.get("/users/{user_hash}/nudge", response_model=NudgeResponse)
def get_nudge(user_hash: str, db: Session = Depends(get_db)):
    """Get a context-aware nudge recommendation for a user"""
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
            ]
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
            ]
        }
    else:
        nudge = {
            "user_hash": user_hash,
            "nudge_type": "positive_reinforcement",
            "message": "Great job maintaining healthy work patterns! Keep up the good balance.",
            "risk_level": risk.risk_level,
            "actions": [
                {"label": "View Dashboard", "action": "view_dashboard"},
            ]
        }

    return NudgeResponse(success=True, data=nudge)
