from sqlalchemy import BigInteger, Column, String, Float, DateTime, Integer, JSON, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base
from datetime import datetime

Base = declarative_base()


class Event(Base):
    """Raw behavioral events (Vault A)"""

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_user_timestamp", "user_hash", "timestamp"),
        Index("ix_events_type", "event_type"),
        Index("ix_events_tenant", "tenant_id"),
        {"schema": "analytics"},
    )

    id = Column(BigInteger, primary_key=True)
    user_hash = Column(String(64), index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    event_type = Column(String(50))  # commit, pr_review, slack_message, unblocked
    target_user_hash = Column(String(64), nullable=True)  # For graph edges
    metadata_ = Column(
        "metadata", JSON
    )  # Renamed to avoid reserved keyword conflict if any, but mapping to "metadata" column

    def to_dict(self):
        return {
            "user_hash": self.user_hash,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "event_type": self.event_type,
            "metadata": self.metadata_,
        }


class RiskScore(Base):
    """Safety Valve outputs"""

    __tablename__ = "risk_scores"
    __table_args__ = (
        Index("ix_risk_scores_level", "risk_level"),
        {"schema": "analytics"},
    )

    user_hash = Column(String(64), primary_key=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    velocity = Column(Float)
    risk_level = Column(String(20))
    confidence = Column(Float)
    thwarted_belongingness = Column(Float)  # Psychological metric (IPT theory)
    updated_at = Column(DateTime, default=datetime.utcnow)

    @property
    def belongingness_score(self) -> float:
        """Alias for consistent naming across models."""
        return self.thwarted_belongingness or 0.0


class GraphEdge(Base):
    """Social graph for Culture Thermometer"""

    __tablename__ = "graph_edges"
    __table_args__ = (
        Index("ix_graph_edges_tenant", "tenant_id"),
        {"schema": "analytics"},
    )

    id = Column(BigInteger, primary_key=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    source_hash = Column(String(64), index=True)
    target_hash = Column(String(64), index=True)
    weight = Column(Float)  # Interaction frequency
    last_interaction = Column(DateTime)
    edge_type = Column(String(20))  # mentorship, collaboration, blocking


class CentralityScore(Base):
    """Talent Scout outputs"""

    __tablename__ = "centrality_scores"
    __table_args__ = {"schema": "analytics"}

    user_hash = Column(String(64), primary_key=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    betweenness = Column(Float)  # How often they bridge disconnected groups
    eigenvector = Column(Float)  # Connected to important people
    unblocking_count = Column(Integer)
    knowledge_transfer_score = Column(Float)
    calculated_at = Column(DateTime, default=datetime.utcnow)


class RiskHistory(Base):
    """Historical risk score snapshots for timeline charts"""

    __tablename__ = "risk_history"
    __table_args__ = (
        Index("ix_risk_history_user_timestamp", "user_hash", "timestamp"),
        {"schema": "analytics"},
    )

    id = Column(BigInteger, primary_key=True)
    user_hash = Column(String(64), index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    risk_level = Column(String(20))
    velocity = Column(Float)
    confidence = Column(Float)
    belongingness_score = Column(Float, default=0.0)
    timestamp = Column(DateTime, default=datetime.utcnow)


class SkillProfile(Base):
    """Employee skills profile for radar chart visualization"""

    __tablename__ = "skill_profiles"
    __table_args__ = {"schema": "analytics"}

    user_hash = Column(String(64), primary_key=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    technical = Column(Float, default=50.0)  # Technical/Problem Solving (0-100)
    communication = Column(Float, default=50.0)  # Communication skills (0-100)
    leadership = Column(Float, default=50.0)  # Leadership (0-100)
    collaboration = Column(Float, default=50.0)  # Team collaboration (0-100)
    adaptability = Column(Float, default=50.0)  # Adaptability/Learning (0-100)
    creativity = Column(Float, default=50.0)  # Creativity/Innovation (0-100)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        """Convert to dict for JSON serialization"""
        return {
            "technical": float(self.technical) if self.technical else 50.0,
            "communication": float(self.communication) if self.communication else 50.0,
            "leadership": float(self.leadership) if self.leadership else 50.0,
            "collaboration": float(self.collaboration) if self.collaboration else 50.0,
            "adaptability": float(self.adaptability) if self.adaptability else 50.0,
            "creativity": float(self.creativity) if self.creativity else 50.0,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
