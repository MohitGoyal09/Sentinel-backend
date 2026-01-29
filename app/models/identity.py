from sqlalchemy import Column, String, LargeBinary, DateTime, JSON, Integer
from sqlalchemy.orm import declarative_base
from datetime import datetime

Base = declarative_base()

class UserIdentity(Base):
    """Vault B: Encrypted PII"""
    __tablename__ = 'users'
    __table_args__ = {"schema": "identity"}
    
    user_hash = Column(String(64), primary_key=True) # Changed to 64 chars to accommodate SHA-256 hex digest if needed, typically 16 as per code but hash is usually longer? user code says [:16]
    email_encrypted = Column(LargeBinary, nullable=False)
    slack_id_encrypted = Column(LargeBinary, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class AuditLog(Base):
    """Vault B: Immutable audit trail"""
    __tablename__ = 'audit_logs'
    __table_args__ = {"schema": "identity"}
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_hash = Column(String(64), index=True)
    action = Column(String(50))  # nudge_sent, data_deleted, etc.
    details = Column(JSON, default={})
    timestamp = Column(DateTime, default=datetime.utcnow)
