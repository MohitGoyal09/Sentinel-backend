"""Invitation model — tracks admin-issued, token-based user invitations."""
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Index, LargeBinary, String
from sqlalchemy.dialects.postgresql import UUID

from app.models.identity import Base


def _default_expires_at() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=7)


def _generate_token() -> str:
    return secrets.token_urlsafe(64)


class Invitation(Base):
    """
    Pending user invitations created by admins.

    Lifecycle:
        pending  →  accepted  (user set password and completed signup)
        pending  →  expired   (cron job or inline check after expires_at passes)
    """

    __tablename__ = "invitations"
    __table_args__ = (
        Index("ix_invitations_tenant_emailhash_status", "tenant_id", "email_hash", "status"),
        {"schema": "identity"},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("identity.tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email_hash = Column(String(32), nullable=False)
    email_encrypted = Column(LargeBinary, nullable=False)
    token = Column(String(128), unique=True, nullable=False, index=True, default=_generate_token)
    role = Column(String(20), nullable=False)
    team_id = Column(
        UUID(as_uuid=True),
        ForeignKey("identity.teams.id", ondelete="SET NULL"),
        nullable=True,
    )
    invited_by = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    created_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )
    expires_at = Column(DateTime, nullable=False, default=_default_expires_at)
