"""
Tests for /team endpoints (Manager Team View)
Run with: pytest tests/test_team_endpoints.py -v
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock
from fastapi import HTTPException

from app.api.v1.endpoints.team import (
    get_my_team_dashboard,
    get_team_member_details,
    get_team_analytics,
    anonymize_user_hash,
)
from app.models.identity import UserIdentity
from app.models.analytics import RiskScore, RiskHistory


class TestTeamEndpoints:
    """Test suite for /team endpoints"""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session"""
        return MagicMock()

    @pytest.fixture
    def manager_user(self):
        """Create a manager user fixture"""
        user = Mock(spec=UserIdentity)
        user.user_hash = "mgr_test_hash_456"
        user.role = "manager"
        return user

    @pytest.fixture
    def admin_user(self):
        """Create an admin user fixture"""
        user = Mock(spec=UserIdentity)
        user.user_hash = "admin_test_hash_789"
        user.role = "admin"
        return user

    class TestAnonymization:
        """Test anonymization helper functions"""

        def test_anonymize_user_hash(self):
            """Should convert hash to User A, B, C, etc."""
            assert anonymize_user_hash("abc123", 0) == "User A"
            assert anonymize_user_hash("def456", 1) == "User B"
            assert anonymize_user_hash("ghi789", 2) == "User C"

    class TestGetTeamDashboard:
        """Test GET /team endpoint"""

        def test_returns_team_overview(self, mock_db, manager_user):
            """Should return team dashboard with anonymized data"""
            # Setup: Create team members
            emp1 = Mock(spec=UserIdentity)
            emp1.user_hash = "emp1_hash"
            emp1.manager_hash = manager_user.user_hash
            emp1.consent_share_with_manager = True

            emp2 = Mock(spec=UserIdentity)
            emp2.user_hash = "emp2_hash"
            emp2.manager_hash = manager_user.user_hash
            emp2.consent_share_with_manager = False

            mock_db.query.return_value.filter.return_value.all.return_value = [
                emp1,
                emp2,
            ]

            # Setup: Risk scores
            risk1 = Mock(spec=RiskScore)
            risk1.user_hash = "emp1_hash"
            risk1.risk_level = "ELEVATED"

            risk2 = Mock(spec=RiskScore)
            risk2.user_hash = "emp2_hash"
            risk2.risk_level = "LOW"

            mock_db.query.return_value.filter.return_value.all.return_value = [
                risk1,
                risk2,
            ]

            # Call endpoint
            result = get_my_team_dashboard(manager_user, mock_db)

            # Assertions
            assert result["team"]["member_count"] == 2
            assert result["metrics"]["total_members"] == 2
            assert result["consent_summary"]["consented"] == 1
            assert result["consent_summary"]["not_consented"] == 1

        def test_handles_no_team_members(self, mock_db, manager_user):
            """Should handle manager with no team"""
            mock_db.query.return_value.filter.return_value.all.return_value = []

            result = get_my_team_dashboard(manager_user, mock_db)

            assert result["team"]["member_count"] == 0
            assert "No team members" in result["team"]["message"]

    class TestGetTeamMemberDetails:
        """Test GET /team/member/{hash} endpoint"""

        def test_grants_access_with_consent(self, mock_db, manager_user):
            """Should grant access when employee has consented"""
            # Setup: Employee with consent
            employee = Mock(spec=UserIdentity)
            employee.user_hash = "emp_consent_hash"
            employee.manager_hash = manager_user.user_hash
            employee.consent_share_with_manager = True
            employee.monitoring_paused_until = None

            mock_db.query.return_value.filter_by.return_value.first.return_value = (
                employee
            )
            mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

            # Call endpoint
            result = get_team_member_details("emp_consent_hash", manager_user, mock_db)

            # Assertions
            assert result["access"] == "granted"
            assert result["employee"]["is_identified"] is True

        def test_denies_access_without_consent(self, mock_db, manager_user):
            """Should deny access when no consent and no emergency"""
            # Setup: Employee without consent
            employee = Mock(spec=UserIdentity)
            employee.user_hash = "emp_no_consent_hash"
            employee.manager_hash = manager_user.user_hash
            employee.consent_share_with_manager = False

            # Setup: Not at critical risk
            mock_db.query.return_value.filter_by.side_effect = [
                Mock(first=Mock(return_value=employee)),  # Employee query
                Mock(first=Mock(return_value=Mock(risk_level="LOW"))),  # Risk query
            ]

            # Call endpoint
            result = get_team_member_details(
                "emp_no_consent_hash", manager_user, mock_db
            )

            # Should return anonymized/denied response
            assert result["access"] == "denied"
            assert "not consented" in result["reason"].lower()

        def test_rejects_non_team_member(self, mock_db, manager_user):
            """Should reject if employee doesn't report to this manager"""
            # Setup: Employee with different manager
            employee = Mock(spec=UserIdentity)
            employee.user_hash = "emp_other_hash"
            employee.manager_hash = "other_manager_hash"  # Different manager

            mock_db.query.return_value.filter_by.return_value.first.return_value = (
                employee
            )

            # Should raise 403
            with pytest.raises(HTTPException) as exc_info:
                get_team_member_details("emp_other_hash", manager_user, mock_db)

            assert exc_info.value.status_code == 403

    class TestGetTeamAnalytics:
        """Test GET /team/analytics endpoint"""

        def test_calculates_team_metrics(self, mock_db, manager_user):
            """Should calculate team health metrics"""
            # Setup: Team members
            emp1 = Mock(spec=UserIdentity)
            emp1.user_hash = "emp1_hash"
            emp1.manager_hash = manager_user.user_hash

            mock_db.query.return_value.filter.return_value.all.return_value = [emp1]

            # Setup: Risk history
            history1 = Mock(spec=RiskHistory)
            history1.user_hash = "emp1_hash"
            history1.timestamp = datetime.utcnow() - timedelta(days=1)
            history1.velocity = 1.5
            history1.risk_level = "ELEVATED"

            mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
                history1
            ]

            # Setup: Current risk
            risk1 = Mock(spec=RiskScore)
            risk1.user_hash = "emp1_hash"
            risk1.velocity = 1.5
            risk1.risk_level = "ELEVATED"

            mock_db.query.return_value.filter.return_value.all.return_value = [risk1]

            # Call endpoint
            result = get_team_analytics(30, manager_user, mock_db)

            # Assertions
            assert result["period_days"] == 30
            assert result["team_size"] == 1
            assert "health_score" in result
            assert "current_metrics" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
