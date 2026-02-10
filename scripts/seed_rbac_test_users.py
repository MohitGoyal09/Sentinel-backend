"""
Seed script to create test users with different roles for RBAC testing.
Run this after migration to set up test accounts.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.config import get_settings
from app.core.security import privacy
from app.models.identity import UserIdentity, AuditLog
from datetime import datetime


def create_test_users():
    settings = get_settings()
    engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=engine)
    session = Session()

    print("=" * 70)
    print("CREATING TEST USERS FOR RBAC TESTING")
    print("=" * 70)

    # Define test users with their roles and relationships
    test_users = [
        {
            "email": "admin@sentinel.local",
            "password": "Admin123!",
            "role": "admin",
            "manager_email": None,
            "consent_share_with_manager": False,
            "description": "System Administrator - Full access to all features",
        },
        {
            "email": "manager1@sentinel.local",
            "password": "Manager123!",
            "role": "manager",
            "manager_email": None,
            "consent_share_with_manager": False,
            "description": "Engineering Manager - Can view team aggregates and consented individual data",
        },
        {
            "email": "manager2@sentinel.local",
            "password": "Manager456!",
            "role": "manager",
            "manager_email": None,
            "consent_share_with_manager": False,
            "description": "Product Manager - Can view team aggregates and consented individual data",
        },
        {
            "email": "employee1@sentinel.local",
            "password": "Employee123!",
            "role": "employee",
            "manager_email": "manager1@sentinel.local",
            "consent_share_with_manager": True,  # Consented to share with manager
            "description": "Senior Developer - Can view own data, has consented to share with manager",
        },
        {
            "email": "employee2@sentinel.local",
            "password": "Employee456!",
            "role": "employee",
            "manager_email": "manager1@sentinel.local",
            "consent_share_with_manager": False,  # Has NOT consented
            "description": "Junior Developer - Can view own data, has NOT consented to share",
        },
        {
            "email": "employee3@sentinel.local",
            "password": "Employee789!",
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
                        "password": user_data["password"],
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
                        "password": user_data["password"],
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
            print(f"   Password: {user['password']}")
            print(f"   Role: {user['role'].upper()}")
            print(f"   Description: {user['description']}")
            print(f"   User Hash: {user['user_hash']}")
            print(f"   Status: {user['status']}")
            print("-" * 70)

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

        # Save documentation to file
        doc_path = os.path.join(os.path.dirname(__file__), "..", "TEST_USERS.md")
        with open(doc_path, "w") as f:
            f.write("# Test Users for RBAC Implementation\n\n")
            f.write("## Credentials\n\n")
            for user in created_users:
                f.write(f"### {user['role'].upper()}: {user['email']}\n")
                f.write(f"- **Password:** `{user['password']}`\n")
                f.write(f"- **Role:** {user['role']}\n")
                f.write(f"- **Description:** {user['description']}\n")
                f.write(f"- **User Hash:** `{user['user_hash']}`\n")
                f.write(f"- **Status:** {user['status']}\n\n")

            f.write("## Organization Structure\n\n")
            f.write("```\n")
            f.write("Admin: admin@sentinel.local\n")
            f.write("└─ Full system access\n\n")
            f.write("Manager 1: manager1@sentinel.local\n")
            f.write("├─ employee1@sentinel.local (CONSENTED)\n")
            f.write("└─ employee2@sentinel.local (NOT consented)\n\n")
            f.write("Manager 2: manager2@sentinel.local\n")
            f.write("└─ employee3@sentinel.local (NOT consented)\n")
            f.write("```\n")

        print(f"\n[SUCCESS] Documentation saved to: {doc_path}")
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
