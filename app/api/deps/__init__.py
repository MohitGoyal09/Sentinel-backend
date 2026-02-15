from typing import Generator
from app.core.database import SessionLocal

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
