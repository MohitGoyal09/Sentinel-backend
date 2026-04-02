from sqlalchemy import (
    Column,
    String,
    DateTime,
    JSON,
    Integer,
    Boolean,
    ForeignKey,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime
from uuid import uuid4

from app.models.identity import Base


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_read", "user_hash", "read_at"),
        Index("ix_notifications_user_created", "user_hash", "created_at"),
        {"schema": "identity"},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_hash = Column(
        String(64), ForeignKey("identity.users.user_hash"), nullable=False, index=True
    )
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("identity.tenants.id"), nullable=True, index=True
    )
    type = Column(String(50), nullable=False)  # auth, team, system, security, activity
    title = Column(String(255), nullable=False)
    message = Column(String(1000), nullable=False)
    data = Column(JSON, default=dict)
    priority = Column(String(20), default="normal")  # low, normal, high, critical
    action_url = Column(String(500), nullable=True)
    read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    __table_args__ = {"schema": "identity"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_hash = Column(
        String(64), ForeignKey("identity.users.user_hash"), nullable=False
    )
    channel = Column(String(20), nullable=False)  # in_app, email, sms
    notification_type = Column(String(50), nullable=False)
    enabled = Column(Boolean, default=True)


class NotificationTemplate(Base):
    __tablename__ = "notification_templates"
    __table_args__ = {"schema": "identity"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    type = Column(String(50), unique=True, nullable=False)
    subject = Column(String(255), nullable=False)
    body_template = Column(String(2000), nullable=False)
    variables = Column(JSON, default=list)
