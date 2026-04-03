from uuid import uuid4

from sqlalchemy import Column, Index, String, LargeBinary, DateTime, JSON, Integer, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base
from datetime import datetime, timezone

Base = declarative_base()


class UserIdentity(Base):
    """Vault B: Encrypted PII with RBAC"""

    __tablename__ = "users"
    __table_args__ = {"schema": "identity"}

    user_hash = Column(String(64), primary_key=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    email_encrypted = Column(LargeBinary, nullable=False)
    slack_id_encrypted = Column(LargeBinary, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Consent Controls
    consent_share_with_manager = Column(Boolean, default=False)
    consent_share_anonymized = Column(Boolean, default=True)  # Default allow aggregates

    # Employee-controlled pause
    monitoring_paused_until = Column(DateTime, nullable=True)

    # Soft-delete support
    is_active = Column(Boolean, nullable=False, default=True)
    deactivated_at = Column(DateTime, nullable=True)


class AuditLog(Base):
    """Immutable record of every privileged action in the system."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_tenant_timestamp", "tenant_id", "timestamp"),
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_user_hash", "user_hash"),
        Index("ix_audit_logs_actor_hash", "actor_hash"),
        {"schema": "identity"},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    # No FK constraint — audit logs must survive tenant deletion for compliance
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    actor_hash = Column(String(64), nullable=True)
    actor_role = Column(String(20), nullable=True)
    user_hash = Column(String(64), nullable=True)  # target user
    action = Column(String(50), nullable=False)
    details = Column(JSON, nullable=False, default=dict)
    ip_address = Column(String(45), nullable=True)
    timestamp = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )
