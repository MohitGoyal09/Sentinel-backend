from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
try:
    from app.core.config import get_settings
except ImportError:
    from app.config import get_settings

from app.core.supabase import get_supabase_client, get_supabase_admin_client

settings = get_settings()

# SQLAlchemy engine for ORM operations (using PostgreSQL connection)
engine = create_engine(
    settings.database_url,
    pool_size=3,
    max_overflow=5,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_timeout=10,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


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
