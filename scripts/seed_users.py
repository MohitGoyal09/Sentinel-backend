import sys
import os

# Ensure backend root is in path
sys.path.append(os.getcwd())

from app.core.database import SessionLocal
from app.models.identity import UserIdentity
from app.core.security import privacy
from datetime import datetime

# Frontend mock users - emails that will be hashed to create user_hash
mock_users = [
    {"email": "alex@algoquest.com"},
    {"email": "sarah@algoquest.com"},
    {"email": "jordan@algoquest.com"},
    {"email": "maria@algoquest.com"},
]


def seed():
    db = SessionLocal()
    try:
        count = 0
        for u in mock_users:
            # Generate user_hash using the same algorithm as vault.store_identity
            user_hash = privacy.hash_identity(u["email"])

            existing = db.query(UserIdentity).filter_by(user_hash=user_hash).first()
            if not existing:
                print(f"Seeding user: {u['email']} ({user_hash})")
                user = UserIdentity(
                    user_hash=user_hash,
                    email_encrypted=privacy.encrypt(u["email"]),
                    created_at=datetime.utcnow(),
                )
                db.add(user)
                count += 1
            else:
                print(f"User already exists: {user_hash}")

        db.commit()
        print(f"Seeding complete. Added {count} users.")

    except Exception as e:
        print(f"Error seeding database: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    seed()
