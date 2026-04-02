"""
Tests for POST /team/reveal-identity endpoint.

Covers four scenarios:
1. Manager reveals team member (can_manager_view_employee -> True)
2. Manager denied for non-team member (both checks -> False) -> 403
3. 36h CRITICAL override (can_manager -> False, is_critical -> True)
4. Admin can always reveal
"""

import pytest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException, Request

from app.api.v1.endpoints.identity_reveal import reveal_identity, RevealRequest
from app.models.identity import UserIdentity
from app.models.tenant import TenantMember


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_member(role: str = "manager", tenant_id=None, user_hash="mgr_hash_001"):
    """Build a minimal TenantMember mock."""
    tid = tenant_id or uuid4()
    member = MagicMock(spec=TenantMember)
    member.role = role
    member.tenant_id = tid
    member.user_hash = user_hash
    member.team_id = uuid4()
    return member


def _make_target(user_hash: str = "target_hash_001", tenant_id=None):
    """Build a minimal UserIdentity mock."""
    target = MagicMock(spec=UserIdentity)
    target.user_hash = user_hash
    target.tenant_id = tenant_id
    target.email_encrypted = b"encrypted_bytes"
    return target


def _make_request():
    """Build a minimal ASGI Request mock with client IP."""
    req = MagicMock(spec=Request)
    req.client = MagicMock()
    req.client.host = "127.0.0.1"
    return req


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestManagerRevealsTeamMember:
    """Scenario 1: Manager can reveal identity when can_manager_view_employee returns True."""

    @patch("app.api.v1.endpoints.identity_reveal.PermissionService")
    def test_manager_reveal_granted(self, MockPermSvc):
        tenant_id = uuid4()
        member = _make_member(role="manager", tenant_id=tenant_id)
        target = _make_target(user_hash="target_hash_001", tenant_id=tenant_id)

        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = target

        # Configure the PermissionService instance
        perm_instance = MockPermSvc.return_value
        perm_instance.can_manager_view_employee.return_value = (
            True,
            "Employee has consented to share data",
        )

        body = RevealRequest(target_user_hash="target_hash_001")
        request = _make_request()

        result = reveal_identity(body=body, request=request, member=member, db=db)

        assert result["revealed"] is True
        assert result["reveal_reason"] == "team_member"
        assert result["user_hash"] == "target_hash_001"
        assert result["has_encrypted_identity"] is True

        # Verify audit log was written
        MockPermSvc.log_data_access.assert_called_once()
        call_kwargs = MockPermSvc.log_data_access.call_args
        assert call_kwargs.kwargs["action"] == "manager_identity_reveal"
        assert call_kwargs.kwargs["actor_hash"] == "mgr_hash_001"


class TestManagerDeniedNonTeamMember:
    """Scenario 2: Manager denied when both checks fail -> 403."""

    @patch("app.api.v1.endpoints.identity_reveal.PermissionService")
    def test_manager_denied_403(self, MockPermSvc):
        tenant_id = uuid4()
        member = _make_member(role="manager", tenant_id=tenant_id)
        target = _make_target(user_hash="stranger_hash", tenant_id=tenant_id)

        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = target

        perm_instance = MockPermSvc.return_value
        perm_instance.can_manager_view_employee.return_value = (
            False,
            "Not your direct report",
        )
        perm_instance.is_critical_for_36h.return_value = False

        body = RevealRequest(target_user_hash="stranger_hash")
        request = _make_request()

        with pytest.raises(HTTPException) as exc_info:
            reveal_identity(body=body, request=request, member=member, db=db)

        assert exc_info.value.status_code == 403
        assert "denied" in exc_info.value.detail.lower()

        # Audit log should NOT be written (access was denied)
        MockPermSvc.log_data_access.assert_not_called()


class TestCritical36hOverride:
    """Scenario 3: Manager initially denied, but 36h critical override applies."""

    @patch("app.api.v1.endpoints.identity_reveal.PermissionService")
    def test_critical_override_grants_access(self, MockPermSvc):
        tenant_id = uuid4()
        member = _make_member(role="manager", tenant_id=tenant_id)
        target = _make_target(user_hash="critical_employee", tenant_id=tenant_id)

        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = target

        perm_instance = MockPermSvc.return_value
        perm_instance.can_manager_view_employee.return_value = (
            False,
            "No consent and no emergency condition",
        )
        perm_instance.is_critical_for_36h.return_value = True

        body = RevealRequest(target_user_hash="critical_employee")
        request = _make_request()

        result = reveal_identity(body=body, request=request, member=member, db=db)

        assert result["revealed"] is True
        assert result["reveal_reason"] == "critical_36h_override"
        assert result["user_hash"] == "critical_employee"

        # Verify audit includes override flag
        MockPermSvc.log_data_access.assert_called_once()
        call_kwargs = MockPermSvc.log_data_access.call_args
        assert call_kwargs.kwargs["action"] == "critical_36h_identity_reveal"
        assert call_kwargs.kwargs["details"]["override"] is True


class TestAdminAlwaysReveals:
    """Scenario 4: Admin can always reveal without any permission checks."""

    @patch("app.api.v1.endpoints.identity_reveal.PermissionService")
    def test_admin_reveal(self, MockPermSvc):
        tenant_id = uuid4()
        member = _make_member(role="admin", tenant_id=tenant_id, user_hash="admin_hash")
        target = _make_target(user_hash="any_employee", tenant_id=tenant_id)

        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = target

        body = RevealRequest(target_user_hash="any_employee")
        request = _make_request()

        result = reveal_identity(body=body, request=request, member=member, db=db)

        assert result["revealed"] is True
        assert result["reveal_reason"] == "admin_access"
        assert result["user_hash"] == "any_employee"
        assert result["has_encrypted_identity"] is True

        # Audit log must be written for admin access
        MockPermSvc.log_data_access.assert_called_once()
        call_kwargs = MockPermSvc.log_data_access.call_args
        assert call_kwargs.kwargs["action"] == "admin_identity_reveal"
        assert call_kwargs.kwargs["actor_role"] == "admin"

        # PermissionService instance methods should NOT have been called
        perm_instance = MockPermSvc.return_value
        perm_instance.can_manager_view_employee.assert_not_called()
        perm_instance.is_critical_for_36h.assert_not_called()


class TestTargetNotFound:
    """Edge case: target user_hash does not exist in this tenant -> 404."""

    @patch("app.api.v1.endpoints.identity_reveal.PermissionService")
    def test_target_not_found_404(self, MockPermSvc):
        tenant_id = uuid4()
        member = _make_member(role="admin", tenant_id=tenant_id)

        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = None

        body = RevealRequest(target_user_hash="nonexistent_hash")
        request = _make_request()

        with pytest.raises(HTTPException) as exc_info:
            reveal_identity(body=body, request=request, member=member, db=db)

        assert exc_info.value.status_code == 404
