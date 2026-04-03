"""
Script to verify encryption is working properly in the database.
Run this to check if emails are actually encrypted (not plaintext).
"""

import os
import sys

# Add the backend directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from app.config import get_settings


def verify_encryption():
    settings = get_settings()
    engine = create_engine(settings.database_url)

    print("=" * 60)
    print("ENCRYPTION VERIFICATION")
    print("=" * 60)

    with engine.connect() as conn:
        # Check if users table exists
        result = conn.execute(
            text("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'identity' AND table_name = 'users'
            )
        """)
        )
        table_exists = result.scalar()

        if not table_exists:
            print("[FAIL] ERROR: identity.users table does not exist!")
            print("   Run migrations first: alembic upgrade head")
            return False

        print("[OK] identity.users table exists")

        # Check the structure
        result = conn.execute(
            text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'identity' AND table_name = 'users'
            ORDER BY ordinal_position
        """)
        )
        columns = result.fetchall()

        print(f"\nTable structure ({len(columns)} columns):")
        for col_name, data_type in columns:
            print(f"  - {col_name}: {data_type}")

        # Check for expected columns
        rbac_columns = [
            "consent_share_with_manager",
            "consent_share_anonymized",
            "monitoring_paused_until",
        ]
        existing_cols = [col[0] for col in columns]

        missing_cols = [col for col in rbac_columns if col not in existing_cols]
        if missing_cols:
            print(f"\n[WARN] WARNING: Missing expected columns: {missing_cols}")
            print("   Run migration: alembic upgrade head")
        else:
            print("\n[OK] All expected columns present")

        # Check if there are any users
        result = conn.execute(text("SELECT COUNT(*) FROM identity.users"))
        user_count = result.scalar()

        print(f"\nUser count: {user_count}")

        if user_count == 0:
            print("\n[WARN] No users in database. Cannot verify encryption.")
            print("   Seed some data first.")
            return True  # Not an error, just no data

        # Check if email_encrypted is actually encrypted
        result = conn.execute(
            text("""
            SELECT user_hash, email_encrypted
            FROM identity.users
            LIMIT 3
        """)
        )
        users = result.fetchall()

        print("\nChecking email encryption for first 3 users:")
        print("-" * 60)

        all_encrypted = True
        for user_hash, email_encrypted in users:
            if email_encrypted is None:
                print(f"\nUser {user_hash}:")
                print("  [WARN] WARNING: email_encrypted is NULL")
                all_encrypted = False
                continue

            # Check if it looks like encrypted data (Fernet produces base64-like output)
            try:
                # Convert memoryview to bytes if needed
                if isinstance(email_encrypted, memoryview):
                    email_bytes = email_encrypted.tobytes()
                else:
                    email_bytes = email_encrypted

                # Try to decode as string to see if it's plaintext
                email_str = email_bytes.decode("utf-8", errors="strict")

                # If it decodes cleanly and looks like an email, it's NOT encrypted
                if "@" in email_str and "." in email_str:
                    print(f"\nUser {user_hash}:")
                    print(f"  [FAIL] CRITICAL: Email appears to be PLAINTEXT!")
                    print(f"     Value: {email_str[:50]}...")
                    all_encrypted = False
                else:
                    print(f"\nUser {user_hash}:")
                    print(f"  [WARN] Decodes as string but doesn't look like email")
                    print(f"     Value: {email_str[:50]}...")

            except UnicodeDecodeError:
                # Cannot decode as UTF-8 - likely encrypted (good!)
                print(f"\nUser {user_hash}:")
                print(f"  [OK] Email is ENCRYPTED (binary data)")
                print(f"     Bytes: {len(email_bytes)} bytes")
                # Show first few bytes as hex
                hex_preview = email_bytes[:20].hex()
                print(f"     Preview: {hex_preview}...")

        print("\n" + "=" * 60)
        if all_encrypted:
            print("[PASS] ENCRYPTION VERIFIED: Emails are properly encrypted")
        else:
            print("[FAIL] ENCRYPTION ISSUE: Some emails are not encrypted")
            print("   Check privacy.encrypt() is being called in vault.py")

        print("=" * 60)
        return all_encrypted


if __name__ == "__main__":
    success = verify_encryption()
    sys.exit(0 if success else 1)
