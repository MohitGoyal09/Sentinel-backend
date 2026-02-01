from pydantic import BaseModel, EmailStr
from typing import List, Optional, Dict, Any

# Request Models
class CreatePersonaRequest(BaseModel):
    email: EmailStr
    persona_type: str  # alex_burnout, sarah_gem, jordan_steady

class InjectEventRequest(BaseModel):
    user_hash: str
    current_risk: str

class AnalyzeTeamRequest(BaseModel):
    team_hashes: List[str]

# Response Models (API Envelope Pattern)
class APIResponse(BaseModel):
    success: bool = True
    data: Optional[Any] = None
    error: Optional[str] = None

class SafetyValveData(BaseModel):
    engine: str
    risk_level: str
    velocity: float
    confidence: float
    belongingness_score: float
    circadian_entropy: float
    indicators: Dict[str, bool]
    status: Optional[str] = "ACTIVE"
    days_collected: Optional[int] = 0

class TalentScoutPerformer(BaseModel):
    user_hash: str
    betweenness: float
    eigenvector: float
    unblocking: int
    is_hidden_gem: bool

class TalentScoutData(BaseModel):
    engine: str
    top_performers: List[TalentScoutPerformer]

class CultureThermometerMetrics(BaseModel):
    avg_velocity: float
    critical_members: int
    graph_fragmentation: float
    comm_decay_rate: float

class CultureThermometerData(BaseModel):
    engine: str
    team_risk: str
    metrics: CultureThermometerMetrics
    recommendation: str

class SimulationData(BaseModel):
    user_hash: str
    events_count: int
    persona: str

class EventData(BaseModel):
    user_hash: str
    timestamp: str
    event_type: str
    metadata: Dict[str, Any]

class RealtimeEventData(BaseModel):
    new_event: EventData
    updated_risk: SafetyValveData

# Typed API Responses
class SafetyValveResponse(APIResponse):
    data: Optional[SafetyValveData] = None

class TalentScoutResponse(APIResponse):
    data: Optional[TalentScoutData] = None

class CultureThermometerResponse(APIResponse):
    data: Optional[CultureThermometerData] = None

class SimulationResponse(APIResponse):
    data: Optional[SimulationData] = None

class RealtimeInjectionResponse(APIResponse):
    data: Optional[RealtimeEventData] = None
