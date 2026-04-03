from pydantic_settings import BaseSettings
from pydantic import field_validator
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql://user:password@localhost:5432/sentinel"

    # Supabase Configuration
    supabase_url: str = ""
    supabase_key: str = ""
    supabase_anon_key: str = ""
    supabase_service_key: str = ""

    # Security
    vault_salt: str = ""
    encryption_key: str = ""
    openai_api_key: str = ""
    slack_bot_token: str = ""

    # LLM Configuration (Portkey AI Gateway)
    portkey_api_key: str = ""
    portkey_virtual_key: str = ""           # Primary model virtual key (set in Portkey dashboard)
    portkey_fallback_virtual_key: str = ""  # Fallback model virtual key (optional)
    llm_model: str = "gemini-2.0-flash"
    llm_fallback_model: str = "gemini-1.5-flash"

    # Direct LLM API keys (fallback when Portkey virtual keys are not configured)
    llm_api_key: str = ""          # Groq API key for direct calls
    gemini_api_key: str = ""       # Gemini API key for direct calls

    # Context API Keys
    pagerduty_api_key: str = ""
    jira_api_key: str = ""
    google_calendar_key: str = ""

    # Composio Integration (External Tools)
    composio_api_key: str = ""

    # App
    simulation_mode: bool = True
    data_retention_days: int = 90
    allowed_origins: str = "http://localhost:3000,http://localhost:3001"
    seed_password: str = ""

    # Environment
    environment: str = "development"
    log_level: str = "INFO"

    # Redis / MCP Session Cache
    redis_url: str = "redis://localhost:6379/0"
    redis_password: str = ""
    mcp_session_ttl_seconds: int = 1800
    mcp_lock_timeout_seconds: int = 30

    # JWT
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        if not v or len(v) < 32:
            raise ValueError(
                "JWT_SECRET must be at least 32 characters. "
                "Generate with: python -c 'import secrets; print(secrets.token_hex(32))'"
            )
        if v in ["change-me-in-production", "your-secret-key", "secret", "jwt-secret"]:
            raise ValueError(
                "JWT_SECRET cannot be a common placeholder value. "
                "Generate a cryptographically secure secret."
            )
        return v

    # Rate Limiting / Lockout
    max_login_attempts: int = 5
    lockout_duration_minutes: int = 15

    # SSO - Google
    google_client_id: str = ""
    google_client_secret: str = ""
    google_allowed_domains: str = ""

    # SSO - Azure AD
    azure_client_id: str = ""
    azure_client_secret: str = ""
    azure_tenant_id: str = "common"

    # SSO - SAML
    saml_entity_id: str = ""
    saml_sso_url: str = ""
    saml_certificate: str = ""

    model_config = {"env_file": ".env", "case_sensitive": False, "extra": "ignore"}


@lru_cache()
def get_settings():
    return Settings()
