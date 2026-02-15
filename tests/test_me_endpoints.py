"""
Tests for /me endpoints (Employee Self-Service)
Run with: pytest tests/test_me_endpoints.py -v
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock, patch
from fastapi import HTTPException

from app.api.v1.endpoints.me import (
    get_my_profile,
    update_my_consent,
    pause_my_monitoring,
    resume_my_monitoring,
    delete_my_data,
    ConsentUpdate,
)
from app.models.identity import UserIdentity, AuditLog
from app.models.analytics import RiskScore, RiskHistory


class TestMeEndpoints:
    """Test suite for /me endpoints"""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session"""
        return MagicMock()

    @pytest.fixture
    def employee_user(self):
        """Create an employee user fixture"""
        user = Mock(spec=UserIdentity)
        user.user_hash = "emp_test_hash_123"
        user.role = "employee"
        user.consent_share_with_manager = False
        user.consent_share_anonymized = True
        user.monitoring_paused_until = None
        user.created_at = datetime.utcnow()
        return user

    class TestGetMyProfile:
        """Test GET /me endpoint"""

        def test_returns_user_profile(self, mock_db, employee_user):
            """Should return user profile with risk data and audit trail"""
            # Setup mock risk score
            mock_risk = Mock(spec=RiskScore)
            mock_risk.velocity = 1.5
            mock_risk.risk_level = "ELEVATED"
            mock_risk.confidence = 0.85
            mock_risk.thwarted_belongingness = 0.3
            mock_risk.updated_at = datetime.utcnow()

            mock_db.query.return_value.filter_by.return_value.first.return_value = (
                mock_risk
            )
            mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

            # Call endpoint
            result = get_my_profile(employee_user, mock_db)

            # Assertions
            assert result["user"]["user_hash"] == employee_user.user_hash
            assert result["risk"]["risk_level"] == "ELEVATED"
            assert result["monitoring_status"]["is_paused"] is False

        def test_handles_no_risk_data(self, mock_db, employee_user):
            """Should handle case when no risk data exists yet"""
            mock_db.query.return_value.filter_by.return_value.first.return_value = None
            mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

            result = get_my_profile(employee_user, mock_db)

            assert result["risk"] is None
            assert result["user"]["role"] == "employee"

    class TestUpdateConsent:
        """Test PUT /me/consent endpoint"""

        def test_updates_manager_consent(self, mock_db, employee_user):
            """Should update consent_share_with_manager"""
            body = ConsentUpdate(consent_share_with_manager=True)
            result = update_my_consent(
                body=body,
                current_user=employee_user,
                db=mock_db,
            )

            assert employee_user.consent_share_with_manager is True
            assert result["consent"]["consent_share_with_manager"] is True
            mock_db.commit.assert_called_once()

        def test_updates_anonymized_consent(self, mock_db, employee_user):
            """Should update consent_share_anonymized"""
            body = ConsentUpdate(consent_share_anonymized=False)
            result = update_my_consent(
                body=body,
                current_user=employee_user,
                db=mock_db,
            )

            assert employee_user.consent_share_anonymized is False
            assert result["consent"]["consent_share_anonymized"] is False

        def test_rejects_no_changes(self, mock_db, employee_user):
            """Should reject request if no consent settings provided"""
            with pytest.raises(HTTPException) as exc_info:
                body = ConsentUpdate()
                update_my_consent(
                    body=body,
                    current_user=employee_user,
                    db=mock_db,
                )

            assert exc_info.value.status_code == 400
            assert "No consent settings" in str(exc_info.value.detail)

    class TestPauseMonitoring:
        """Test POST /me/pause-monitoring endpoint"""

        def test_pauses_for_specified_hours(self, mock_db, employee_user):
            """Should pause monitoring for specified duration"""
            result = pause_my_monitoring(
                hours=24, current_user=employee_user, db=mock_db
            )

            assert employee_user.monitoring_paused_until is not None
            assert result["message"] == "Monitoring paused for 24 hours"
            mock_db.commit.assert_called_once()

        def test_rejects_invalid_duration(self, mock_db, employee_user):
            """Should reject invalid pause durations"""
            with pytest.raises(HTTPException) as exc_info:
                pause_my_monitoring(hours=0, current_user=employee_user, db=mock_db)

            assert exc_info.value.status_code == 400

        def test_rejects_too_long_duration(self, mock_db, employee_user):
            """Should reject pause durations over 168 hours"""
            with pytest.raises(HTTPException) as exc_info:
                pause_my_monitoring(hours=200, current_user=employee_user, db=mock_db)

            assert exc_info.value.status_code == 400
            assert "168 hours" in str(exc_info.value.detail)

    class TestResumeMonitoring:
        """Test POST /me/resume-monitoring endpoint"""

        def test_resumes_monitoring(self, mock_db, employee_user):
            """Should resume monitoring immediately"""
            employee_user.monitoring_paused_until = datetime.utcnow() + timedelta(
                hours=24
            )

            result = resume_my_monitoring(current_user=employee_user, db=mock_db)

            assert employee_user.monitoring_paused_until is None
            assert result["message"] == "Monitoring resumed"
            mock_db.commit.assert_called_once()

    class TestDeleteData:
        """Test DELETE /me/data endpoint"""

        def test_deletes_all_data_with_confirmation(self, mock_db, employee_user):
            """Should delete all data when confirmed"""
            result = delete_my_data(
                confirm=True, current_user=employee_user, db=mock_db
            )

            # Should delete from multiple tables
            assert (
                mock_db.query.return_value.filter_by.return_value.delete.call_count >= 3
            )
            mock_db.commit.assert_called_once()
            assert "deleted successfully" in result["message"]

        def test_rejects_without_confirmation(self, mock_db, employee_user):
            """Should reject deletion without confirmation"""
            with pytest.raises(HTTPException) as exc_info:
                delete_my_data(confirm=False, current_user=employee_user, db=mock_db)

            assert exc_info.value.status_code == 400
            assert "confirm=true" in str(exc_info.value.detail)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
