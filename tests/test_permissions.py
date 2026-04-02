"""
Comprehensive tests for the PermissionService and RBAC system.
Run with: pytest tests/test_permissions.py -v
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock
from uuid import uuid4

from app.services.permission_service import (
    PermissionService,
    UserRole,
    PermissionDenied,
    NotFound,
)
from app.models.identity import UserIdentity, AuditLog
from app.models.tenant import TenantMember
from app.models.analytics import RiskScore, RiskHistory


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
    """Create a mock TenantMember."""
    m = Mock(spec=TenantMember)
    m.user_hash = user_hash
    m.role = role
    m.team_id = team_id
    m.tenant_id = tenant_id
    return m


class TestPermissionService:
    """Test suite for PermissionService"""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session"""
        return MagicMock()

    @pytest.fixture
    def permission_service(self, mock_db):
        """Create a PermissionService with mocked DB"""
        return PermissionService(mock_db)

    @pytest.fixture
    def employee_member(self):
        """Create an employee TenantMember fixture"""
        return _make_member("employee_hash_123", role="employee")

    @pytest.fixture
    def manager_member(self):
        """Create a manager TenantMember fixture"""
        return _make_member("manager_hash_456", role="manager")

    @pytest.fixture
    def admin_member(self):
        """Create an admin TenantMember fixture"""
        return _make_member("admin_hash_789", role="admin")

    class TestBasicPermissions:
        """Test basic permission checks"""

        def test_employee_can_view_own_data(self, permission_service, employee_member, mock_db):
            """Employees should be able to view their own data"""
            can_view, reason = permission_service.can_view_user_data(
                mock_db, employee_member, employee_member.user_hash
            )
            assert can_view is True
            assert "own data" in reason.lower()

        def test_employee_cannot_view_others_data(
            self, permission_service, employee_member, mock_db
        ):
            """Employees should NOT be able to view other users' data"""
            other_hash = "other_employee_hash"
            can_view, reason = permission_service.can_view_user_data(
                mock_db, employee_member, other_hash
            )
            assert can_view is False
            assert "own data" in reason.lower()

        def test_admin_can_view_any_data(self, permission_service, admin_member, mock_db):
            """Admins should be able to view anyone's data"""
            any_hash = "any_user_hash"
            can_view, reason = permission_service.can_view_user_data(
                mock_db, admin_member, any_hash
            )
            assert can_view is True
            assert "admin" in reason.lower()

        def test_manager_cannot_view_unrelated_employee(
            self, permission_service, manager_member, mock_db
        ):
            """Manager should NOT view employee who doesn't report to them"""
            # Setup: No TenantMember found for this employee in manager's team
            mock_db.query.return_value.filter_by.return_value.first.return_value = None

            can_view, reason = permission_service.can_view_user_data(
                mock_db, manager_member, "unrelated_hash"
            )
            assert can_view is False
            assert "Not your direct report" in reason

    class TestConsentBasedAccess:
        """Test consent-based data access for managers"""

        def test_manager_can_view_consented_employee(
            self, permission_service, manager_member, mock_db
        ):
            """Manager should view employee who has consented"""
            employee_hash = "consented_employee_hash"

            # Mock: employee TenantMember exists in the same team
            employee_tm = _make_member(employee_hash, role="employee", team_id=TEAM_ID)

            # Mock: employee UserIdentity with consent
            employee_identity = Mock(spec=UserIdentity)
            employee_identity.user_hash = employee_hash
            employee_identity.consent_share_with_manager = True

            # DB query sequence:
            # 1. TenantMember lookup by team_id + user_hash -> employee_tm
            # 2. UserIdentity lookup by user_hash -> employee_identity
            call_count = 0

            def mock_query(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                mock_q = Mock()
                if call_count == 1:
                    mock_q.filter_by.return_value.first.return_value = employee_tm
                elif call_count == 2:
                    mock_q.filter_by.return_value.first.return_value = employee_identity
                return mock_q

            mock_db.query.side_effect = mock_query

            can_view, reason = permission_service.can_view_user_data(
                mock_db, manager_member, employee_hash
            )
            assert can_view is True
            assert "consented" in reason.lower()

        def test_manager_cannot_view_non_consented_employee(
            self, permission_service, manager_member, mock_db
        ):
            """Manager should NOT view employee who hasn't consented (no emergency)"""
            employee_hash = "non_consented_hash"

            # Mock: employee TenantMember exists in the same team
            employee_tm = _make_member(employee_hash, role="employee", team_id=TEAM_ID)

            # Mock: employee UserIdentity without consent
            employee_identity = Mock(spec=UserIdentity)
            employee_identity.user_hash = employee_hash
            employee_identity.consent_share_with_manager = False

            # Mock: employee is not at critical risk
            low_risk = Mock(spec=RiskScore)
            low_risk.risk_level = "LOW"
            low_risk.updated_at = datetime.utcnow()

            call_count = 0

            def mock_query(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                mock_q = Mock()
                if call_count == 1:
                    # TenantMember lookup
                    mock_q.filter_by.return_value.first.return_value = employee_tm
                elif call_count == 2:
                    # UserIdentity lookup
                    mock_q.filter_by.return_value.first.return_value = employee_identity
                elif call_count == 3:
                    # RiskScore lookup (is_critical_for_36h uses self.db)
                    mock_q.filter_by.return_value.first.return_value = low_risk
                else:
                    mock_q.filter_by.return_value.first.return_value = None
                return mock_q

            mock_db.query.side_effect = mock_query

            can_view, reason = permission_service.can_view_user_data(
                mock_db, manager_member, employee_hash
            )
            assert can_view is False
            assert "consent" in reason.lower()

    class Test36HourCriticalRule:
        """Test the emergency 36-hour critical risk override"""

        def test_manager_can_view_critical_employee_after_36h(
            self, permission_service, manager_member, mock_db
        ):
            """Manager CAN view employee at CRITICAL risk for 36+ hours"""
            employee_hash = "critical_employee_hash"

            # Mock: employee TenantMember in same team
            employee_tm = _make_member(employee_hash, role="employee", team_id=TEAM_ID)

            # Mock: employee UserIdentity without consent
            employee_identity = Mock(spec=UserIdentity)
            employee_identity.user_hash = employee_hash
            employee_identity.consent_share_with_manager = False

            # Setup: Risk has been critical for 36+ hours
            critical_risk = Mock(spec=RiskScore)
            critical_risk.risk_level = "CRITICAL"
            critical_risk.updated_at = datetime.utcnow() - timedelta(hours=40)

            # Risk history showing continuous critical status
            old_history = Mock(spec=RiskHistory)
            old_history.risk_level = "CRITICAL"
            old_history.timestamp = datetime.utcnow() - timedelta(hours=37)

            # can_view_user_data calls can_manager_view_employee which does:
            #   1. db.query(TenantMember).filter_by(team_id=..., user_hash=...) -> employee_tm
            #   2. db.query(UserIdentity).filter_by(user_hash=...) -> employee_identity
            # Then is_critical_for_36h uses self.db (the PermissionService constructor db):
            #   3. self.db.query(RiskScore).filter_by(user_hash=...) -> critical_risk
            #   4. self.db.query(RiskHistory).filter(...).order_by(...).all() -> [old_history]

            # For the db passed to can_view_user_data (the explicit db arg)
            call_count_explicit = 0

            def mock_query_explicit(*args, **kwargs):
                nonlocal call_count_explicit
                call_count_explicit += 1
                mock_q = Mock()
                if call_count_explicit == 1:
                    mock_q.filter_by.return_value.first.return_value = employee_tm
                elif call_count_explicit == 2:
                    mock_q.filter_by.return_value.first.return_value = employee_identity
                return mock_q

            mock_db.query.side_effect = mock_query_explicit

            # For the self.db (PermissionService's internal db) used by is_critical_for_36h
            # Since permission_service uses mock_db as self.db, we need a unified mock.
            # The self.db is the same mock_db, so queries 3 and 4 also go through mock_db.
            # Let's use a single side_effect that handles all calls.

            call_count = 0

            def mock_query_all(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                mock_q = Mock()
                if call_count == 1:
                    # TenantMember lookup
                    mock_q.filter_by.return_value.first.return_value = employee_tm
                elif call_count == 2:
                    # UserIdentity lookup
                    mock_q.filter_by.return_value.first.return_value = employee_identity
                elif call_count == 3:
                    # RiskScore lookup (is_critical_for_36h)
                    mock_q.filter_by.return_value.first.return_value = critical_risk
                elif call_count == 4:
                    # RiskHistory lookup (is_critical_for_36h)
                    mock_q.filter.return_value.order_by.return_value.all.return_value = [
                        old_history
                    ]
                return mock_q

            mock_db.query.side_effect = mock_query_all

            can_view, reason = permission_service.can_view_user_data(
                mock_db, manager_member, employee_hash
            )
            assert can_view is True
            assert "EMERGENCY" in reason or "critical" in reason.lower()

        def test_manager_cannot_view_critical_employee_before_36h(
            self, permission_service, manager_member, mock_db
        ):
            """Manager CANNOT view employee at CRITICAL risk for less than 36 hours"""
            employee_hash = "recent_critical_hash"

            # Mock: employee TenantMember in same team
            employee_tm = _make_member(employee_hash, role="employee", team_id=TEAM_ID)

            # Mock: employee UserIdentity without consent
            employee_identity = Mock(spec=UserIdentity)
            employee_identity.user_hash = employee_hash
            employee_identity.consent_share_with_manager = False

            # Risk is critical but only for 12 hours
            recent_critical = Mock(spec=RiskScore)
            recent_critical.risk_level = "CRITICAL"
            recent_critical.updated_at = datetime.utcnow() - timedelta(hours=12)

            # History entry only 12 hours old
            recent_history = Mock(spec=RiskHistory)
            recent_history.risk_level = "CRITICAL"
            recent_history.timestamp = datetime.utcnow() - timedelta(hours=12)

            call_count = 0

            def mock_query_all(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                mock_q = Mock()
                if call_count == 1:
                    mock_q.filter_by.return_value.first.return_value = employee_tm
                elif call_count == 2:
                    mock_q.filter_by.return_value.first.return_value = employee_identity
                elif call_count == 3:
                    mock_q.filter_by.return_value.first.return_value = recent_critical
                elif call_count == 4:
                    mock_q.filter.return_value.order_by.return_value.all.return_value = [
                        recent_history
                    ]
                return mock_q

            mock_db.query.side_effect = mock_query_all

            can_view, reason = permission_service.can_view_user_data(
                mock_db, manager_member, employee_hash
            )
            assert can_view is False

    class TestTeamAggregates:
        """Test access to team-level (anonymized) data"""

        def test_manager_can_view_team_aggregates(
            self, permission_service, manager_member
        ):
            """Managers should be able to view team aggregates"""
            can_view = permission_service.can_view_team_aggregates(manager_member)
            assert can_view is True

        def test_employee_cannot_view_team_aggregates(
            self, permission_service, employee_member
        ):
            """Employees should NOT be able to view team aggregates"""
            can_view = permission_service.can_view_team_aggregates(employee_member)
            assert can_view is False

        def test_admin_can_view_team_aggregates(self, permission_service, admin_member):
            """Admins should be able to view team aggregates"""
            can_view = permission_service.can_view_team_aggregates(admin_member)
            assert can_view is True

    class TestAuditLogging:
        """Test audit logging functionality"""

        def test_data_access_is_logged(self, mock_db):
            """Every data access should be logged via the static method"""
            target_hash = "target_employee_hash"

            PermissionService.log_data_access(
                mock_db,
                actor_hash="manager_hash_456",
                actor_role="manager",
                target_hash=target_hash,
                action="view",
                tenant_id=str(TENANT_ID),
                details={"test": True},
            )

            # Verify audit log was created
            mock_db.add.assert_called_once()
            mock_db.flush.assert_called_once()

            # Verify the log contains expected data
            call_args = mock_db.add.call_args[0][0]
            assert isinstance(call_args, AuditLog)
            assert call_args.action == "view"
            assert call_args.user_hash == target_hash
            assert call_args.actor_hash == "manager_hash_456"

    class TestPermissionMatrix:
        """Test the complete permission matrix"""

        def test_permission_matrix_completeness(self):
            """Verify all expected permissions exist"""
            expected_permissions = [
                "view_own_risk",
                "view_own_velocity",
                "view_team_aggregates",
                "reveal_team_identity",
                "pause_own_monitoring",
                "delete_own_data",
                "run_simulation",
                "configure_thresholds",
                "view_audit_logs",
                "view_system_health",
            ]

            for perm in expected_permissions:
                assert perm in PermissionService.PERMISSIONS

        def test_employee_permissions(self, permission_service, employee_member):
            """Employees should have limited permissions"""
            assert permission_service.check_permission(employee_member, "view_own_risk")
            assert permission_service.check_permission(
                employee_member, "pause_own_monitoring"
            )
            assert permission_service.check_permission(employee_member, "delete_own_data")
            assert not permission_service.check_permission(
                employee_member, "view_team_aggregates"
            )
            assert not permission_service.check_permission(
                employee_member, "run_simulation"
            )
            assert not permission_service.check_permission(
                employee_member, "configure_thresholds"
            )

        def test_manager_permissions(self, permission_service, manager_member):
            """Managers should have team-level permissions"""
            assert permission_service.check_permission(manager_member, "view_own_risk")
            assert permission_service.check_permission(
                manager_member, "view_team_aggregates"
            )
            assert permission_service.check_permission(manager_member, "run_simulation")
            assert not permission_service.check_permission(
                manager_member, "configure_thresholds"
            )
            assert not permission_service.check_permission(
                manager_member, "view_audit_logs"
            )

        def test_admin_permissions(self, permission_service, admin_member):
            """Admins should have all permissions"""
            for perm in PermissionService.PERMISSIONS:
                assert permission_service.check_permission(admin_member, perm)


class TestIntegrationScenarios:
    """Integration tests for real-world scenarios"""

    @pytest.fixture
    def mock_db(self):
        return MagicMock()

    @pytest.fixture
    def permission_service(self, mock_db):
        return PermissionService(mock_db)

    def test_scenario_employee_checks_own_wellness(self, permission_service, mock_db):
        """Employee should be able to check their own wellness dashboard"""
        employee = _make_member("emp_wellness_check", role="employee")

        can_view, reason = permission_service.can_view_user_data(
            mock_db, employee, employee.user_hash
        )
        assert can_view is True

    def test_scenario_manager_team_health_dashboard(self, permission_service, mock_db):
        """Manager should see team health without individual identification"""
        manager = _make_member("mgr_team_health", role="manager")

        # Manager should be able to view team aggregates
        assert permission_service.can_view_team_aggregates(manager)

    def test_scenario_emergency_intervention(self, permission_service, mock_db):
        """Emergency: Manager can see critical employee details without consent"""
        manager = _make_member("mgr_emergency", role="manager")
        employee_hash = "emp_critical_emergency"

        # Mock: employee TenantMember in same team
        employee_tm = _make_member(employee_hash, role="employee", team_id=TEAM_ID)

        # Mock: employee UserIdentity without consent
        employee_identity = Mock(spec=UserIdentity)
        employee_identity.user_hash = employee_hash
        employee_identity.consent_share_with_manager = False

        # Setup: Risk data showing 40 hours of critical status
        critical_risk = Mock(spec=RiskScore)
        critical_risk.risk_level = "CRITICAL"
        critical_risk.updated_at = datetime.utcnow() - timedelta(hours=40)

        old_history = Mock(spec=RiskHistory)
        old_history.risk_level = "CRITICAL"
        old_history.timestamp = datetime.utcnow() - timedelta(hours=38)

        call_count = 0

        def mock_query(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_q = Mock()
            if call_count == 1:
                # TenantMember lookup
                mock_q.filter_by.return_value.first.return_value = employee_tm
            elif call_count == 2:
                # UserIdentity lookup
                mock_q.filter_by.return_value.first.return_value = employee_identity
            elif call_count == 3:
                # RiskScore lookup (is_critical_for_36h)
                mock_q.filter_by.return_value.first.return_value = critical_risk
            else:
                # RiskHistory lookup (is_critical_for_36h)
                mock_q.filter.return_value.order_by.return_value.all.return_value = [
                    old_history
                ]
            return mock_q

        mock_db.query.side_effect = mock_query

        can_view, reason = permission_service.can_view_user_data(
            mock_db, manager, employee_hash
        )

        assert can_view is True
        assert "EMERGENCY" in reason or "critical" in reason.lower()


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
