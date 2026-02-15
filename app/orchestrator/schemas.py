# Orchestrator Schemas
# Pydantic models for orchestration API

from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from enum import Enum
from datetime import datetime


class AgentType(str, Enum):
    SAFETY_VALVE = "safety_valve"
    TALENT_SCOUT = "talent_scout"
    CULTURE_THERMOMETER = "culture_thermometer"
    LLM_CONTEXT = "llm_context"
    NU_DISPATCHER = "nudge_dispatcher"


class OrchestrationRequest(BaseModel):
    """Request to execute multiple agents in parallel."""
    task_group_id: Optional[str] = None
    tasks: List["OrchestrationTask"]
    aggregation_strategy: str = "hierarchical"
    timeout_ms: int = Field(default=30000, le=120000)


class OrchestrationTask(BaseModel):
    """Individual task to execute on an agent."""
    task_id: str
    agent_id: str
    payload: Dict[str, Any]
    priority: int = Field(default=2, ge=1, le=3)
    dependencies: List[str] = Field(default_factory=list)


class OrchestrationResponse(BaseModel):
    """Response from orchestration execution."""
    task_group_id: str
    aggregated_result: "AggregatedResult"
    individual_results: Dict[str, Any]
    execution_time_ms: int


class AggregatedResult(BaseModel):
    """Aggregated result from multiple agents."""
    primary_results: Dict[str, Any]
    aggregated_insights: Dict[str, Any]
    confidence_score: float
    execution_time_ms: int
    warnings: List[str]
    errors: List[str]


class AgentInfo(BaseModel):
    """Information about an available agent."""
    agent_id: str
    name: str
    agent_type: AgentType
    capabilities: List[str]
    status: str
    max_concurrent_tasks: int
    timeout_seconds: int


class AgentListResponse(BaseModel):
    """Response listing all available agents."""
    agents: List[AgentInfo]
    total_count: int


class HealthResponse(BaseModel):
    """Health check response for an agent."""
    status: str
    agent_id: str
    timestamp: datetime
    uptime_seconds: Optional[int] = None


class TaskStatusResponse(BaseModel):
    """Status of a specific task."""
    task_id: str
    status: str  # pending, running, completed, failed
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    execution_time_ms: Optional[int] = None


# Update forward references
OrchestrationRequest.model_rebuild()
