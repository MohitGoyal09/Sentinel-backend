"""
Comprehensive tests for the PermissionService and RBAC system.
Run with: pytest tests/test_permissions.py -v
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock

from app.services.permission_service import (
    PermissionService,
    UserRole,
    PermissionDenied,
    NotFound,
)
from app.models.identity import UserIdentity, AuditLog
from app.models.analytics import RiskScore, RiskHistory


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
    def employee_user(self):
        """Create an employee user fixture"""
        user = Mock(spec=UserIdentity)
        user.user_hash = "employee_hash_123"
        user.role = UserRole.EMPLOYEE.value
        user.consent_share_with_manager = False
        user.manager_hash = None
        return user

    @pytest.fixture
    def manager_user(self):
        """Create a manager user fixture"""
        user = Mock(spec=UserIdentity)
        user.user_hash = "manager_hash_456"
        user.role = UserRole.MANAGER.value
        user.consent_share_with_manager = False
        user.manager_hash = None
        return user

    @pytest.fixture
    def admin_user(self):
        """Create an admin user fixture"""
        user = Mock(spec=UserIdentity)
        user.user_hash = "admin_hash_789"
        user.role = UserRole.ADMIN.value
        user.consent_share_with_manager = False
        user.manager_hash = None
        return user

    class TestBasicPermissions:
        """Test basic permission checks"""

        def test_employee_can_view_own_data(self, permission_service, employee_user):
            """Employees should be able to view their own data"""
            can_view, reason = permission_service.can_view_user_data(
                employee_user, employee_user.user_hash
            )
            assert can_view is True
            assert "own data" in reason.lower()

        def test_employee_cannot_view_others_data(
            self, permission_service, employee_user
        ):
            """Employees should NOT be able to view other users' data"""
            other_hash = "other_employee_hash"
            can_view, reason = permission_service.can_view_user_data(
                employee_user, other_hash
            )
            assert can_view is False
            assert "own data" in reason.lower()

        def test_admin_can_view_any_data(self, permission_service, admin_user):
            """Admins should be able to view anyone's data"""
            any_hash = "any_user_hash"
            can_view, reason = permission_service.can_view_user_data(
                admin_user, any_hash
            )
            assert can_view is True
            assert "admin" in reason.lower()

        def test_manager_cannot_view_unrelated_employee(
            self, permission_service, manager_user, mock_db
        ):
            """Manager should NOT view employee who doesn't report to them"""
            # Setup: Create unrelated employee
            unrelated_employee = Mock(spec=UserIdentity)
            unrelated_employee.user_hash = "unrelated_hash"
            unrelated_employee.manager_hash = "different_manager_hash"
            unrelated_employee.consent_share_with_manager = False

            mock_db.query.return_value.filter_by.return_value.first.return_value = (
                unrelated_employee
            )

            can_view, reason = permission_service.can_view_user_data(
                manager_user, unrelated_employee.user_hash
            )
            assert can_view is False
            assert "Not your direct report" in reason

    class TestConsentBasedAccess:
        """Test consent-based data access for managers"""

        def test_manager_can_view_consented_employee(
            self, permission_service, manager_user, mock_db
        ):
            """Manager should view employee who has consented"""
            # Setup: Create employee who consented and reports to this manager
            employee = Mock(spec=UserIdentity)
            employee.user_hash = "consented_employee_hash"
            employee.manager_hash = manager_user.user_hash
            employee.consent_share_with_manager = True

            mock_db.query.return_value.filter_by.return_value.first.return_value = (
                employee
            )

            can_view, reason = permission_service.can_view_user_data(
                manager_user, employee.user_hash
            )
            assert can_view is True
            assert "consented" in reason.lower()

        def test_manager_cannot_view_non_consented_employee(
            self, permission_service, manager_user, mock_db
        ):
            """Manager should NOT view employee who hasn't consented (no emergency)"""
            # Setup: Create employee who hasn't consented
            employee = Mock(spec=UserIdentity)
            employee.user_hash = "non_consented_hash"
            employee.manager_hash = manager_user.user_hash
            employee.consent_share_with_manager = False

            mock_db.query.return_value.filter_by.return_value.first.return_value = (
                employee
            )

            # Mock: Employee is not at critical risk
            mock_db.query.return_value.filter_by.side_effect = [
                Mock(
                    first=Mock(return_value=Mock(risk_level="LOW"))
                ),  # RiskScore query
            ]

            can_view, reason = permission_service.can_view_user_data(
                manager_user, employee.user_hash
            )
            assert can_view is False
            assert "No consent" in reason or "consent" in reason.lower()

    class Test36HourCriticalRule:
        """Test the emergency 36-hour critical risk override"""

        def test_manager_can_view_critical_employee_after_36h(
            self, permission_service, manager_user, mock_db
        ):
            """Manager CAN view employee at CRITICAL risk for 36+ hours"""
            # Setup: Employee hasn't consented but is at critical risk
            employee = Mock(spec=UserIdentity)
            employee.user_hash = "critical_employee_hash"
            employee.manager_hash = manager_user.user_hash
            employee.consent_share_with_manager = False

            mock_db.query.return_value.filter_by.return_value.first.return_value = (
                employee
            )

            # Setup: Risk has been critical for 36+ hours
            critical_risk = Mock(spec=RiskScore)
            critical_risk.risk_level = "CRITICAL"
            critical_risk.updated_at = datetime.utcnow() - timedelta(hours=40)

            # Setup: Risk history showing continuous critical status
            old_history = Mock(spec=RiskHistory)
            old_history.risk_level = "CRITICAL"
            old_history.timestamp = datetime.utcnow() - timedelta(hours=37)

            # Mock the database queries
            def mock_query_side_effect(*args, **kwargs):
                mock_query = Mock()
                if (
                    hasattr(args[0], "__tablename__")
                    and args[0].__tablename__ == "risk_scores"
                ):
                    mock_query.filter_by.return_value.first.return_value = critical_risk
                elif (
                    hasattr(args[0], "__tablename__")
                    and args[0].__tablename__ == "risk_history"
                ):
                    mock_query.filter.return_value.order_by.return_value.all.return_value = [
                        old_history
                    ]
                else:
                    mock_query.filter_by.return_value.first.return_value = employee
                return mock_query

            mock_db.query.side_effect = mock_query_side_effect

            can_view, reason = permission_service.can_view_user_data(
                manager_user, employee.user_hash
            )
            assert can_view is True
            assert "EMERGENCY" in reason or "critical" in reason.lower()

        def test_manager_cannot_view_critical_employee_before_36h(
            self, permission_service, manager_user, mock_db
        ):
            """Manager CANNOT view employee at CRITICAL risk for less than 36 hours"""
            # Setup: Employee at critical risk but only for 12 hours
            employee = Mock(spec=UserIdentity)
            employee.user_hash = "recent_critical_hash"
            employee.manager_hash = manager_user.user_hash
            employee.consent_share_with_manager = False

            mock_db.query.return_value.filter_by.return_value.first.return_value = (
                employee
            )

            # Setup: Risk is critical but only for 12 hours
            recent_critical = Mock(spec=RiskScore)
            recent_critical.risk_level = "CRITICAL"
            recent_critical.updated_at = datetime.utcnow() - timedelta(hours=12)

            mock_db.query.return_value.filter_by.return_value.first.return_value = (
                recent_critical
            )

            can_view, reason = permission_service.can_view_user_data(
                manager_user, employee.user_hash
            )
            assert can_view is False

    class TestTeamAggregates:
        """Test access to team-level (anonymized) data"""

        def test_manager_can_view_team_aggregates(
            self, permission_service, manager_user
        ):
            """Managers should be able to view team aggregates"""
            can_view = permission_service.can_view_team_aggregates(manager_user)
            assert can_view is True

        def test_employee_cannot_view_team_aggregates(
            self, permission_service, employee_user
        ):
            """Employees should NOT be able to view team aggregates"""
            can_view = permission_service.can_view_team_aggregates(employee_user)
            assert can_view is False

        def test_admin_can_view_team_aggregates(self, permission_service, admin_user):
            """Admins should be able to view team aggregates"""
            can_view = permission_service.can_view_team_aggregates(admin_user)
            assert can_view is True

    class TestAuditLogging:
        """Test audit logging functionality"""

        def test_data_access_is_logged(self, permission_service, manager_user, mock_db):
            """Every data access should be logged"""
            target_hash = "target_employee_hash"

            permission_service.log_data_access(
                accessor_hash=manager_user.user_hash,
                target_hash=target_hash,
                action="view",
                details={"test": True},
            )

            # Verify audit log was created
            mock_db.add.assert_called_once()
            mock_db.commit.assert_called_once()

            # Verify the log contains expected data
            call_args = mock_db.add.call_args[0][0]
            assert isinstance(call_args, AuditLog)
            assert call_args.action == "data_access:view"
            assert call_args.user_hash == target_hash
            assert "accessor_hash" in call_args.details

    class TestPermissionMatrix:
        """Test the complete permission matrix"""

        def test_permission_matrix_completeness(self):
            """Verify all expected permissions exist"""
            expected_permissions = [
                "view_own_risk",
                "view_own_velocity",
                "view_team_aggregates",
                "view_individual_details",
                "pause_monitoring",
                "delete_own_data",
                "run_simulation",
                "configure_thresholds",
                "view_audit_logs",
                "view_system_health",
            ]

            for perm in expected_permissions:
                assert perm in PermissionService.PERMISSIONS

        def test_employee_permissions(self, permission_service, employee_user):
            """Employees should have limited permissions"""
            assert permission_service.check_permission(employee_user, "view_own_risk")
            assert permission_service.check_permission(
                employee_user, "pause_monitoring"
            )
            assert permission_service.check_permission(employee_user, "delete_own_data")
            assert not permission_service.check_permission(
                employee_user, "view_team_aggregates"
            )
            assert not permission_service.check_permission(
                employee_user, "run_simulation"
            )
            assert not permission_service.check_permission(
                employee_user, "configure_thresholds"
            )

        def test_manager_permissions(self, permission_service, manager_user):
            """Managers should have team-level permissions"""
            assert permission_service.check_permission(manager_user, "view_own_risk")
            assert permission_service.check_permission(
                manager_user, "view_team_aggregates"
            )
            assert permission_service.check_permission(manager_user, "run_simulation")
            assert not permission_service.check_permission(
                manager_user, "configure_thresholds"
            )
            assert not permission_service.check_permission(
                manager_user, "view_audit_logs"
            )

        def test_admin_permissions(self, permission_service, admin_user):
            """Admins should have all permissions"""
            for perm in PermissionService.PERMISSIONS:
                assert permission_service.check_permission(admin_user, perm)


