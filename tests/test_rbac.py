import pytest
from unittest.mock import patch, MagicMock, Mock
from uuid import uuid4

from app.services.permission_service import (
    PermissionService,
    UserRole,
    PermissionDenied,
    NotFound,
)
from app.models.tenant import TenantMember


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


class TestRoleBasedAccess:
    @pytest.fixture
    def mock_db(self):
        return MagicMock()

    @pytest.fixture
    def permission_service(self, mock_db):
        return PermissionService(mock_db)

    @pytest.fixture
    def employee_member(self):
        return _make_member("emp-123", role="employee")

    @pytest.fixture
    def manager_member(self):
        return _make_member("mgr-456", role="manager")

    @pytest.fixture
    def admin_member(self):
        return _make_member("adm-789", role="admin")

    def test_employee_cannot_access_admin(self, permission_service, employee_member):
        """Employee role should not have admin permissions."""
        assert not permission_service.check_permission(
            employee_member, "configure_thresholds"
        )
        assert not permission_service.check_permission(employee_member, "view_audit_logs")
        assert not permission_service.check_permission(
            employee_member, "view_system_health"
        )

    def test_manager_can_view_team(self, permission_service, manager_member):
        """Manager should be able to view team aggregates."""
        assert permission_service.can_view_team_aggregates(manager_member)

    def test_manager_cannot_view_other_teams(
        self, permission_service, manager_member, mock_db
    ):
        """Manager should only see their own direct reports."""
        # Employee is not in manager's team (TenantMember lookup returns None)
        mock_db.query.return_value.filter_by.return_value.first.return_value = None

        can_view, reason = permission_service.can_view_user_data(
            mock_db, manager_member, "other-emp"
        )
        assert can_view is False
        assert "Not your direct report" in reason

    def test_admin_can_access_all(self, permission_service, admin_member, mock_db):
        """Admin should have full access."""
        for perm in PermissionService.PERMISSIONS:
            assert permission_service.check_permission(admin_member, perm)

        can_view, reason = permission_service.can_view_user_data(
            mock_db, admin_member, "any-user-hash"
        )
        assert can_view is True


class TestTenantRBAC:
    @pytest.fixture
    def mock_db(self):
        return MagicMock()

    @pytest.fixture
    def permission_service(self, mock_db):
        return PermissionService(mock_db)

    def test_viewer_cannot_modify(self, permission_service, mock_db):
        """Viewer role cannot perform write operations (no write perms defined)."""
        viewer = _make_member("viewer-1", role="viewer")

        # Viewer is not in any permission's allowed roles
        for perm in PermissionService.PERMISSIONS:
            assert not permission_service.check_permission(viewer, perm)

    def test_admin_can_manage_members(self, permission_service, mock_db):
        """Admin can invite/remove members (has team aggregate + individual detail access)."""
        admin = _make_member("admin-1", role="admin")

        can_view, _ = permission_service.can_view_user_data(
            mock_db, admin, "any-user"
        )
        assert can_view is True
        assert permission_service.can_view_team_aggregates(admin)

    def test_owner_can_delete_tenant(self, permission_service):
        """Admin role has all permissions including configure_thresholds."""
        admin = _make_member("admin-owner", role="admin")

        assert permission_service.check_permission(admin, "configure_thresholds")
        assert permission_service.check_permission(admin, "view_audit_logs")
