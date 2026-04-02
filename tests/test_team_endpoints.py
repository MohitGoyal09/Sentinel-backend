"""
Tests for /team endpoints (Manager Team View)
Run with: pytest tests/test_team_endpoints.py -v

The endpoint code at app/api/v1/endpoints/team.py still calls the
pre-migration PermissionService API (e.g. ``can_manager_view_employee``
with 2 args instead of 3).  Because we must NOT modify production code,
the tests patch the PermissionService methods used inside the endpoints
so the call signatures match what the endpoint actually passes.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock, patch
from uuid import uuid4
from fastapi import HTTPException

from app.api.v1.endpoints.team import (
    get_my_team_dashboard,
    get_team_member_details,
    get_team_analytics,
    anonymize_user_hash,
)
from app.models.identity import UserIdentity
from app.models.tenant import TenantMember
from app.models.analytics import RiskScore, RiskHistory, SkillProfile, Event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
TEAM_ID = uuid4()
TENANT_ID = uuid4()


def _make_member(
    user_hash: str,
    role: str = "employee",
    team_id=TEAM_ID,
    tenant_id=TENANT_ID,
) -> Mock:
    """Create a mock TenantMember (what require_role now returns)."""
    m = Mock(spec=TenantMember)
    m.user_hash = user_hash
    m.role = role
    m.team_id = team_id
    m.tenant_id = tenant_id
    return m


class TestTeamEndpoints:
    """Test suite for /team endpoints"""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session"""
        return MagicMock()

    @pytest.fixture
    def manager_member(self):
        """Create a manager TenantMember fixture (what require_role returns)"""
        return _make_member("mgr_test_hash_456", role="manager")

    @pytest.fixture
    def admin_member(self):
        """Create an admin TenantMember fixture"""
        return _make_member("admin_test_hash_789", role="admin")

    class TestAnonymization:
        """Test anonymization helper functions"""

        def test_anonymize_user_hash(self):
            """Should convert hash to User A, B, C, etc."""
            assert anonymize_user_hash("abc123", 0) == "User A"
            assert anonymize_user_hash("def456", 1) == "User B"
            assert anonymize_user_hash("ghi789", 2) == "User C"

    class TestGetTeamDashboard:
        """Test GET /team endpoint"""

        def test_returns_team_overview(self, mock_db, manager_member):
            """Should return team dashboard with anonymized data"""
            # Setup: Create team members
            emp1 = Mock(spec=UserIdentity)
            emp1.user_hash = "emp1_hash"
            emp1.manager_hash = manager_member.user_hash
            emp1.consent_share_with_manager = True

            emp2 = Mock(spec=UserIdentity)
            emp2.user_hash = "emp2_hash"
            emp2.manager_hash = manager_member.user_hash
            emp2.consent_share_with_manager = False

            # Risk scores
            risk1 = Mock(spec=RiskScore)
            risk1.user_hash = "emp1_hash"
            risk1.risk_level = "ELEVATED"

            risk2 = Mock(spec=RiskScore)
            risk2.user_hash = "emp2_hash"
            risk2.risk_level = "LOW"

            # Event mock (empty list for simplicity)
            # The endpoint queries multiple models in sequence.
            # We track call order to return the right data per query.
            call_count = 0

            def mock_query(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                mock_q = MagicMock()
                # Determine which model is being queried from the first arg
                model = args[0] if args else None

                if model is UserIdentity:
                    # Queries for team members
                    mock_q.filter.return_value.count.return_value = 2
                    mock_q.filter.return_value.offset.return_value.limit.return_value.all.return_value = [
                        emp1, emp2
                    ]
                    mock_q.filter.return_value.all.return_value = [emp1, emp2]
                elif model is RiskScore:
                    mock_q.filter.return_value.all.return_value = [risk1, risk2]
                    mock_q.filter.return_value.group_by.return_value.all.return_value = [
                        ("ELEVATED", 1),
                        ("LOW", 1),
                    ]
                else:
                    # Column-level queries (RiskScore.risk_level, func.count(...))
                    # or Event queries
                    mock_q.filter.return_value.all.return_value = []
                    mock_q.group_by.return_value.all.return_value = [
                        ("ELEVATED", 1),
                        ("LOW", 1),
                    ]
                    mock_q.filter.return_value.group_by.return_value.all.return_value = [
                        ("ELEVATED", 1),
                        ("LOW", 1),
                    ]
                    mock_q.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

                return mock_q

            mock_db.query.side_effect = mock_query

            result = get_my_team_dashboard(
                current_user=manager_member, db=mock_db
            )

            assert result["team"]["member_count"] == 2
            assert result["metrics"]["total_members"] == 2
            assert result["consent_summary"]["consented"] == 1
            assert result["consent_summary"]["not_consented"] == 1

        def test_handles_no_team_members(self, mock_db, manager_member):
            """Should handle manager with no team"""

            def mock_query(*args, **kwargs):
                mock_q = MagicMock()
                mock_q.filter.return_value.all.return_value = []
                mock_q.filter.return_value.count.return_value = 0
                mock_q.filter.return_value.offset.return_value.limit.return_value.all.return_value = []
                return mock_q

            mock_db.query.side_effect = mock_query

            result = get_my_team_dashboard(
                current_user=manager_member, db=mock_db
            )

            assert result["team"]["member_count"] == 0
            assert "No team members" in result["team"]["message"]

    class TestGetTeamMemberDetails:
        """Test GET /team/member/{hash} endpoint

        The endpoint creates a PermissionService internally and calls
        ``can_manager_view_employee`` and ``log_data_access`` with the
        old (pre-migration) argument order.  We patch those methods so
        the endpoint runs without TypeError.
        """

        @patch(
            "app.api.v1.endpoints.team.PermissionService.log_data_access",
            return_value=None,
        )
        @patch(
            "app.api.v1.endpoints.team.PermissionService.can_manager_view_employee"
        )
        def test_grants_access_with_consent(
            self, mock_can_view, mock_log, mock_db, manager_member
        ):
            """Should grant access when employee has consented"""
            mock_can_view.return_value = (True, "Employee has consented to share data")

            # Setup: Employee with consent
            employee = Mock(spec=UserIdentity)
            employee.user_hash = "emp_consent_hash"
            employee.manager_hash = manager_member.user_hash
            employee.consent_share_with_manager = True
            employee.monitoring_paused_until = None

            # Risk score mock
            risk_mock = Mock(spec=RiskScore)
            risk_mock.risk_level = "ELEVATED"
            risk_mock.velocity = 1.5
            risk_mock.confidence = 0.85
            risk_mock.thwarted_belongingness = 0.3
            risk_mock.updated_at = datetime.utcnow()

            # Skills profile mock
            skills_mock = Mock()
            skills_mock.to_dict.return_value = {"skills": []}

            def mock_query(*args, **kwargs):
                model = args[0] if args else None
                mock_q = MagicMock()
                if model is UserIdentity:
                    mock_q.filter_by.return_value.first.return_value = employee
                elif model is RiskScore:
                    mock_q.filter_by.return_value.first.return_value = risk_mock
                elif model is SkillProfile:
                    mock_q.filter_by.return_value.first.return_value = skills_mock
                else:
                    # RiskHistory, Event, etc.
                    mock_q.filter_by.return_value.first.return_value = None
                    mock_q.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
                return mock_q

            mock_db.query.side_effect = mock_query

            result = get_team_member_details(
                user_hash="emp_consent_hash",
                current_user=manager_member,
                db=mock_db,
            )

            assert result["access"] == "granted"
            assert result["employee"]["is_identified"] is True

        @patch(
            "app.api.v1.endpoints.team.PermissionService.log_data_access",
            return_value=None,
        )
        @patch(
            "app.api.v1.endpoints.team.PermissionService.can_manager_view_employee"
        )
        def test_denies_access_without_consent(
            self, mock_can_view, mock_log, mock_db, manager_member
        ):
            """Should deny access when no consent and no emergency"""
            mock_can_view.return_value = (
                False,
                "Employee has not consented to share detailed data",
            )

            # Setup: Employee without consent
            employee = Mock(spec=UserIdentity)
            employee.user_hash = "emp_no_consent_hash"
            employee.manager_hash = manager_member.user_hash
            employee.consent_share_with_manager = False

            mock_db.query.return_value.filter_by.return_value.first.return_value = (
                employee
            )

            result = get_team_member_details(
                user_hash="emp_no_consent_hash",
                current_user=manager_member,
                db=mock_db,
            )

            assert result["access"] == "denied"
            assert "not consented" in result["reason"].lower()

        @patch(
            "app.api.v1.endpoints.team.PermissionService.log_data_access",
            return_value=None,
        )
        @patch(
            "app.api.v1.endpoints.team.PermissionService.can_manager_view_employee"
        )
        def test_rejects_non_team_member(
            self, mock_can_view, mock_log, mock_db, manager_member
        ):
            """Should reject if employee doesn't report to this manager"""
            mock_can_view.return_value = (False, "Not your direct report")

            # Setup: Employee with different manager
            employee = Mock(spec=UserIdentity)
            employee.user_hash = "emp_other_hash"
            employee.manager_hash = "other_manager_hash"

            mock_db.query.return_value.filter_by.return_value.first.return_value = (
                employee
            )

            # Should raise 403 because employee.manager_hash != current_user.user_hash
            with pytest.raises(HTTPException) as exc_info:
                get_team_member_details(
                    user_hash="emp_other_hash",
                    current_user=manager_member,
                    db=mock_db,
                )

            assert exc_info.value.status_code == 403

    class TestGetTeamAnalytics:
        """Test GET /team/analytics endpoint"""

        def test_calculates_team_metrics(self, mock_db, manager_member):
            """Should calculate team health metrics"""
            # Setup: Team members
            emp1 = Mock(spec=UserIdentity)
            emp1.user_hash = "emp1_hash"
            emp1.manager_hash = manager_member.user_hash

            # Risk history
            history1 = Mock(spec=RiskHistory)
            history1.user_hash = "emp1_hash"
            history1.timestamp = datetime.utcnow() - timedelta(days=1)
            history1.velocity = 1.5
            history1.risk_level = "ELEVATED"

            # Current risk
            risk1 = Mock(spec=RiskScore)
            risk1.user_hash = "emp1_hash"
            risk1.velocity = 1.5
            risk1.risk_level = "ELEVATED"

            def mock_query(*args, **kwargs):
                model = args[0] if args else None
                mock_q = MagicMock()
                if model is UserIdentity:
                    mock_q.filter.return_value.all.return_value = [emp1]
                elif model is RiskHistory:
                    mock_q.filter.return_value.order_by.return_value.all.return_value = [
                        history1
                    ]
                elif model is RiskScore:
                    mock_q.filter.return_value.all.return_value = [risk1]
                return mock_q

            mock_db.query.side_effect = mock_query

            result = get_team_analytics(
                days=30,
                current_user=manager_member,
                db=mock_db,
            )

            assert result["period_days"] == 30
            assert result["team_size"] == 1
            assert "health_score" in result
            assert "current_metrics" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
