"""
Seed script to create test users with different roles for RBAC testing.
Run this after migration to set up test accounts.

Passwords are read from SEED_PASSWORD env var.
If not set a random password is generated and printed once.
"""

import os
import sys
import secrets
import string

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from pathlib import Path

try:
    from dotenv import load_dotenv

    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(env_path)
except ImportError:
    print("python-dotenv not installed, using system env vars")

from app.config import get_settings
from app.core.security import privacy
from app.models.identity import UserIdentity, AuditLog


def _get_seed_password() -> str:
    """Read SEED_PASSWORD from env or generate one."""
    pw = os.getenv("SEED_PASSWORD", "")
    if pw:
        return pw
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    pw = "".join(secrets.choice(alphabet) for _ in range(16))
    print(f"  [WARN] SEED_PASSWORD not set. Generated: {pw}")
    print("         Set SEED_PASSWORD in .env to use a fixed password.\n")
    return pw


def create_test_users():
    settings = get_settings()
    engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=engine)
    session = Session()

    print("=" * 70)
    print("CREATING TEST USERS FOR RBAC TESTING")
    print("=" * 70)

    _get_seed_password()

    # Unified demo users — matches seed_demo.py emails exactly
    test_users = [
        {
            "email": "admin@sentinel.local",
            "role": "admin",
            "manager_email": None,
            "consent_share_with_manager": False,
            "description": "System Administrator — Full access",
        },
        {
            "email": "jordan.chen@sentinel.local",
            "role": "manager",
            "manager_email": None,
            "consent_share_with_manager": False,
            "description": "Engineering Manager — Healthy leader, can view team aggregates",
        },
        {
            "email": "alex.rivera@sentinel.local",
            "role": "employee",
            "manager_email": "jordan.chen@sentinel.local",
            "consent_share_with_manager": True,
            "description": "Senior Backend Engineer — CRITICAL burnout demo persona",
        },
        {
            "email": "sarah.kim@sentinel.local",
            "role": "employee",
            "manager_email": "jordan.chen@sentinel.local",
            "consent_share_with_manager": False,
            "description": "Mid-Level Frontend Engineer — Hidden gem demo persona (NOT consented)",
        },
        {
            "email": "maria.santos@sentinel.local",
            "role": "employee",
            "manager_email": "jordan.chen@sentinel.local",
            "consent_share_with_manager": True,
            "description": "Backend Engineer — Contagion pattern demo persona",
        },
        {
            "email": "priya.sharma@sentinel.local",
            "role": "employee",
            "manager_email": "jordan.chen@sentinel.local",
            "consent_share_with_manager": True,
            "description": "Staff Engineer — Backend team, consented",
        },
        {
            "email": "emma.wilson@sentinel.local",
            "role": "employee",
            "manager_email": "jordan.chen@sentinel.local",
            "consent_share_with_manager": True,
            "description": "Senior Frontend Engineer — Frontend team, consented",
        },
    ]

    created_users = []

    try:
        for user_data in test_users:
            email = user_data["email"]

            # Check if user already exists
            user_hash = privacy.hash_identity(email)
            existing = (
                session.query(UserIdentity).filter_by(user_hash=user_hash).first()
            )

            if existing:
                print(
                    f"[WARN] User {email} already exists, updating consent settings..."
                )
                existing.consent_share_with_manager = user_data[
                    "consent_share_with_manager"
                ]
                existing.consent_share_anonymized = True  # Default

                session.add(existing)
                created_users.append(
                    {
                        "email": email,
                        "role": user_data["role"],
                        "user_hash": user_hash,
                        "description": user_data["description"],
                        "status": "UPDATED",
                    }
                )
            else:
                # Create new user
                user_hash = privacy.hash_identity(email)
                encrypted_email = privacy.encrypt(email)

                # Create user record
                new_user = UserIdentity(
                    user_hash=user_hash,
                    email_encrypted=encrypted_email,
                    slack_id_encrypted=None,
                    role=user_data["role"],
                    consent_share_with_manager=user_data["consent_share_with_manager"],
                    consent_share_anonymized=True,
                    monitoring_paused_until=None,
                )

                session.add(new_user)

                # Create audit log
                audit_log = AuditLog(
                    user_hash=user_hash,
                    action="user_created",
                    details={
                        "role": user_data["role"],
                        "consent_share_with_manager": user_data[
                            "consent_share_with_manager"
                        ],
                        "created_by": "setup_script",
                    },
                )
                session.add(audit_log)

                created_users.append(
                    {
                        "email": email,
                        "role": user_data["role"],
                        "user_hash": user_hash,
                        "description": user_data["description"],
                        "status": "CREATED",
                    }
                )

                print(f"[OK] Created user: {email} ({user_data['role']})")

        session.commit()

        print("\n" + "=" * 70)
        print("TEST USERS DOCUMENTATION")
        print("=" * 70)
        print("\nUse these credentials to test the RBAC system:\n")

        for user in created_users:
            print(f"Email: {user['email']}")
            print(f"   Role: {user['role'].upper()}")
            print(f"   Description: {user['description']}")
            print(f"   User Hash: {user['user_hash']}")
            print(f"   Status: {user['status']}")
            print("-" * 70)

        print("\nPassword for all users: (the SEED_PASSWORD you provided)")

        print("\n" + "=" * 70)
        print("ORGANIZATION STRUCTURE")
        print("=" * 70)
        print("\nAdmin: admin@sentinel.local")
        print("   Full system access\n")
        print("Manager: jordan.chen@sentinel.local (Engineering Manager)")
        print("   |- alex.rivera@sentinel.local     (CONSENTED, CRITICAL burnout)")
        print("   |- sarah.kim@sentinel.local       (NOT consented, hidden gem)")
        print("   |- maria.santos@sentinel.local    (CONSENTED, contagion pattern)")
        print("   |- priya.sharma@sentinel.local    (CONSENTED)")
        print("   L- emma.wilson@sentinel.local     (CONSENTED)\n")

        print("=" * 70)
        print("RBAC TEST SCENARIOS")
        print("=" * 70)
        print("""
Test these scenarios after implementation:

1. EMPLOYEE VIEW (/me):
   - Login as employee1@sentinel.local
   - Should see: Own risk score, velocity chart, consent toggles
   - Should NOT see: Other users' data, team aggregates

2. MANAGER VIEW (/team):
   - Login as manager1@sentinel.local
   - Should see: Team aggregates (anonymized by default)
   - Should see: employee1 details (because consented)
   - Should NOT see: employee2 details (no consent, not critical)
   - Should NOT see: employee3 details (different manager)

3. ADMIN VIEW (/admin):
   - Login as admin@sentinel.local
   - Should see: System health, all audit logs
   - Can view any user data (for audit purposes)

4. CONSENT FLOW:
   - Login as employee2@sentinel.local
   - Toggle "Share with manager" ON
   - Login as manager1@sentinel.local
   - Should now see employee2 details

5. 36-HOUR CRITICAL RULE:
   - Set employee3 to CRITICAL risk
   - Wait (or simulate) 36 hours
   - Manager2 should see employee3 details even without consent
""")

        # NOTE: No longer writing TEST_USERS.md to avoid leaking passwords to VCS
        print("=" * 70)

        return True

    except Exception as e:
        session.rollback()
        print(f"\n[ERROR] {str(e)}")
        import traceback

        traceback.print_exc()
        return False
    finally:
        session.close()


if __name__ == "__main__":
    success = create_test_users()
    sys.exit(0 if success else 1)
