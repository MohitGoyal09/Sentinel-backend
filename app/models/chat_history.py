from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, JSON, String, Text
from app.models.identity import Base


class ChatSession(Base):
    """Chat session — groups conversation turns into a named session."""

    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("ix_chat_sessions_user_tenant", "user_hash", "tenant_id"),
        {"schema": "identity"},
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_hash = Column(String(64), nullable=False, index=True)
    tenant_id = Column(String(36), nullable=False)
    title = Column(String(255), nullable=False, default="Untitled Chat")
    is_active = Column(Boolean, nullable=False, default=True)
    is_favorite = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class ChatHistory(Base):
    """Persisted Ask Sentinel conversation turns (Vault B — identity schema)."""

    __tablename__ = "chat_history"
    __table_args__ = (
        Index("ix_chat_history_user_conversation", "user_hash", "conversation_id"),
        Index("ix_chat_history_user_tenant", "user_hash", "tenant_id"),
        Index("ix_chat_history_session", "session_id", "created_at"),
        {"schema": "identity"},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_hash = Column(String(64), nullable=False, index=True)
    tenant_id = Column(String(36), nullable=False)
    conversation_id = Column(String(64), nullable=False)  # kept for backward compat
    session_id = Column(String(36), nullable=True, index=True)  # FK to ChatSession
    role = Column(String(20), nullable=False)  # "user" or "assistant"
    type = Column(String(20), nullable=True, default="message")  # message/tool/card/workflow
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    metadata_ = Column("metadata", JSON, nullable=True)
