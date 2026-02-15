"""
Re-export dependencies so `from app.api.deps import get_db` works.

Note: Both `app/api/deps.py` and `app/api/deps/` exist.
Python resolves the directory (package) first, so we re-export here.
"""
from typing import Generator
from app.core.database import SessionLocal


def get_db() -> Generator:
    """Yield a database session, auto-closing on teardown."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
