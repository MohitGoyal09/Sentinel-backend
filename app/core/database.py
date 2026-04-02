from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from supabase import create_client, Client
try:
    from app.core.config import get_settings
except ImportError:
    from app.config import get_settings

settings = get_settings()

# SQLAlchemy engine for ORM operations (using PostgreSQL connection)
engine = create_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=3600
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Supabase client for real-time features and auth
_supabase_client: Client | None = None

def get_supabase_client() -> Client:
    """Get or create Supabase client singleton."""
    global _supabase_client
    if _supabase_client is None:
        if not settings.supabase_url or not settings.supabase_key:
            raise ValueError(
                "Supabase URL and Key must be configured. "
                "Set SUPABASE_URL and SUPABASE_KEY environment variables."
            )
        _supabase_client = create_client(settings.supabase_url, settings.supabase_key)
    return _supabase_client

def get_supabase_admin_client() -> Client:
    """Get Supabase client with service role key for admin operations."""
    if not settings.supabase_url or not settings.supabase_service_key:
        raise ValueError(
            "Supabase URL and Service Key must be configured for admin operations. "
            "Set SUPABASE_URL and SUPABASE_SERVICE_KEY environment variables."
        )
    return create_client(settings.supabase_url, settings.supabase_service_key)

# Dependency for FastAPI
def get_db():
    """SQLAlchemy database session dependency."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def get_supabase():
    """Supabase client dependency."""
    return get_supabase_client()
