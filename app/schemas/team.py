"""Pydantic schemas for Team CRUD endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TeamCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    manager_hash: Optional[str] = None


class TeamUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    manager_hash: Optional[str] = None


class TeamMemberSummary(BaseModel):
    user_hash: str
    role: str

    model_config = {"from_attributes": True}


class TeamResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    manager_hash: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class TeamListItem(TeamResponse):
    """TeamResponse extended with a member count for list views."""

    member_count: int = 0


class TeamDetailResponse(TeamResponse):
    """TeamResponse extended with full member list for detail views."""

    members: list[TeamMemberSummary] = []
