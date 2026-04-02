"""
Tests for the promote/demote safety guards.

Covers all five guard conditions by calling
``validate_promote_demote_guards()`` directly with sync MagicMock objects.
"""

import pytest
from unittest.mock import MagicMock
from fastapi import HTTPException

from app.api.v1.endpoints.admin_promote import validate_promote_demote_guards


TENANT_ID = "tenant-001"


def _make_member(
    user_hash: str = "user-aaa",
    role: str = "employee",
    team_id=None,
    tenant_id: str = TENANT_ID,
) -> MagicMock:
    """Create a lightweight TenantMember mock."""
    m = MagicMock()
    m.user_hash = user_hash
    m.role = role
    m.team_id = team_id
    m.tenant_id = tenant_id
    return m


# -----------------------------------------------------------------------
# Guard 1: Admin cannot demote themselves
# -----------------------------------------------------------------------
class TestSelfDemotionGuard:
    def test_admin_cannot_change_own_role(self):
        db = MagicMock()
        caller = _make_member(user_hash="admin-1", role="admin")
        target = _make_member(user_hash="admin-1", role="admin")

        with pytest.raises(HTTPException) as exc_info:
            validate_promote_demote_guards(
                db=db,
                caller=caller,
                target=target,
                new_role="employee",
                tenant_id=TENANT_ID,
            )

        assert exc_info.value.status_code == 403
        assert "yourself" in exc_info.value.detail.lower()


# -----------------------------------------------------------------------
# Guard 2: Last admin cannot be demoted
# -----------------------------------------------------------------------
class TestLastAdminGuard:
    def test_last_admin_cannot_be_demoted(self):
        db = MagicMock()
        # Chain: db.query(...).filter(...).count() → 1
        db.query.return_value.filter.return_value.count.return_value = 1

        caller = _make_member(user_hash="admin-1", role="admin")
        target = _make_member(user_hash="admin-2", role="admin")

        with pytest.raises(HTTPException) as exc_info:
            validate_promote_demote_guards(
                db=db,
                caller=caller,
                target=target,
                new_role="manager",
                tenant_id=TENANT_ID,
                new_team_id="team-1",
            )

        assert exc_info.value.status_code == 409
        assert "last admin" in exc_info.value.detail.lower()


# -----------------------------------------------------------------------
# Guard 3: Promoting to manager requires team
# -----------------------------------------------------------------------
class TestManagerRequiresTeamGuard:
    def test_promote_to_manager_requires_team_id(self):
        db = MagicMock()
        # Allow guard 2 to pass by returning multiple admins
        db.query.return_value.filter.return_value.count.return_value = 3

        caller = _make_member(user_hash="admin-1", role="admin")
        target = _make_member(user_hash="user-2", role="employee")

        with pytest.raises(HTTPException) as exc_info:
            validate_promote_demote_guards(
                db=db,
                caller=caller,
                target=target,
                new_role="manager",
                tenant_id=TENANT_ID,
                new_team_id=None,
            )

        assert exc_info.value.status_code == 422
        assert "team" in exc_info.value.detail.lower()


# -----------------------------------------------------------------------
# Guard 4: Demoting manager with team requires reassignment
# -----------------------------------------------------------------------
class TestManagerDemoteReassignGuard:
    def test_demote_manager_with_team_requires_reassignment(self):
        db = MagicMock()

        caller = _make_member(user_hash="admin-1", role="admin")
        target = _make_member(
            user_hash="mgr-1", role="manager", team_id="team-abc"
        )

        with pytest.raises(HTTPException) as exc_info:
            validate_promote_demote_guards(
                db=db,
                caller=caller,
                target=target,
                new_role="employee",
                tenant_id=TENANT_ID,
                new_team_id=None,
            )

        assert exc_info.value.status_code == 422
        assert "reassign" in exc_info.value.detail.lower()


# -----------------------------------------------------------------------
# Guard 5: Valid promotion passes all guards
# -----------------------------------------------------------------------
class TestValidPromotion:
    def test_valid_promotion_passes_all_guards(self):
        db = MagicMock()
        # Guard 2 — enough admins
        db.query.return_value.filter.return_value.count.return_value = 3

        caller = _make_member(user_hash="admin-1", role="admin")
        target = _make_member(user_hash="user-2", role="employee")

        # Should not raise
        result = validate_promote_demote_guards(
            db=db,
            caller=caller,
            target=target,
            new_role="manager",
            tenant_id=TENANT_ID,
            new_team_id="team-xyz",
        )

        assert result is None
