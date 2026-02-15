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

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.config import get_settings
from app.core.security import privacy
from app.models.identity import UserIdentity, AuditLog
from datetime import datetime


def _get_seed_password() -> str:
    """Read SEED_PASSWORD from env or generate one."""
    pw = os.getenv("SEED_PASSWORD", "")
    if pw:
        return pw
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    pw = "".join(secrets.choice(alphabet) for _ in range(16))
    print(f"  [WARN] SEED_PASSWORD not set. Generated: {pw}")
    print(f"         Set SEED_PASSWORD in .env to use a fixed password.\n")
    return pw


def create_test_users():
    settings = get_settings()
    engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=engine)
    session = Session()

    print("=" * 70)
    print("CREATING TEST USERS FOR RBAC TESTING")
    print("=" * 70)

    seed_password = _get_seed_password()

    # Define test users with their roles and relationships
    test_users = [
        {
            "email": "admin@sentinel.local",
            "role": "admin",
            "manager_email": None,
            "consent_share_with_manager": False,
            "description": "System Administrator - Full access to all features",
        },
        {
            "email": "manager1@sentinel.local",
            "role": "manager",
            "manager_email": None,
            "consent_share_with_manager": False,
            "description": "Engineering Manager - Can view team aggregates and consented individual data",
        },
        {
            "email": "manager2@sentinel.local",
            "role": "manager",
            "manager_email": None,
            "consent_share_with_manager": False,
            "description": "Product Manager - Can view team aggregates and consented individual data",
        },
        {
            "email": "employee1@sentinel.local",
            "role": "employee",
            "manager_email": "manager1@sentinel.local",
            "consent_share_with_manager": True,
            "description": "Senior Developer - Can view own data, has consented to share with manager",
        },
        {
            "email": "employee2@sentinel.local",
            "role": "employee",
            "manager_email": "manager1@sentinel.local",
            "consent_share_with_manager": False,
            "description": "Junior Developer - Can view own data, has NOT consented to share",
        },
        {
            "email": "employee3@sentinel.local",
            "role": "employee",
            "manager_email": "manager2@sentinel.local",
            "consent_share_with_manager": False,
            "description": "Designer - Can view own data, different manager",
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
                    f"[WARN] User {email} already exists, updating role and relationships..."
                )
                existing.role = user_data["role"]
                existing.consent_share_with_manager = user_data[
                    "consent_share_with_manager"
                ]
                existing.consent_share_anonymized = True  # Default

                # Set manager hash if applicable
                if user_data["manager_email"]:
                    manager_hash = privacy.hash_identity(user_data["manager_email"])
                    existing.manager_hash = manager_hash
                else:
                    existing.manager_hash = None

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
                    manager_hash=None,
                )

                # Set manager hash if applicable
                if user_data["manager_email"]:
                    new_user.manager_hash = privacy.hash_identity(
                        user_data["manager_email"]
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

        print(f"\nPassword for all users: (the SEED_PASSWORD you provided)")

        print("\n" + "=" * 70)
        print("MANAGER-EMPLOYEE RELATIONSHIPS")
        print("=" * 70)
        print("\nOrganization Structure:\n")
        print("Admin: admin@sentinel.local")
        print("   L- Full system access\n")
        print("Manager 1: manager1@sentinel.local")
        print("   |- employee1@sentinel.local (CONSENTED)")
        print("   L- employee2@sentinel.local (NOT consented)\n")
        print("Manager 2: manager2@sentinel.local")
        print("   L- employee3@sentinel.local (NOT consented)\n")

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
