from sqlalchemy import Column, String, Float, DateTime, Integer, JSON
from sqlalchemy.orm import declarative_base
from datetime import datetime

Base = declarative_base()

class Event(Base):
    """Raw behavioral events (Vault A)"""
    __tablename__ = "events"
    __table_args__ = {"schema": "analytics"}
    
    id = Column(Integer, primary_key=True)
    user_hash = Column(String(64), index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    event_type = Column(String(50))  # commit, pr_review, slack_message, unblocked
    target_user_hash = Column(String(64), nullable=True)  # For graph edges
    metadata_ = Column("metadata", JSON) # Renamed to avoid reserved keyword conflict if any, but mapping to "metadata" column

class RiskScore(Base):
    """Safety Valve outputs"""
    __tablename__ = "risk_scores"
    __table_args__ = {"schema": "analytics"}
    
    user_hash = Column(String(64), primary_key=True)
    velocity = Column(Float)
    risk_level = Column(String(20))
    confidence = Column(Float)
    thwarted_belongingness = Column(Float)  # Psychological metric
    updated_at = Column(DateTime, default=datetime.utcnow)

class GraphEdge(Base):
    """Social graph for Culture Thermometer"""
    __tablename__ = "graph_edges"
    __table_args__ = {"schema": "analytics"}
    
    id = Column(Integer, primary_key=True)
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
    betweenness = Column(Float)  # How often they bridge disconnected groups
    eigenvector = Column(Float)  # Connected to important people
    unblocking_count = Column(Integer)
    knowledge_transfer_score = Column(Float)
    calculated_at = Column(DateTime, default=datetime.utcnow)
