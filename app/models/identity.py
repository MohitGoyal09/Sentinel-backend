from sqlalchemy import Column, String, LargeBinary, DateTime, JSON, Integer, Boolean
from sqlalchemy.orm import declarative_base
from datetime import datetime

Base = declarative_base()


class UserIdentity(Base):
    """Vault B: Encrypted PII with RBAC"""

    __tablename__ = "users"
    __table_args__ = {"schema": "identity"}

    user_hash = Column(String(64), primary_key=True)
    email_encrypted = Column(LargeBinary, nullable=False)
    slack_id_encrypted = Column(LargeBinary, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # RBAC Fields
    role = Column(String(20), default="employee")  # employee, manager, admin

    # Consent Controls
    consent_share_with_manager = Column(Boolean, default=False)
    consent_share_anonymized = Column(Boolean, default=True)  # Default allow aggregates

    # Employee-controlled pause
    monitoring_paused_until = Column(DateTime, nullable=True)

    # Manager reference (who can see this employee's data)
    manager_hash = Column(String(64), nullable=True, index=True)


class AuditLog(Base):
    """Vault B: Immutable audit trail"""

    __tablename__ = "audit_logs"
    __table_args__ = {"schema": "identity"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_hash = Column(String(64), index=True)
    action = Column(String(50))  # nudge_sent, data_deleted, etc.
    details = Column(JSON, default={})
    timestamp = Column(DateTime, default=datetime.utcnow)
