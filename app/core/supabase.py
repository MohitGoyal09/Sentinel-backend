"""
Supabase client initialization for backend authentication.
"""
from supabase import create_client, Client
from functools import lru_cache
from app.config import get_settings

@lru_cache()
def get_supabase_client() -> Client:
    """Get cached Supabase client instance (uses anon/public key)."""
    settings = get_settings()
    url = settings.supabase_url
    key = settings.supabase_key

    if not url or not key:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in .env")

    return create_client(url, key)

@lru_cache()
def get_supabase_admin_client() -> Client:
    """Get cached Supabase client with service role key for admin operations."""
    settings = get_settings()
    url = settings.supabase_url
    key = settings.supabase_service_key

    if not url or not key:
        raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")

    return create_client(url, key)

