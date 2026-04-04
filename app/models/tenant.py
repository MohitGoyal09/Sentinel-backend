from sqlalchemy import Column, String, DateTime, JSON, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
from uuid import uuid4

from app.models.identity import Base


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = {"schema": "identity"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    plan = Column(String(50), default="free")
    status = Column(String(20), default="active")
    settings = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    members = relationship(
        "TenantMember", back_populates="tenant", cascade="all, delete-orphan"
    )
    teams = relationship(
        "Team", back_populates="tenant", cascade="all, delete-orphan"
    )


class TenantMember(Base):
    __tablename__ = "tenant_members"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_hash", name="uq_tenant_user"),
        {"schema": "identity"},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("identity.tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_hash = Column(
        String(64), ForeignKey("identity.users.user_hash"), nullable=False, index=True
    )
    role = Column(String(20), default="employee")
    display_name = Column(String(100), nullable=True)
    team_id = Column(
        UUID(as_uuid=True),
        ForeignKey("identity.teams.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    invited_by = Column(String(64), nullable=True)
    joined_at = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="members")
    team = relationship("Team", back_populates="members")
