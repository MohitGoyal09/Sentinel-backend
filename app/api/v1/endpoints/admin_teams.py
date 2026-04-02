"""
Admin Team CRUD Endpoints (/admin/teams)

Provides create, read, update, delete operations for Team resources.
All endpoints require the ``admin`` role.
"""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.api.deps.auth import require_role
from app.models.team import Team
from app.models.tenant import TenantMember
from app.schemas.team import (
    TeamCreateRequest,
    TeamUpdateRequest,
    TeamDetailResponse,
    TeamListItem,
    TeamMemberSummary,
    TeamResponse,
)

router = APIRouter(prefix="/admin/teams", tags=["Admin", "Teams"])


@router.get("", response_model=List[TeamListItem])
def list_teams(
    member: TenantMember = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> list[dict]:
    """List all teams in the caller's tenant with member counts."""
    tenant_id = member.tenant_id

    rows = (
        db.query(
            Team,
            func.count(TenantMember.id).label("member_count"),
        )
        .outerjoin(TenantMember, TenantMember.team_id == Team.id)
        .filter(Team.tenant_id == tenant_id)
        .group_by(Team.id)
        .all()
    )

    result: list[dict] = []
    for team, count in rows:
        item = TeamListItem.model_validate(team)
        item.member_count = count
        result.append(item)
    return result


@router.post("", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
def create_team(
    body: TeamCreateRequest,
    member: TenantMember = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> Team:
    """Create a new team within the caller's tenant.

    If ``manager_hash`` is provided, it must reference an existing
    TenantMember whose role is ``manager`` or ``admin``.
    """
    tenant_id = member.tenant_id

    # Duplicate-name guard
    existing = (
        db.query(Team)
        .filter(Team.tenant_id == tenant_id, Team.name == body.name)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A team named '{body.name}' already exists in this tenant",
        )

    # Validate manager_hash if supplied
    if body.manager_hash is not None:
        manager_member = (
            db.query(TenantMember)
            .filter(
                TenantMember.tenant_id == tenant_id,
                TenantMember.user_hash == body.manager_hash,
                TenantMember.role.in_(["manager", "admin"]),
            )
            .first()
        )
        if manager_member is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="manager_hash must reference an existing manager or admin in this tenant",
            )

    team = Team(
        tenant_id=tenant_id,
        name=body.name,
        manager_hash=body.manager_hash,
    )
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


@router.get("/{team_id}", response_model=TeamDetailResponse)
def get_team(
    team_id: uuid.UUID,
    member: TenantMember = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> TeamDetailResponse:
    """Get a single team with its full member list."""
    tenant_id = member.tenant_id

    team = (
        db.query(Team)
        .filter(Team.id == team_id, Team.tenant_id == tenant_id)
        .first()
    )
    if team is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found",
        )

    members = (
        db.query(TenantMember)
        .filter(TenantMember.team_id == team_id, TenantMember.tenant_id == tenant_id)
        .all()
    )

    response = TeamDetailResponse.model_validate(team)
    response.members = [
        TeamMemberSummary(user_hash=m.user_hash, role=m.role)
        for m in members
    ]
    return response


@router.patch("/{team_id}", response_model=TeamResponse)
def update_team(
    team_id: uuid.UUID,
    body: TeamUpdateRequest,
    member: TenantMember = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> Team:
    """Update a team's name and/or manager_hash."""
    tenant_id = member.tenant_id

    team = (
        db.query(Team)
        .filter(Team.id == team_id, Team.tenant_id == tenant_id)
        .first()
    )
    if team is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found",
        )

    if body.name is not None:
        # Ensure uniqueness within tenant
        duplicate = (
            db.query(Team)
            .filter(
                Team.tenant_id == tenant_id,
                Team.name == body.name,
                Team.id != team_id,
            )
            .first()
        )
        if duplicate:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A team named '{body.name}' already exists in this tenant",
            )
        team.name = body.name

    if body.manager_hash is not None:
        manager_member = (
            db.query(TenantMember)
            .filter(
                TenantMember.tenant_id == tenant_id,
                TenantMember.user_hash == body.manager_hash,
                TenantMember.role.in_(["manager", "admin"]),
            )
            .first()
        )
        if manager_member is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="manager_hash must reference an existing manager or admin in this tenant",
            )
        team.manager_hash = body.manager_hash

    db.commit()
    db.refresh(team)
    return team


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_team(
    team_id: uuid.UUID,
    member: TenantMember = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> None:
    """Delete a team.

    Returns 409 Conflict if the team still has active members.
    Reassign or remove members before deleting.
    """
    tenant_id = member.tenant_id

    team = (
        db.query(Team)
        .filter(Team.id == team_id, Team.tenant_id == tenant_id)
        .first()
    )
    if team is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found",
        )

    active_member_count = (
        db.query(func.count(TenantMember.id))
        .filter(TenantMember.team_id == team_id)
        .scalar()
    )
    if active_member_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete team with {active_member_count} active member(s). "
            "Reassign or remove members first.",
        )

    db.delete(team)
    db.commit()
