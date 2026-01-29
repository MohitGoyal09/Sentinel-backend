from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
try:
    from app.core.config import get_settings
except ImportError:
    from app.config import get_settings

settings = get_settings()

engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