class TestIntegrationScenarios:
    """Integration tests for real-world scenarios"""

    def test_scenario_employee_checks_own_wellness(self, permission_service, mock_db):
        """Employee should be able to check their own wellness dashboard"""
        employee = Mock(spec=UserIdentity)
        employee.user_hash = "emp_wellness_check"
        employee.role = UserRole.EMPLOYEE.value

        can_view, reason = permission_service.can_view_user_data(
            employee, employee.user_hash
        )
        assert can_view is True

    def test_scenario_manager_team_health_dashboard(self, permission_service, mock_db):
        """Manager should see team health without individual identification"""
        manager = Mock(spec=UserIdentity)
        manager.user_hash = "mgr_team_health"
        manager.role = UserRole.MANAGER.value

        # Manager should be able to view team aggregates
        assert permission_service.can_view_team_aggregates(manager)

    def test_scenario_emergency_intervention(self, permission_service, mock_db):
        """Emergency: Manager can see critical employee details without consent"""
        # Setup: Manager
        manager = Mock(spec=UserIdentity)
        manager.user_hash = "mgr_emergency"
        manager.role = UserRole.MANAGER.value

        # Setup: Employee in critical condition for 40 hours
        employee = Mock(spec=UserIdentity)
        employee.user_hash = "emp_critical_emergency"
        employee.manager_hash = manager.user_hash
        employee.consent_share_with_manager = False

        mock_db.query.return_value.filter_by.return_value.first.return_value = employee

        # Setup: Risk data showing 40 hours of critical status
        critical_risk = Mock(spec=RiskScore)
        critical_risk.risk_level = "CRITICAL"
        critical_risk.updated_at = datetime.utcnow() - timedelta(hours=40)

        old_history = Mock(spec=RiskHistory)
        old_history.risk_level = "CRITICAL"
        old_history.timestamp = datetime.utcnow() - timedelta(hours=38)

        # Mock queries
        call_count = 0

        def mock_query(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_q = Mock()
            if call_count == 1:
                mock_q.filter_by.return_value.first.return_value = employee
            elif call_count == 2:
                mock_q.filter_by.return_value.first.return_value = critical_risk
            else:
                mock_q.filter.return_value.order_by.return_value.all.return_value = [
                    old_history
                ]
            return mock_q

        mock_db.query.side_effect = mock_query

        can_view, reason = permission_service.can_view_user_data(
            manager, employee.user_hash
        )

        assert can_view is True
        assert "EMERGENCY" in reason or "critical" in reason.lower()


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
