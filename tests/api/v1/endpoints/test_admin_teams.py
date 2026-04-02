"""
Tests for /admin/teams CRUD endpoints.
Run with: pytest tests/api/v1/endpoints/test_admin_teams.py -v
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.admin_teams import (
    create_team,
    delete_team,
    get_team,
    list_teams,
    update_team,
)
from app.models.team import Team
from app.models.tenant import TenantMember


TENANT_ID = uuid.uuid4()
TEAM_ID = uuid.uuid4()


def _make_member(
    *,
    tenant_id: uuid.UUID = TENANT_ID,
    role: str = "admin",
    user_hash: str = "admin_hash_001",
) -> MagicMock:
    """Create a mock TenantMember returned by ``require_role``."""
    m = MagicMock(spec=TenantMember)
    m.tenant_id = tenant_id
    m.role = role
    m.user_hash = user_hash
    return m


def _make_team(
    *,
    team_id: uuid.UUID = TEAM_ID,
    tenant_id: uuid.UUID = TENANT_ID,
    name: str = "Engineering",
    manager_hash: str | None = None,
) -> MagicMock:
    """Create a mock Team row."""
    t = MagicMock(spec=Team)
    t.id = team_id
    t.tenant_id = tenant_id
    t.name = name
    t.manager_hash = manager_hash
    t.created_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return t


class TestListTeams:
    """GET /admin/teams"""

    def test_returns_teams_with_member_counts(self):
        """Admin should see all tenant teams with member counts."""
        db = MagicMock()
        member = _make_member()
        team = _make_team()

        # Simulate the outerjoin/group_by query returning (Team, count) tuples
        db.query.return_value.outerjoin.return_value.filter.return_value.group_by.return_value.all.return_value = [
            (team, 3),
        ]

        result = list_teams(member=member, db=db)

        assert len(result) == 1
        item = result[0]
        assert item.name == "Engineering"
        assert item.member_count == 3

    def test_returns_empty_list_when_no_teams(self):
        """Should return an empty list when the tenant has no teams."""
        db = MagicMock()
        member = _make_member()

        db.query.return_value.outerjoin.return_value.filter.return_value.group_by.return_value.all.return_value = []

        result = list_teams(member=member, db=db)
        assert result == []


class TestCreateTeam:
    """POST /admin/teams"""

    def test_creates_team_successfully(self):
        """Should return 201 when team is created."""
        db = MagicMock()
        member = _make_member()

        # No duplicate name
        db.query.return_value.filter.return_value.first.return_value = None

        from app.schemas.team import TeamCreateRequest

        body = TeamCreateRequest(name="Backend Squad")

        result = create_team(body=body, member=member, db=db)

        db.add.assert_called_once()
        db.commit.assert_called_once()
        db.refresh.assert_called_once()

    def test_rejects_duplicate_name(self):
        """Should raise 409 if team name already exists in tenant."""
        db = MagicMock()
        member = _make_member()

        # Duplicate found
        db.query.return_value.filter.return_value.first.return_value = _make_team()

        from app.schemas.team import TeamCreateRequest

        body = TeamCreateRequest(name="Engineering")

        with pytest.raises(HTTPException) as exc_info:
            create_team(body=body, member=member, db=db)

        assert exc_info.value.status_code == 409

    def test_rejects_invalid_manager_hash(self):
        """Should raise 400 if manager_hash references a non-manager."""
        db = MagicMock()
        member = _make_member()

        # First call: no duplicate name. Second call: manager not found.
        db.query.return_value.filter.return_value.first.side_effect = [
            None,  # duplicate-name check
            None,  # manager-hash validation
        ]

        from app.schemas.team import TeamCreateRequest

        body = TeamCreateRequest(name="New Team", manager_hash="nonexistent_hash")

        with pytest.raises(HTTPException) as exc_info:
            create_team(body=body, member=member, db=db)

        assert exc_info.value.status_code == 400


class TestGetTeam:
    """GET /admin/teams/{team_id}"""

    def test_returns_team_with_member_list(self):
        """Should return the team plus its members."""
        db = MagicMock()
        member = _make_member()
        team = _make_team()

        # Team lookup
        db.query.return_value.filter.return_value.first.return_value = team

        # Members query
        mem1 = MagicMock(spec=TenantMember)
        mem1.user_hash = "user_hash_a"
        mem1.role = "employee"

        mem2 = MagicMock(spec=TenantMember)
        mem2.user_hash = "user_hash_b"
        mem2.role = "manager"

        db.query.return_value.filter.return_value.all.return_value = [mem1, mem2]

        result = get_team(team_id=TEAM_ID, member=member, db=db)

        assert result.name == "Engineering"
        assert len(result.members) == 2
        roles = {m.role for m in result.members}
        assert "employee" in roles
        assert "manager" in roles

    def test_returns_404_for_missing_team(self):
        """Should raise 404 when team does not exist."""
        db = MagicMock()
        member = _make_member()

        db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            get_team(team_id=uuid.uuid4(), member=member, db=db)

        assert exc_info.value.status_code == 404


class TestUpdateTeam:
    """PATCH /admin/teams/{team_id}"""

    def test_updates_name_successfully(self):
        """Should update the team name and commit."""
        db = MagicMock()
        member = _make_member()
        team = _make_team()

        # Team lookup succeeds, duplicate check returns None
        db.query.return_value.filter.return_value.first.side_effect = [
            team,  # find team
            None,  # no duplicate name
        ]

        from app.schemas.team import TeamUpdateRequest

        body = TeamUpdateRequest(name="Platform Squad")

        result = update_team(team_id=TEAM_ID, body=body, member=member, db=db)

        assert team.name == "Platform Squad"
        db.commit.assert_called_once()


class TestDeleteTeam:
    """DELETE /admin/teams/{team_id}"""

    def test_blocks_delete_with_active_members(self):
        """Should return 409 if team has active members."""
        db = MagicMock()
        member = _make_member()
        team = _make_team()

        # Team lookup
        db.query.return_value.filter.return_value.first.return_value = team

        # Active member count > 0
        db.query.return_value.filter.return_value.scalar.return_value = 2

        with pytest.raises(HTTPException) as exc_info:
            delete_team(team_id=TEAM_ID, member=member, db=db)

        assert exc_info.value.status_code == 409
        assert "2 active member(s)" in exc_info.value.detail

    def test_deletes_empty_team_successfully(self):
        """Should delete a team with zero members and return 204."""
        db = MagicMock()
        member = _make_member()
        team = _make_team()

        # Team lookup
        db.query.return_value.filter.return_value.first.return_value = team

        # No active members
        db.query.return_value.filter.return_value.scalar.return_value = 0

        # Should not raise
        delete_team(team_id=TEAM_ID, member=member, db=db)

        db.delete.assert_called_once_with(team)
        db.commit.assert_called_once()

    def test_returns_404_for_missing_team(self):
        """Should raise 404 when team does not exist."""
        db = MagicMock()
        member = _make_member()

        db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            delete_team(team_id=uuid.uuid4(), member=member, db=db)

        assert exc_info.value.status_code == 404
