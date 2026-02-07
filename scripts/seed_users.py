import sys
import os

# Ensure backend root is in path
sys.path.append(os.getcwd())

from app.core.database import SessionLocal
from app.models.identity import UserIdentity
from datetime import datetime

# Frontend mock users
mock_users = [
    {"user_hash": "8f3a2d9e", "email": "alex@algoquest.com"},
    {"user_hash": "b4c7e1f2", "email": "sarah@algoquest.com"},
    {"user_hash": "e2f8c4d7", "email": "jordan@algoquest.com"},
    {"user_hash": "a1c3e5g7", "email": "maria@algoquest.com"}
]

def seed():
    db = SessionLocal()
    try:
        count = 0
        for u in mock_users:
            existing = db.query(UserIdentity).filter_by(user_hash=u["user_hash"]).first()
            if not existing:
                print(f"Seeding user: {u['email']} ({u['user_hash']})")
                # Create dummy encrypted email (just base64 encoded for "encrypted" look, logic just needs bytes)
                dummy_encrypted = f"encrypted_{u['email']}".encode()
                
                user = UserIdentity(
                    user_hash=u["user_hash"],
                    email_encrypted=dummy_encrypted,
                    created_at=datetime.utcnow()
                )
                db.add(user)
                count += 1
            else:
                print(f"User already exists: {u['user_hash']}")
        
        db.commit()
        print(f"Seeding complete. Added {count} users.")
        
    except Exception as e:
        print(f"Error seeding database: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed()
