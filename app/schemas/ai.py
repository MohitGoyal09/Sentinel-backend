"""
AI Chat and NLP Schema Models

This module contains Pydantic models for AI-powered endpoints including
role-aware chat, semantic queries, and narrative reports.
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any


# Chat Models
class ChatMessage(BaseModel):
    """Individual message in a conversation"""

    role: str = Field(
        ..., description="Role of the message sender: user, assistant, or system"
    )
    content: str = Field(..., description="Message content")
    timestamp: Optional[str] = None


class ChatRequest(BaseModel):
    """Request model for AI chat endpoint"""

    message: str = Field(
        ..., description="User's message to the AI", min_length=1, max_length=4000
    )
    conversation_id: Optional[str] = Field(
        None, description="Optional conversation ID for context continuity"
    )
    context: Optional[Dict[str, Any]] = Field(
        None, description="Additional context data"
    )


class ChatContextUsed(BaseModel):
    """Context data used in generating the response"""

    risk_level: Optional[str] = None
    velocity: Optional[float] = None
    belongingness: Optional[float] = None
    team_size: Optional[int] = None
    org_total_users: Optional[int] = None


class ChatResponse(BaseModel):
    """Response model for AI chat endpoint"""

    response: str = Field(..., description="AI's response message")
    role: str = Field(..., description="User's role that shaped the response")
    conversation_id: Optional[str] = Field(
        None, description="Conversation ID for continuity"
    )
    context_used: ChatContextUsed = Field(
        ..., description="Context data that informed the response"
    )
    generated_at: str = Field(..., description="ISO timestamp of response generation")


class ChatConversation(BaseModel):
    """Full conversation with history"""

    conversation_id: str
    messages: List[ChatMessage]
    created_at: str
    updated_at: str


# System Prompt Configuration
class SystemPromptConfig(BaseModel):
    """Configuration for role-based system prompts"""

    role: str = Field(
        ..., description="Role this prompt applies to: employee, manager, or admin"
    )
    prompt: str = Field(..., description="System prompt text")
    focus_areas: List[str] = Field(
        default_factory=list, description="Key focus areas for this role"
    )
    guidelines: List[str] = Field(
        default_factory=list, description="Response guidelines"
    )
    tone: str = Field("professional", description="Expected tone of responses")


# Query Models (existing patterns)
class QueryRequest(BaseModel):
    """Request for semantic query over employee data"""

    query: str = Field(..., description="Natural language query")


class QueryResult(BaseModel):
    """Individual result from semantic query"""

    user_hash: str
    name: str
    risk_level: Optional[str] = None
    velocity: Optional[float] = None
    betweenness: Optional[float] = None
    eigenvector: Optional[float] = None
    skills: List[str] = []
    tenure_months: Optional[int] = None
    insights: List[str] = []
    suggested_action: Optional[str] = None


class QueryResponse(BaseModel):
    """Response from semantic query"""

    query: str
    response: str
    results: List[QueryResult]
    query_type: str

    @property
    def summary(self) -> str:
        """Alias for response field (frontend compatibility)"""
        return self.response


# Agenda/Copilot Models
class AgendaRequest(BaseModel):
    """Request for generating 1:1 agenda"""

    user_hash: str = Field(..., description="Target user for agenda generation")


class TalkingPoint(BaseModel):
    """Individual talking point for 1:1"""

    text: str
    type: str = Field(..., description="Type: supportive, question, or action")


class SuggestedAction(BaseModel):
    """Suggested action for manager"""

    label: str
    action: str


class AgendaResponse(BaseModel):
    """Response with generated 1:1 agenda"""

    user_hash: str
    risk_level: str
    talking_points: List[TalkingPoint]
    suggested_actions: List[SuggestedAction]
    generated_at: str


# Narrative Report Models
class NarrativeReportResponse(BaseModel):
    """Individual risk narrative report"""

    user_hash: str
    narrative: str
    trend: str
    key_insights: List[str]
    generated_at: str


class TeamReportResponse(BaseModel):
    """Team health narrative report"""

    team_id: str
    narrative: str
    trend: str
    key_insights: List[str]
    member_count: int
    at_risk_count: int
    generated_at: str


# Role Context Models
class EmployeeContext(BaseModel):
    """Context data for employee role"""

    risk_level: str
    velocity: float
    belongingness: float
    betweenness: float
    unblocking_count: int
    recent_trend: Optional[str] = None


class ManagerContext(BaseModel):
    """Context data for manager role"""

    personal_risk_level: str
    team_size: int
    at_risk_count: int
    critical_count: int
    team_velocity_avg: Optional[float] = None


class AdminContext(BaseModel):
    """Context data for admin role"""

    personal_risk_level: str
    org_total_users: int
    org_at_risk_count: int
    org_critical_count: int
    org_risk_percentage: float
    department_breakdown: Optional[Dict[str, Any]] = None


# Export all models
__all__ = [
    # Chat
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "ChatContextUsed",
    "ChatConversation",
    "SystemPromptConfig",
    # Query
    "QueryRequest",
    "QueryResult",
    "QueryResponse",
    # Agenda
    "AgendaRequest",
    "TalkingPoint",
    "SuggestedAction",
    "AgendaResponse",
    # Reports
    "NarrativeReportResponse",
    "TeamReportResponse",
    # Context
    "EmployeeContext",
    "ManagerContext",
    "AdminContext",
]
