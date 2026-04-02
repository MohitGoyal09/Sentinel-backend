import pytest
import os
from unittest.mock import MagicMock, patch

# Set test env vars BEFORE any app imports
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("SUPABASE_URL", "http://localhost:8000")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("VAULT_SALT", "test-vault-salt-for-ci")
os.environ.setdefault("ENCRYPTION_KEY", "test-encryption-key-for-ci-testing")
os.environ.setdefault("ENVIRONMENT", "test")


@pytest.fixture
def mock_db():
    """Mock database session."""
    return MagicMock()


@pytest.fixture
def client():
    """Test client for the FastAPI app."""
    from fastapi.testclient import TestClient

    with patch("app.core.database.engine"):
        from app.main import app

        return TestClient(app)


@pytest.fixture
def sample_tenant():
    return {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "name": "Test Corp",
        "slug": "test-corp",
        "plan": "free",
        "status": "active",
    }


@pytest.fixture
def sample_user():
    return {
        "user_hash": "abc123def456",
        "role": "employee",
        "consent_share_with_manager": False,
        "consent_share_anonymized": True,
    }
