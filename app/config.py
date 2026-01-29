import os
from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    # Default to a placeholder; user must set DATABASE_URL in env for Supabase
    database_url: str = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/sentinel")
    vault_salt: str = os.getenv("VAULT_SALT", "change-me-in-production-salt")
    encryption_key: str = os.getenv("ENCRYPTION_KEY", "change-me-to-32-byte-key-here!!")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    slack_bot_token: str = os.getenv("SLACK_BOT_TOKEN", "")
    
    # LLM Configuration (LiteLLM)
    # Providers: 'gemini', 'anthropic', 'openai', etc.
    llm_provider: str = os.getenv("LLM_PROVIDER", "gemini")
    llm_model: str = os.getenv("LLM_MODEL", "gemini-pro") 
    llm_api_key: str = os.getenv("LLM_API_KEY", "") # Generic key for chosen provider

    # Context API Keys
    pagerduty_api_key: str = os.getenv("PAGERDUTY_API_KEY", "")
    jira_api_key: str = os.getenv("JIRA_API_KEY", "")
    google_calendar_key: str = os.getenv("GOOGLE_CALENDAR_KEY", "")

    simulation_mode: bool = True
    data_retention_days: int = 90

    class Config:
        env_file = ".env"

@lru_cache()
def get_settings():
    return Settings()
