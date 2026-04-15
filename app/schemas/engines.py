from pydantic import BaseModel, EmailStr, Field
from typing import List, Optional, Dict, Any


# Request Models
class CreatePersonaRequest(BaseModel):
    email: EmailStr
    persona_type: str  # alex_burnout, sarah_gem, jordan_steady


class InjectEventRequest(BaseModel):
    user_hash: str
    current_risk: str
    event_type: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class AnalyzeTeamRequest(BaseModel):
    team_hashes: List[str] = Field(max_length=500)


class ForecastRequest(BaseModel):
    team_hashes: List[str] = Field(max_length=500)
    days: int = Field(default=30, ge=1, le=365)


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
    attrition_probability: Optional[float] = 0.0
    sentiment_score: Optional[float] = None
    sentiment_available: Optional[bool] = False
    indicators: Dict[str, bool]
    status: Optional[str] = "ACTIVE"
    days_collected: Optional[int] = 0
    events_analyzed: Optional[int] = None
    analysis_window_days: Optional[int] = None
    sources_used: Optional[List[str]] = None
    source_count: Optional[int] = None
    explained_events_filtered: Optional[int] = None
    unexplained_events_count: Optional[int] = None


class TalentScoutPerformer(BaseModel):
    user_hash: str
    betweenness: float
    eigenvector: float
    unblocking: int
    is_hidden_gem: bool


class GraphNode(BaseModel):
    id: str
    label: str
    risk_level: Optional[str] = "LOW"
    betweenness: Optional[float] = 0.0
    eigenvector: Optional[float] = 0.0
    unblocking_count: Optional[int] = 0
    is_hidden_gem: Optional[bool] = False
    x: Optional[float] = 0.0
    y: Optional[float] = 0.0


class GraphEdge(BaseModel):
    source: str
    target: str
    weight: float
    edge_type: str = "collaboration"


class TalentScoutData(BaseModel):
    engine: str
    top_performers: List[TalentScoutPerformer]
    nodes: Optional[List[GraphNode]] = []
    edges: Optional[List[GraphEdge]] = []


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
    description: Optional[str] = None
    risk_impact: Optional[str] = "neutral"


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


class UserSummary(BaseModel):
    user_hash: str
    name: Optional[str] = None
    role: Optional[str] = "Engineer"
    risk_level: Optional[str] = None
    velocity: Optional[float] = None
    confidence: Optional[float] = None
    updated_at: Optional[str] = None
    sentiment_score: Optional[float] = None
    sentiment_available: Optional[bool] = False


class UserListResponse(APIResponse):
    data: Optional[List[UserSummary]] = None


class RiskHistoryEntry(BaseModel):
    timestamp: str
    risk_level: str
    velocity: float


class RiskHistoryData(BaseModel):
    user_hash: str
    history: List[RiskHistoryEntry]


class RiskHistoryResponse(APIResponse):
    data: Optional[RiskHistoryData] = None


class ActivityEventResponse(APIResponse):
    data: Optional[List[EventData]] = None


class NudgeAction(BaseModel):
    label: str
    action: str


class NudgeData(BaseModel):
    user_hash: str
    nudge_type: str
    message: str
    risk_level: str
    actions: List[NudgeAction]


class NudgeResponse(APIResponse):
    data: Optional[NudgeData] = None
