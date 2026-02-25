from typing import Generator
from app.core.database import SessionLocal
from .auth import get_current_user, get_current_user_identity, get_optional_user

def get_db() -> Generator:
    """
    Database dependency for FastAPI.
    Yields a database session and ensures it's closed after the request.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

