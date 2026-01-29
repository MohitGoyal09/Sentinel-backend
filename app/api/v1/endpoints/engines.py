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
    CultureThermometerResponse, RealtimeInjectionResponse
)
from datetime import datetime
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
