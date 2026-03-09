"""Base connector interface and shared types for the ingestion pipeline."""

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel


class ConnectorStatus(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    PENDING = "pending"


class NormalizedEvent(BaseModel):
    """Unified event format that all connectors produce."""
    source: str  # e.g. "git", "slack", "jira", "calendar", "csv"
    event_type: str  # e.g. "commit", "message", "ticket_closed"
    user_identifier: str  # email or username (will be hashed by privacy layer)
    timestamp: datetime
    metadata: dict = {}
    risk_signal: Optional[str] = None  # "positive", "negative", "neutral"


class ConnectorHealth(BaseModel):
    name: str
    status: ConnectorStatus
    events_ingested: int = 0
    last_sync: Optional[datetime] = None
    error_message: Optional[str] = None
    latency_ms: Optional[float] = None


class BaseConnector(ABC):
    """Abstract base for all data source connectors."""

    def __init__(self, name: str):
        self.name = name
        self._status = ConnectorStatus.DISCONNECTED
        self._events_ingested = 0
        self._last_sync: Optional[datetime] = None
        self._error: Optional[str] = None

    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to the data source."""
        ...

    @abstractmethod
    async def fetch_events(self, since: Optional[datetime] = None) -> list[NormalizedEvent]:
        """Pull new events from the data source."""
        ...

    def health(self) -> ConnectorHealth:
        return ConnectorHealth(
            name=self.name,
            status=self._status,
            events_ingested=self._events_ingested,
            last_sync=self._last_sync,
            error_message=self._error,
        )
