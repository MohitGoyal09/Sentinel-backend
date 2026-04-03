"""
Seed Supabase Auth users for testing.

This script creates users in Supabase Auth (auth.users) table and then
seeds the PostgreSQL database with UserIdentity, Tenant, and TenantMember
records so that seeded users have a tenant immediately upon first login.

Run this AFTER setting correct SUPABASE_SERVICE_ROLE_KEY in .env

Usage:
    cd backend
    python scripts/seed_supabase_auth.py

Prerequisites:
    1. Set SUPABASE_SERVICE_KEY in backend/.env (must be a JWT with "role": "service_role")
    2. Get the key from: Supabase Dashboard → Project Settings → API → service_role secret
    3. Verify the key: Decode at jwt.io - payload should show "role": "service_role"
    4. Set ENCRYPTION_KEY and VAULT_SALT in backend/.env (required for DB seeding)
"""

import os
import sys
import json
import base64
import asyncio
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env BEFORE importing any app modules that call get_settings() at module level.
# This ensures pydantic-settings picks up the values from the file.
try:
    from dotenv import load_dotenv

    _env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(_env_path)
except ImportError:
    pass  # fall back to system environment variables

from supabase import create_client, Client

# Test users matching TEST_USERS.md
TEST_USERS = [
    {
        "email": "admin@sentinel.local",
        "password": "Admin123!",
        "role": "admin",
        "display_name": "Admin User",
        "user_metadata": {"role": "admin", "display_name": "Admin User"},
    },
    {
        "email": "manager1@sentinel.local",
        "password": "Manager123!",
        "role": "manager",
        "display_name": "Manager One",
        "user_metadata": {"role": "manager", "display_name": "Manager One"},
    },
    {
        "email": "manager2@sentinel.local",
        "password": "Manager456!",
        "role": "manager",
        "display_name": "Manager Two",
        "user_metadata": {"role": "manager", "display_name": "Manager Two"},
    },
    {
        "email": "employee1@sentinel.local",
        "password": "Employee123!",
        "role": "employee",
        "display_name": "Employee One",
        "user_metadata": {
            "role": "employee",
            "display_name": "Employee One",
            "manager_email": "manager1@sentinel.local",
        },
    },
    {
        "email": "employee2@sentinel.local",
        "password": "Employee456!",
        "role": "employee",
        "display_name": "Employee Two",
        "user_metadata": {
            "role": "employee",
            "display_name": "Employee Two",
            "manager_email": "manager1@sentinel.local",
        },
    },
    {
        "email": "employee3@sentinel.local",
        "password": "Employee789!",
        "role": "employee",
        "display_name": "Employee Three",
        "user_metadata": {
            "role": "employee",
            "display_name": "Employee Three",
            "manager_email": "manager2@sentinel.local",
        },
    },
    {
        "email": "employee4@sentinel.local",
        "password": "Employee101!",
        "role": "employee",
        "display_name": "Employee Four",
        "user_metadata": {
            "role": "employee",
            "display_name": "Employee Four",
            "manager_email": "manager2@sentinel.local",
        },
    },
    {
        "email": "employee5@sentinel.local",
        "password": "Employee112!",
        "role": "employee",
        "display_name": "Employee Five",
        "user_metadata": {
            "role": "employee",
            "display_name": "Employee Five",
            "manager_email": "manager2@sentinel.local",
        },
    },
    {
        "email": "employee6@sentinel.local",
        "password": "Employee123!",
        "role": "employee",
        "display_name": "Employee Six",
        "user_metadata": {
            "role": "employee",
            "display_name": "Employee Six",
            "manager_email": "manager2@sentinel.local",
        },
    },
    {
        "email": "employee7@sentinel.local",
        "password": "Employee234!",
        "role": "employee",
        "display_name": "Employee Seven",
        "user_metadata": {
            "role": "employee",
            "display_name": "Employee Seven",
            "manager_email": "manager2@sentinel.local",
        },
    },
]


def load_env_vars() -> tuple[str, str]:
    """Load Supabase credentials from environment or .env file."""
    # .env is already loaded at module import time; this is a no-op fallback.

    supabase_url = os.getenv("SUPABASE_URL")
    service_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv(
        "SUPABASE_SERVICE_ROLE_KEY"
    )

    if not supabase_url:
        raise ValueError(
            "SUPABASE_URL not found in environment.\n"
            "Please set it in backend/.env file."
        )

    if not service_key:
        raise ValueError(
            "SUPABASE_SERVICE_KEY not found in environment.\n"
            "Please set it in backend/.env file.\n"
            "Get it from: Supabase Dashboard → Project Settings → API → service_role secret"
        )

    return supabase_url, service_key


def decode_jwt_payload(token: str) -> dict:
    """Decode the payload portion of a JWT token without verification."""
    try:
        # JWT has 3 parts: header.payload.signature
        parts = token.split(".")
        if len(parts) != 3:
            return {}

        # Decode the payload (second part)
        payload = parts[1]
        # Add padding if needed (base64url may have missing padding)
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding

        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return {}


def validate_service_key(service_key: str) -> None:
    """Validate that the service key is a JWT with service_role permissions."""
    if not service_key.startswith("eyJ"):
        raise ValueError(
            "INVALID SUPABASE_SERVICE_KEY FORMAT!\n\n"
            "The service key must be a JWT token starting with 'eyJ'.\n"
            f"Current value starts with: '{service_key[:20]}...'\n\n"
            "To get the correct key:\n"
            "1. Go to Supabase Dashboard → Project Settings → API\n"
            "2. Copy the 'service_role' secret (NOT the anon key)\n"
            "3. It should look like: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...\n\n"
            "Update your backend/.env file with the correct value."
        )

    # Decode JWT and check the role
    payload = decode_jwt_payload(service_key)
    role = payload.get("role", "unknown")

    if role == "anon":
        raise ValueError(
            " WRONG KEY TYPE DETECTED!\n\n"
            "You are using the 'anon' key, but this script requires the 'service_role' key.\n\n"
            "Both keys are JWTs starting with 'eyJ', but they have different permissions:\n"
            "  • anon key: Limited permissions, cannot create users\n"
            "  • service_role key: Full admin access, can create users\n\n"
            'Your JWT payload shows: "role": "anon"\n'
            'Required JWT payload: "role": "service_role"\n\n'
            "To fix this:\n"
            "1. Go to Supabase Dashboard → Project Settings → API\n"
            "2. Find 'Project API keys' section\n"
            "3. Copy the 'service_role' secret (click 'Reveal' to show it)\n"
            "4. Update SUPABASE_SERVICE_KEY in backend/.env with the service_role key\n"
            '5. Verify: Decode at jwt.io - payload should show "role": "service_role"\n\n'
            " WARNING: The service_role key has FULL admin access. Never expose it to the frontend!"
        )

    if role != "service_role":
        print(f"   Warning: JWT role is '{role}', expected 'service_role'")
        print("   If you encounter permission errors, verify you have the correct key.")

    print(f"   ✓ JWT role: {role}")


class UserAlreadyExistsError(Exception):
    """Raised when a user already exists during creation."""

    pass


def check_existing_user(client: Client, email: str) -> dict | None:
    """Check if a user with the given email already exists."""
    try:
        # Use admin API to list users
        # Note: list_users() doesn't support query filters in all versions
        # We need to paginate through users and filter by email
        response = client.auth.admin.list_users()

        # The response is a list, iterate to find matching user
        # Note: list_users may return a list or an object with .users attribute
        # depending on the supabase-py version
        users_list = (
            response if isinstance(response, list) else getattr(response, "users", [])
        )

        for user in users_list:
            # user might be a dict or an object with email attribute
            user_email = user.email if hasattr(user, "email") else user.get("email")
            if user_email == email:
                # Return as dict for consistency
                if hasattr(user, "model_dump"):
                    return user.model_dump()
                return user if isinstance(user, dict) else user.model_dump()
        return None
    except Exception as e:
        print(f"Warning: Could not check existing users: {e}")
        return None


def create_auth_user(client: Client, user_data: dict) -> dict:
    """Create a user in Supabase Auth."""
    try:
        # Create user with admin API using dictionary directly
        response = client.auth.admin.create_user(
            {
                "email": user_data["email"],
                "password": user_data["password"],
                "email_confirm": True,  # Auto-confirm email for testing
                "user_metadata": user_data.get("user_metadata", {}),
            }
        )
        return response.model_dump()
    except Exception as e:
        error_msg = str(e)
        # Check if user already exists
        if (
            "already been registered" in error_msg.lower()
            or "already exists" in error_msg.lower()
        ):
            raise UserAlreadyExistsError(f"User already exists: {user_data['email']}")
        raise Exception(f"Failed to create user: {e}")


def update_existing_user(client: Client, user_id: str, user_data: dict) -> dict:
    """Update an existing user's password and metadata."""
    try:
        response = client.auth.admin.update_user_by_id(
            user_id,
            {
                "password": user_data["password"],
                "user_metadata": user_data.get("user_metadata", {}),
                "email_confirm": True,  # Ensure email stays confirmed
            },
        )
        return response.model_dump()
    except Exception as e:
        raise Exception(f"Failed to update user: {e}")


def seed_auth_users() -> None:
    """Main function to seed Supabase Auth users."""
    print("=" * 60)
    print("Supabase Auth User Seeding Script")
    print("=" * 60)
    print()

    # Load and validate credentials
    print(" Loading credentials...")
    try:
        supabase_url, service_key = load_env_vars()
        validate_service_key(service_key)
        print(f"   ✓ SUPABASE_URL: {supabase_url}")
        print(f"   ✓ Service key format: Valid JWT")
    except ValueError as e:
        print(f"\n{e}")
        sys.exit(1)

    # Create Supabase client with service role key
    print("\n🔌 Connecting to Supabase...")
    try:
        client = create_client(supabase_url, service_key)
        print("   ✓ Connected successfully")
    except Exception as e:
        print(f"   ✗ Connection failed: {e}")
        sys.exit(1)

    # Process each test user
    print(f"\n👥 Processing {len(TEST_USERS)} test users...")
    print("-" * 60)

    created_count = 0
    updated_count = 0
    skipped_count = 0
    error_count = 0

    for user in TEST_USERS:
        email = user["email"]
        print(f"\n    {email}")

        # Check if user already exists
        existing_user = check_existing_user(client, email)

        if existing_user:
            user_id = existing_user.get("id")
            print(f"      → User already exists (ID: {user_id[:8]}...)")

            # Update password and metadata for existing user
            try:
                update_existing_user(client, user_id, user)
                print(f"      ✓ Updated password and metadata: role={user['role']}")
                updated_count += 1
            except Exception as e:
                print(f"      ⚠ Could not update user: {e}")
                skipped_count += 1
        else:
            # Create new user
            try:
                new_user = create_auth_user(client, user)
                user_id = new_user.get("id", "unknown")
                print(f"      ✓ Created successfully (ID: {user_id[:8]}...)")
                print(f"      → Role: {user['role']}")
                created_count += 1
            except UserAlreadyExistsError as e:
                # User exists but wasn't found by check - try to find and update
                print(f"      → User exists (detected during creation)")
                # Try to find the user again
                existing_user = check_existing_user(client, email)
                if existing_user:
                    user_id = existing_user.get("id")
                    try:
                        update_existing_user(client, user_id, user)
                        print(
                            f"      ✓ Updated password and metadata: role={user['role']}"
                        )
                        updated_count += 1
                    except Exception as update_error:
                        print(f"      ✗ Failed to update: {update_error}")
                        error_count += 1
                else:
                    print(f"      ✗ Could not find existing user to update")
                    error_count += 1
            except Exception as e:
                print(f"      ✗ Failed: {e}")
                error_count += 1

    # Summary
    print("\n" + "=" * 60)
    print(" Summary")
    print("=" * 60)
    print(f"   ✓ Created: {created_count}")
    print(f"   ↻ Updated: {updated_count}")
    print(f"   → Skipped: {skipped_count}")
    print(f"   ✗ Errors:  {error_count}")

    if error_count == 0:
        print("\n All users processed successfully!")
        print("\nYou can now log in with any of these credentials:")
        print("   • admin@sentinel.local / Admin123!")
        print("   • manager1@sentinel.local / Manager123!")
        print("   • employee1@sentinel.local / Employee123!")
    else:
        print(f"\n {error_count} user(s) failed to create. Check errors above.")
        sys.exit(1)


def seed_database_records() -> None:
    """
    Create UserIdentity, Tenant, and TenantMember records in the PostgreSQL DB.

    This phase runs after seed_auth_users() so that seeded users already have a
    tenant when they first log in (instead of relying on the lazy auto-create path
    that leaves tenant_id NULL).

    The function is idempotent:
      - UserIdentity: INSERT ... ON CONFLICT (user_hash) DO UPDATE
      - Tenant:       INSERT ... ON CONFLICT (slug) DO NOTHING, then SELECT
      - TenantMember: INSERT ... ON CONFLICT (tenant_id, user_hash) DO NOTHING
    """
    print()
    print("=" * 60)
    print("Database Record Seeding (UserIdentity / Tenant / TenantMember)")
    print("=" * 60)
    print()

    # ------------------------------------------------------------------ imports
    # All app-level imports are deferred here so that get_settings() is only
    # called after load_dotenv() has already populated os.environ.
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import sessionmaker
    except ImportError:
        print("   SQLAlchemy is not installed. Skipping DB seeding.")
        return

    try:
        from app.core.security import privacy
    except Exception as e:
        print(f"   Could not import privacy engine: {e}")
        print("   Make sure ENCRYPTION_KEY and VAULT_SALT are set in .env.")
        return

    # ----------------------------------------------------------- env validation
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("   DATABASE_URL not set in .env — skipping DB seeding.")
        return

    encryption_key = os.getenv("ENCRYPTION_KEY")
    vault_salt = os.getenv("VAULT_SALT")
    if not encryption_key or not vault_salt:
        print("   ENCRYPTION_KEY or VAULT_SALT not set in .env — skipping DB seeding.")
        return

    print(" Connecting to PostgreSQL database...")
    try:
        engine = create_engine(database_url, pool_pre_ping=True)
        Session = sessionmaker(bind=engine)
        # Quick connectivity check
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("   Connected successfully.")
    except Exception as e:
        print(f"   Connection failed: {e}")
        return

    # ----------------------------------------------------------- role mappings
    # Supabase role -> TenantMember role
    TENANT_ROLE_MAP: dict[str, str] = {
        "admin": "owner",
        "manager": "admin",
        "employee": "member",
    }

    SHARED_TENANT_NAME = "Sentinel Workspace"
    SHARED_TENANT_SLUG = "sentinel-workspace"

    # -------------------------------------------------------- Step 1: UserIdentity
    print(f"\n   Step 1: Seeding {len(TEST_USERS)} UserIdentity records...")

    with Session() as db:
        try:
            for user in TEST_USERS:
                email: str = user["email"]
                role: str = user["role"]

                user_hash: str = privacy.hash_identity(email)
                email_encrypted: bytes = privacy.encrypt(email)

                db.execute(
                    text(
                        """
                        INSERT INTO identity.users
                            (user_hash, email_encrypted,
                             consent_share_with_manager, consent_share_anonymized,
                             created_at)
                        VALUES
                            (:user_hash, :email_encrypted,
                             false, true,
                             NOW())
                        ON CONFLICT (user_hash) DO UPDATE SET
                            email_encrypted = EXCLUDED.email_encrypted
                        """
                    ),
                    {
                        "user_hash": user_hash,
                        "email_encrypted": email_encrypted,
                    },
                )
                print(f"      {email} -> user_hash={user_hash[:8]}... role={role}")

            db.commit()
            print(f"   UserIdentity records committed ({len(TEST_USERS)} rows).")
        except Exception as e:
            db.rollback()
            print(f"   Failed to seed UserIdentity records: {e}")
            return

    # -------------------------------------------------------- Step 2: Tenant
    print(f"\n   Step 2: Ensuring tenant '{SHARED_TENANT_NAME}'...")

    tenant_id: str | None = None
    with Session() as db:
        try:
            db.execute(
                text(
                    """
                    INSERT INTO identity.tenants
                        (id, name, slug, plan, status, settings, created_at, updated_at)
                    VALUES
                        (gen_random_uuid(), :name, :slug, 'free', 'active', CAST('{}' AS jsonb),
                         NOW(), NOW())
                    ON CONFLICT (slug) DO NOTHING
                    """
                ),
                {"name": SHARED_TENANT_NAME, "slug": SHARED_TENANT_SLUG},
            )
            db.commit()

            row = db.execute(
                text("SELECT id FROM identity.tenants WHERE slug = :slug"),
                {"slug": SHARED_TENANT_SLUG},
            ).fetchone()

            if row is None:
                print("   Could not retrieve tenant ID after insert.")
                return

            tenant_id = str(row[0])
            print(f"   Tenant ID: {tenant_id}")
        except Exception as e:
            db.rollback()
            print(f"   Failed to seed Tenant: {e}")
            return

    # -------------------------------------------------------- Step 3: TenantMember
    print(f"\n   Step 3: Linking {len(TEST_USERS)} users to tenant...")

    with Session() as db:
        try:
            for user in TEST_USERS:
                email: str = user["email"]
                role: str = user["role"]
                user_hash: str = privacy.hash_identity(email)
                tenant_role: str = TENANT_ROLE_MAP.get(role, "member")

                db.execute(
                    text(
                        """
                        INSERT INTO identity.tenant_members
                            (id, tenant_id, user_hash, role, joined_at)
                        VALUES
                            (gen_random_uuid(), :tenant_id, :user_hash, :role, NOW())
                        ON CONFLICT (tenant_id, user_hash) DO NOTHING
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "user_hash": user_hash,
                        "role": tenant_role,
                    },
                )
                print(f"      {email} -> tenant_role={tenant_role}")

            db.commit()
            print(f"   TenantMember records committed ({len(TEST_USERS)} rows).")
        except Exception as e:
            db.rollback()
            print(f"   Failed to seed TenantMember records: {e}")
            return

    # -------------------------------------------------------- Step 4: Backfill tenant_id on UserIdentity
    print(f"\n   Step 4: Backfilling tenant_id on UserIdentity rows...")

    with Session() as db:
        try:
            result = db.execute(
                text(
                    """
                    UPDATE identity.users u
                    SET    tenant_id = :tenant_id
                    FROM   identity.tenant_members tm
                    WHERE  tm.user_hash = u.user_hash
                      AND  tm.tenant_id = CAST(:tenant_id AS uuid)
                      AND  (u.tenant_id IS NULL OR u.tenant_id != CAST(:tenant_id AS uuid))
                    """
                ),
                {"tenant_id": tenant_id},
            )
            db.commit()
            print(f"   tenant_id backfilled on {result.rowcount} UserIdentity row(s).")
        except Exception as e:
            db.rollback()
            print(f"   Failed to backfill tenant_id: {e}")
            return

    print()
    print("=" * 60)
    print("   DB seeding complete.")
    print(f"   Tenant : {SHARED_TENANT_NAME} ({SHARED_TENANT_SLUG})")
    print(f"   Members: {len(TEST_USERS)} users linked")
    print("=" * 60)


def seed_engine_data() -> None:
    """
    Seed analytics engine data for the @sentinel.local users created by
    seed_auth_users() / seed_database_records().

    Creates per-user records in the analytics schema:
      - analytics.risk_scores        (current snapshot, 1 row per user)
      - analytics.risk_history       (14 daily snapshots per user)
      - analytics.events             (10-20 behavioral events per user)
      - analytics.graph_edges        (collaboration graph edges)
      - analytics.centrality_scores  (betweenness + eigenvector per user)
      - analytics.skill_profiles     (6 skill dimensions per user)

    The function is idempotent: it deletes existing analytics rows for these
    users (keyed by user_hash) before re-inserting, so re-runs are safe.

    Consent variation:
      employee1 and employee6 have consent_share_with_manager=False so that
      the blocked-view RBAC flow can be demonstrated.
    """
    print()
    print("=" * 60)
    print("Engine Data Seeding (Analytics Schema)")
    print("=" * 60)
    print()

    # ------------------------------------------------------------------ imports
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import sessionmaker
    except ImportError:
        print("   SQLAlchemy is not installed. Skipping engine data seeding.")
        return

    try:
        from app.core.security import privacy
    except Exception as e:
        print(f"   Could not import privacy engine: {e}")
        print("   Make sure ENCRYPTION_KEY and VAULT_SALT are set in .env.")
        return

    import random
    from datetime import datetime, timedelta

    # Reproducible randomness -- same data every run
    random.seed(99)

    # ----------------------------------------------------------- env validation
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("   DATABASE_URL not set in .env -- skipping engine data seeding.")
        return

    encryption_key = os.getenv("ENCRYPTION_KEY")
    vault_salt = os.getenv("VAULT_SALT")
    if not encryption_key or not vault_salt:
        print("   ENCRYPTION_KEY or VAULT_SALT not set -- skipping engine data seeding.")
        return

    print(" Connecting to PostgreSQL database...")
    try:
        engine = create_engine(database_url, pool_pre_ping=True)
        Session = sessionmaker(bind=engine)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("   Connected successfully.")
    except Exception as e:
        print(f"   Connection failed: {e}")
        return

    # ----------------------------------------------------------- persona specs
    # Persona-driven risk data for each @sentinel.local user
    PERSONA_SPECS: dict[str, dict] = {
        "admin@sentinel.local": {
            "velocity": 45.0, "risk_level": "LOW",      "confidence": 0.88,
            "belongingness": 0.82,
            "skills": {"technical": 88, "communication": 92, "leadership": 95,
                       "collaboration": 88, "adaptability": 90, "creativity": 85},
            "centrality": {"betweenness": 0.55, "eigenvector": 0.88,
                           "unblocking_count": 10, "knowledge_transfer": 0.82},
            "persona": "cto",
        },
        "manager1@sentinel.local": {
            "velocity": 55.0, "risk_level": "LOW",      "confidence": 0.86,
            "belongingness": 0.75,
            "skills": {"technical": 72, "communication": 88, "leadership": 90,
                       "collaboration": 85, "adaptability": 82, "creativity": 72},
            "centrality": {"betweenness": 0.62, "eigenvector": 0.76,
                           "unblocking_count": 14, "knowledge_transfer": 0.70},
            "persona": "manager_healthy",
        },
        "manager2@sentinel.local": {
            "velocity": 72.0, "risk_level": "ELEVATED", "confidence": 0.79,
            "belongingness": 0.58,
            "skills": {"technical": 68, "communication": 74, "leadership": 72,
                       "collaboration": 65, "adaptability": 60, "creativity": 62},
            "centrality": {"betweenness": 0.38, "eigenvector": 0.52,
                           "unblocking_count": 6, "knowledge_transfer": 0.55},
            "persona": "contagion",
        },
        "employee1@sentinel.local": {
            "velocity": 94.0, "risk_level": "CRITICAL",  "confidence": 0.91,
            "belongingness": 0.18,
            "skills": {"technical": 91, "communication": 52, "leadership": 40,
                       "collaboration": 45, "adaptability": 32, "creativity": 70},
            "centrality": {"betweenness": 0.40, "eigenvector": 0.58,
                           "unblocking_count": 7, "knowledge_transfer": 0.65},
            "persona": "burnout",
            "consent_share_with_manager": False,  # RBAC demo: blocked view
        },
        "employee2@sentinel.local": {
            "velocity": 35.0, "risk_level": "LOW",      "confidence": 0.87,
            "belongingness": 0.88,
            "skills": {"technical": 85, "communication": 86, "leadership": 72,
                       "collaboration": 93, "adaptability": 80, "creativity": 70},
            "centrality": {"betweenness": 0.86, "eigenvector": 0.68,
                           "unblocking_count": 44, "knowledge_transfer": 0.89},
            "persona": "hidden_gem",
        },
        "employee3@sentinel.local": {
            "velocity": 78.0, "risk_level": "ELEVATED", "confidence": 0.81,
            "belongingness": 0.42,
            "skills": {"technical": 76, "communication": 40, "leadership": 36,
                       "collaboration": 30, "adaptability": 43, "creativity": 82},
            "centrality": {"betweenness": 0.12, "eigenvector": 0.20,
                           "unblocking_count": 0, "knowledge_transfer": 0.16},
            "persona": "struggling",
        },
        "employee4@sentinel.local": {
            "velocity": 40.0, "risk_level": "LOW",      "confidence": 0.85,
            "belongingness": 0.90,
            "skills": {"technical": 94, "communication": 86, "leadership": 80,
                       "collaboration": 83, "adaptability": 86, "creativity": 78},
            "centrality": {"betweenness": 0.68, "eigenvector": 0.80,
                           "unblocking_count": 25, "knowledge_transfer": 0.91},
            "persona": "high_performer",
        },
        "employee5@sentinel.local": {
            "velocity": 50.0, "risk_level": "LOW",      "confidence": 0.84,
            "belongingness": 0.72,
            "skills": {"technical": 73, "communication": 70, "leadership": 53,
                       "collaboration": 76, "adaptability": 69, "creativity": 66},
            "centrality": {"betweenness": 0.33, "eigenvector": 0.50,
                           "unblocking_count": 4, "knowledge_transfer": 0.52},
            "persona": "steady",
        },
        "employee6@sentinel.local": {
            "velocity": 68.0, "risk_level": "ELEVATED", "confidence": 0.78,
            "belongingness": 0.55,
            "skills": {"technical": 50, "communication": 60, "leadership": 53,
                       "collaboration": 36, "adaptability": 46, "creativity": 56},
            "centrality": {"betweenness": 0.26, "eigenvector": 0.30,
                           "unblocking_count": 2, "knowledge_transfer": 0.26},
            "persona": "contagion",
            "consent_share_with_manager": False,  # RBAC demo: blocked view
        },
        "employee7@sentinel.local": {
            "velocity": 30.0, "risk_level": "LOW",      "confidence": 0.63,
            "belongingness": 0.85,
            "skills": {"technical": 63, "communication": 68, "leadership": 38,
                       "collaboration": 70, "adaptability": 86, "creativity": 73},
            "centrality": {"betweenness": 0.16, "eigenvector": 0.32,
                           "unblocking_count": 1, "knowledge_transfer": 0.30},
            "persona": "new_hire",
        },
    }

    # ----------------------------------------------------------- hash all users
    now = datetime.utcnow()
    user_hashes: dict[str, str] = {
        email: privacy.hash_identity(email)
        for email in PERSONA_SPECS
    }
    # -------------------------------------------------------- helper functions
    def _ts(days_ago: int, hour: int) -> datetime:
        """Return a datetime offset from now by days_ago days at the given hour."""
        return now - timedelta(days=days_ago) + timedelta(
            hours=hour - 24, minutes=random.randint(0, 59)
        )

    def _risk_history_rows(email: str, user_hash: str, tenant_id: str) -> list[dict]:
        """Generate 14 daily risk history snapshots for one user."""
        spec = PERSONA_SPECS[email]
        base_vel = spec["velocity"]
        base_bel = spec["belongingness"]
        base_conf = spec["confidence"]
        base_level = spec["risk_level"]
        persona = spec["persona"]
        rows = []

        for day in range(14, 0, -1):
            # Persona-specific trajectory logic
            if persona == "burnout":
                # Accelerating: worse in recent days
                progress = (14 - day) / 14.0
                vel = round(base_vel * (0.75 + 0.25 * progress) + random.uniform(-1, 1), 2)
                bel = round(max(0.05, base_bel * (1.3 - 0.3 * progress) + random.uniform(-0.01, 0.01)), 3)
                level = "CRITICAL" if day <= 7 else "ELEVATED"
            elif persona == "hidden_gem":
                vel = round(base_vel + random.uniform(-2, 2), 2)
                bel = round(min(1.0, base_bel + random.uniform(-0.02, 0.02)), 3)
                level = "LOW"
            elif persona == "contagion":
                vel = round(base_vel + random.uniform(-3, 3), 2)
                bel = round(base_bel + random.uniform(-0.03, 0.03), 3)
                level = "ELEVATED" if random.random() > 0.3 else "LOW"
            elif persona == "struggling":
                # Gradually worsening
                progress = (14 - day) / 14.0
                vel = round(base_vel * (0.9 + 0.1 * progress) + random.uniform(-1, 1), 2)
                bel = round(max(0.1, base_bel - 0.02 * progress + random.uniform(-0.01, 0.01)), 3)
                level = "ELEVATED"
            elif persona == "high_performer":
                vel = round(base_vel + random.uniform(-2, 2), 2)
                bel = round(min(1.0, base_bel + random.uniform(-0.02, 0.02)), 3)
                level = "LOW"
            elif persona == "new_hire":
                # Onboarding dip then learning curve
                vel = round(base_vel + (14 - day) * 0.8 + random.uniform(-1, 1), 2)
                bel = round(min(1.0, base_bel + random.uniform(-0.03, 0.03)), 3)
                level = "LOW"
            else:
                vel = round(base_vel + random.uniform(-2, 2), 2)
                bel = round(max(0.0, min(1.0, base_bel + random.uniform(-0.02, 0.02))), 3)
                level = base_level

            rows.append({
                "user_hash": user_hash,
                "tenant_id": tenant_id,
                "risk_level": level,
                "velocity": vel,
                "confidence": round(base_conf + random.uniform(-0.02, 0.02), 3),
                "belongingness_score": bel,
                "timestamp": now - timedelta(days=day),
            })
        return rows

    def _event_rows(email: str, user_hash: str, tenant_id: str) -> list[dict]:
        """Generate 10-20 behavioral events over 14 days for one user."""
        spec = PERSONA_SPECS[email]
        persona = spec["persona"]
        other_hashes = [h for e, h in user_hashes.items() if e != email]

        def pick_target() -> str | None:
            return random.choice(other_hashes) if other_hashes else None

        rows = []

        if persona == "burnout":
            # High volume, after-hours, escalating
            for day in range(14, 0, -1):
                n_events = random.randint(8, 12) if day <= 7 else random.randint(5, 8)
                for _ in range(n_events):
                    hour = random.randint(6, 23)
                    after_h = hour >= 20 or hour < 8
                    et = random.choices(
                        ["commit", "pr_review", "slack_message"],
                        weights=[50, 30, 20]
                    )[0]
                    rows.append({
                        "user_hash": user_hash,
                        "tenant_id": tenant_id,
                        "timestamp": _ts(day, hour),
                        "event_type": et,
                        "target_user_hash": None,
                        "metadata": {
                            "after_hours": after_h,
                            "context_switches": random.randint(6, 13),
                            "lines_changed": random.randint(20, 400) if et == "commit" else None,
                        },
                    })

        elif persona == "hidden_gem":
            # Collaborative unblocking pattern
            for day in range(14, 0, -1):
                for _ in range(random.randint(4, 6)):
                    hour = random.randint(9, 17)
                    et = random.choices(
                        ["commit", "pr_review", "code_review", "unblocked"],
                        weights=[35, 25, 20, 20]
                    )[0]
                    tgt = pick_target() if et in ("pr_review", "code_review", "unblocked") else None
                    rows.append({
                        "user_hash": user_hash,
                        "tenant_id": tenant_id,
                        "timestamp": _ts(day, hour),
                        "event_type": et,
                        "target_user_hash": tgt,
                        "metadata": {
                            "after_hours": False,
                            "context_switches": random.randint(1, 3),
                            "unblocking_count": 1 if et == "unblocked" else 0,
                        },
                    })

        elif persona == "contagion":
            # Negative sentiment in slack
            for day in range(14, 0, -1):
                for _ in range(random.randint(2, 4)):
                    hour = random.randint(9, 17)
                    et = random.choices(
                        ["slack_message", "commit", "pr_review"],
                        weights=[55, 30, 15]
                    )[0]
                    sentiment = round(random.uniform(-0.7, -0.3), 2) if et == "slack_message" else None
                    rows.append({
                        "user_hash": user_hash,
                        "tenant_id": tenant_id,
                        "timestamp": _ts(day, hour),
                        "event_type": et,
                        "target_user_hash": pick_target() if et == "slack_message" else None,
                        "metadata": {
                            "after_hours": False,
                            "sentiment": sentiment,
                            "context_switches": random.randint(3, 7),
                            "topic": random.choice(["deadline", "scope_creep", "process"]),
                        },
                    })

        elif persona == "struggling":
            # Sparse, isolated
            for day in range(14, 0, -1):
                for _ in range(random.randint(2, 5)):
                    et = random.choices(
                        ["commit", "slack_message", "pr_review"],
                        weights=[60, 25, 15]
                    )[0]
                    tgt = pick_target() if random.random() > 0.75 else None
                    rows.append({
                        "user_hash": user_hash,
                        "tenant_id": tenant_id,
                        "timestamp": _ts(day, random.randint(9, 18)),
                        "event_type": et,
                        "target_user_hash": tgt,
                        "metadata": {
                            "after_hours": random.random() > 0.85,
                            "context_switches": random.randint(2, 5),
                            "isolated": True,
                        },
                    })

        elif persona == "high_performer":
            # Balanced, mentoring pattern
            for day in range(14, 0, -1):
                for _ in range(random.randint(5, 7)):
                    hour = random.randint(9, 18)
                    et = random.choices(
                        ["commit", "pr_review", "code_review", "slack_message"],
                        weights=[40, 30, 20, 10]
                    )[0]
                    tgt = pick_target() if et in ("pr_review", "code_review") else None
                    rows.append({
                        "user_hash": user_hash,
                        "tenant_id": tenant_id,
                        "timestamp": _ts(day, hour),
                        "event_type": et,
                        "target_user_hash": tgt,
                        "metadata": {
                            "after_hours": False,
                            "context_switches": random.randint(2, 4),
                            "comment_length": random.randint(80, 350) if "review" in et else None,
                        },
                    })

        elif persona == "new_hire":
            # Questions early, commits later
            for day in range(14, 0, -1):
                n = random.randint(1, 3) if day > 10 else random.randint(3, 5)
                for _ in range(n):
                    hour = random.randint(9, 17)
                    if day > 10:
                        et = random.choices(["slack_message", "commit"], weights=[65, 35])[0]
                        is_q = et == "slack_message" and random.random() > 0.3
                    else:
                        et = random.choices(["commit", "slack_message", "pr_review"], weights=[60, 25, 15])[0]
                        is_q = False
                    rows.append({
                        "user_hash": user_hash,
                        "tenant_id": tenant_id,
                        "timestamp": _ts(day, hour),
                        "event_type": et,
                        "target_user_hash": pick_target() if is_q else None,
                        "metadata": {
                            "after_hours": False,
                            "is_question": is_q,
                            "context_switches": 4 if day > 10 else 2,
                        },
                    })

        elif persona in ("manager_healthy", "cto"):
            # Coordination-heavy
            for day in range(14, 0, -1):
                for _ in range(random.randint(2, 4)):
                    hour = random.randint(9, 17)
                    et = random.choices(
                        ["slack_message", "pr_review", "standup", "commit"],
                        weights=[40, 25, 25, 10]
                    )[0]
                    rows.append({
                        "user_hash": user_hash,
                        "tenant_id": tenant_id,
                        "timestamp": _ts(day, hour),
                        "event_type": et,
                        "target_user_hash": pick_target() if et != "commit" else None,
                        "metadata": {
                            "after_hours": False,
                            "context_switches": random.randint(3, 7),
                        },
                    })

        else:
            # Steady / generic
            for day in range(14, 0, -1):
                for _ in range(random.randint(3, 5)):
                    et = random.choices(
                        ["commit", "pr_review", "slack_message"],
                        weights=[55, 25, 20]
                    )[0]
                    rows.append({
                        "user_hash": user_hash,
                        "tenant_id": tenant_id,
                        "timestamp": _ts(day, random.randint(9, 17)),
                        "event_type": et,
                        "target_user_hash": pick_target() if et != "commit" else None,
                        "metadata": {
                            "after_hours": False,
                            "context_switches": random.randint(2, 4),
                        },
                    })

        return rows

    # ------------------------------------------------ retrieve tenant_id
    SHARED_TENANT_SLUG = "sentinel-workspace"
    tenant_id: str | None = None

    with Session() as db:
        try:
            row = db.execute(
                text("SELECT id FROM identity.tenants WHERE slug = :slug"),
                {"slug": SHARED_TENANT_SLUG},
            ).fetchone()
            if row is None:
                print("   Tenant 'sentinel-workspace' not found. Run seed_database_records() first.")
                return
            tenant_id = str(row[0])
            print(f"   Tenant ID: {tenant_id}")
        except Exception as e:
            print(f"   Could not retrieve tenant ID: {e}")
            return

    # -------------------------------------------------------- Step 1: consent updates
    print(f"\n   Step 1: Applying consent variations...")
    CONSENT_BLOCKED = {"employee1@sentinel.local", "employee6@sentinel.local"}

    with Session() as db:
        try:
            for email in PERSONA_SPECS:
                uh = user_hashes[email]
                consent_val = email not in CONSENT_BLOCKED
                db.execute(
                    text(
                        """
                        UPDATE identity.users
                        SET    consent_share_with_manager = :consent
                        WHERE  user_hash = :user_hash
                        """
                    ),
                    {"consent": consent_val, "user_hash": uh},
                )
                if not consent_val:
                    print(f"      {email} -> consent_share_with_manager=False (RBAC demo)")
            db.commit()
            print(f"   Consent variations applied.")
        except Exception as e:
            db.rollback()
            print(f"   Failed to apply consent variations: {e}")
            return

    # -------------------------------------------------------- Step 2: risk_scores
    print(f"\n   Step 2: Seeding analytics.risk_scores ({len(PERSONA_SPECS)} rows)...")

    with Session() as db:
        try:
            for email, spec in PERSONA_SPECS.items():
                uh = user_hashes[email]
                db.execute(
                    text(
                        """
                        INSERT INTO analytics.risk_scores
                            (user_hash, tenant_id, velocity, risk_level, confidence,
                             thwarted_belongingness, updated_at)
                        VALUES
                            (:user_hash, CAST(:tenant_id AS uuid), :velocity, :risk_level,
                             :confidence, :belongingness, NOW())
                        ON CONFLICT (user_hash) DO UPDATE SET
                            tenant_id              = EXCLUDED.tenant_id,
                            velocity               = EXCLUDED.velocity,
                            risk_level             = EXCLUDED.risk_level,
                            confidence             = EXCLUDED.confidence,
                            thwarted_belongingness = EXCLUDED.thwarted_belongingness,
                            updated_at             = NOW()
                        """
                    ),
                    {
                        "user_hash":    uh,
                        "tenant_id":    tenant_id,
                        "velocity":     spec["velocity"],
                        "risk_level":   spec["risk_level"],
                        "confidence":   spec["confidence"],
                        "belongingness": spec["belongingness"],
                    },
                )
                print(f"      {email} -> {spec['risk_level']} vel={spec['velocity']}")
            db.commit()
            print(f"   risk_scores committed.")
        except Exception as e:
            db.rollback()
            print(f"   Failed to seed risk_scores: {e}")
            return

    # -------------------------------------------------------- Step 3: risk_history
    print(f"\n   Step 3: Seeding analytics.risk_history (14 days per user)...")

    with Session() as db:
        try:
            # Delete existing history for these users so we can re-insert cleanly
            for uh in user_hashes.values():
                db.execute(
                    text("DELETE FROM analytics.risk_history WHERE user_hash = :uh"),
                    {"uh": uh},
                )

            history_count = 0
            for email, uh in user_hashes.items():
                rows = _risk_history_rows(email, uh, tenant_id)
                for row in rows:
                    db.execute(
                        text(
                            """
                            INSERT INTO analytics.risk_history
                                (user_hash, tenant_id, risk_level, velocity, confidence,
                                 belongingness_score, timestamp)
                            VALUES
                                (:user_hash, CAST(:tenant_id AS uuid), :risk_level, :velocity,
                                 :confidence, :belongingness_score, :timestamp)
                            """
                        ),
                        row,
                    )
                history_count += len(rows)

            db.commit()
            print(f"   risk_history committed ({history_count} rows across {len(PERSONA_SPECS)} users).")
        except Exception as e:
            db.rollback()
            print(f"   Failed to seed risk_history: {e}")
            return

    # -------------------------------------------------------- Step 4: events
    print(f"\n   Step 4: Seeding analytics.events (10-20 per user over 14 days)...")

    with Session() as db:
        try:
            # Delete existing events for these users
            for uh in user_hashes.values():
                db.execute(
                    text("DELETE FROM analytics.events WHERE user_hash = :uh"),
                    {"uh": uh},
                )

            import json as _json
            event_count = 0
            for email, uh in user_hashes.items():
                rows = _event_rows(email, uh, tenant_id)
                for row in rows:
                    db.execute(
                        text(
                            """
                            INSERT INTO analytics.events
                                (user_hash, tenant_id, timestamp, event_type,
                                 target_user_hash, metadata)
                            VALUES
                                (:user_hash, CAST(:tenant_id AS uuid), :timestamp, :event_type,
                                 :target_user_hash, CAST(:metadata AS jsonb))
                            """
                        ),
                        {
                            "user_hash":        row["user_hash"],
                            "tenant_id":        row["tenant_id"],
                            "timestamp":        row["timestamp"],
                            "event_type":       row["event_type"],
                            "target_user_hash": row["target_user_hash"],
                            "metadata":         _json.dumps(row["metadata"]),
                        },
                    )
                event_count += len(rows)
                print(f"      {email} -> {len(rows)} events")

            db.commit()
            print(f"   events committed ({event_count} total rows).")
        except Exception as e:
            db.rollback()
            print(f"   Failed to seed events: {e}")
            return

    # -------------------------------------------------------- Step 5: graph_edges
    print(f"\n   Step 5: Seeding analytics.graph_edges...")

    # Define collaboration graph for the sentinel team
    H = user_hashes
    EDGES: list[dict] = []

    def _edge(src_email: str, tgt_email: str, weight: float, edge_type: str, days_ago: int | None = None) -> None:
        src = H.get(src_email)
        tgt = H.get(tgt_email)
        if not src or not tgt:
            return
        d = days_ago if days_ago is not None else random.randint(0, 3)
        EDGES.append({
            "tenant_id":        tenant_id,
            "source_hash":      src,
            "target_hash":      tgt,
            "weight":           round(weight + random.uniform(-0.02, 0.02), 3),
            "last_interaction": now - timedelta(days=d),
            "edge_type":        edge_type,
        })

    # Collaboration edges (bidirectional for key pairs)
    _edge("manager1@sentinel.local", "employee1@sentinel.local",  0.82, "collaboration")
    _edge("employee1@sentinel.local", "manager1@sentinel.local",  0.82, "collaboration")
    _edge("manager1@sentinel.local", "employee2@sentinel.local",  0.75, "collaboration")
    _edge("employee2@sentinel.local", "manager1@sentinel.local",  0.75, "collaboration")
    _edge("manager2@sentinel.local", "employee3@sentinel.local",  0.70, "collaboration")
    _edge("employee3@sentinel.local", "manager2@sentinel.local",  0.70, "collaboration")
    _edge("manager2@sentinel.local", "employee4@sentinel.local",  0.78, "collaboration")
    _edge("employee4@sentinel.local", "manager2@sentinel.local",  0.78, "collaboration")
    _edge("manager2@sentinel.local", "employee5@sentinel.local",  0.65, "collaboration")
    _edge("employee5@sentinel.local", "manager2@sentinel.local",  0.65, "collaboration")
    _edge("manager2@sentinel.local", "employee6@sentinel.local",  0.60, "collaboration")
    _edge("employee6@sentinel.local", "manager2@sentinel.local",  0.60, "collaboration")
    _edge("manager2@sentinel.local", "employee7@sentinel.local",  0.55, "collaboration")
    _edge("employee7@sentinel.local", "manager2@sentinel.local",  0.55, "collaboration")
    _edge("admin@sentinel.local",    "manager1@sentinel.local",   0.50, "collaboration")
    _edge("manager1@sentinel.local", "admin@sentinel.local",      0.50, "collaboration")
    _edge("admin@sentinel.local",    "manager2@sentinel.local",   0.45, "collaboration")
    _edge("manager2@sentinel.local", "admin@sentinel.local",      0.45, "collaboration")
    # Cross-team collaboration
    _edge("employee2@sentinel.local", "employee1@sentinel.local", 0.42, "collaboration")  # hidden gem unblocks burnout
    _edge("employee2@sentinel.local", "employee5@sentinel.local", 0.65, "collaboration")
    _edge("employee4@sentinel.local", "employee7@sentinel.local", 0.52, "collaboration")  # high_performer mentors new_hire
    _edge("employee5@sentinel.local", "employee4@sentinel.local", 0.58, "collaboration")

    # Mentorship edges
    _edge("employee4@sentinel.local", "employee7@sentinel.local", 0.80, "mentorship")
    _edge("employee2@sentinel.local", "employee5@sentinel.local", 0.71, "mentorship")
    _edge("manager1@sentinel.local",  "employee1@sentinel.local", 0.44, "mentorship")  # trying to help

    # Blocking edges (manager2 contagion pattern)
    _edge("manager2@sentinel.local", "employee2@sentinel.local", 0.38, "blocking", days_ago=2)
    _edge("manager2@sentinel.local", "employee4@sentinel.local", 0.32, "blocking", days_ago=4)
    _edge("manager2@sentinel.local", "employee3@sentinel.local", 0.28, "blocking", days_ago=3)

    with Session() as db:
        try:
            # Delete existing graph edges for this tenant
            db.execute(
                text("DELETE FROM analytics.graph_edges WHERE tenant_id = CAST(:tid AS uuid)"),
                {"tid": tenant_id},
            )

            for edge in EDGES:
                db.execute(
                    text(
                        """
                        INSERT INTO analytics.graph_edges
                            (tenant_id, source_hash, target_hash, weight,
                             last_interaction, edge_type)
                        VALUES
                            (CAST(:tenant_id AS uuid), :source_hash, :target_hash, :weight,
                             :last_interaction, :edge_type)
                        """
                    ),
                    edge,
                )

            db.commit()
            print(f"   graph_edges committed ({len(EDGES)} edges).")
        except Exception as e:
            db.rollback()
            print(f"   Failed to seed graph_edges: {e}")
            return

    # -------------------------------------------------------- Step 6: centrality_scores
    print(f"\n   Step 6: Seeding analytics.centrality_scores ({len(PERSONA_SPECS)} rows)...")

    with Session() as db:
        try:
            for email, spec in PERSONA_SPECS.items():
                uh = user_hashes[email]
                c = spec["centrality"]
                db.execute(
                    text(
                        """
                        INSERT INTO analytics.centrality_scores
                            (user_hash, tenant_id, betweenness, eigenvector,
                             unblocking_count, knowledge_transfer_score, calculated_at)
                        VALUES
                            (:user_hash, CAST(:tenant_id AS uuid), :betweenness, :eigenvector,
                             :unblocking_count, :knowledge_transfer_score, NOW())
                        ON CONFLICT (user_hash) DO UPDATE SET
                            tenant_id                = EXCLUDED.tenant_id,
                            betweenness              = EXCLUDED.betweenness,
                            eigenvector              = EXCLUDED.eigenvector,
                            unblocking_count         = EXCLUDED.unblocking_count,
                            knowledge_transfer_score = EXCLUDED.knowledge_transfer_score,
                            calculated_at            = NOW()
                        """
                    ),
                    {
                        "user_hash":               uh,
                        "tenant_id":               tenant_id,
                        "betweenness":             c["betweenness"],
                        "eigenvector":             c["eigenvector"],
                        "unblocking_count":        c["unblocking_count"],
                        "knowledge_transfer_score": c["knowledge_transfer"],
                    },
                )
                print(f"      {email} -> btw={c['betweenness']} eig={c['eigenvector']}")
            db.commit()
            print(f"   centrality_scores committed.")
        except Exception as e:
            db.rollback()
            print(f"   Failed to seed centrality_scores: {e}")
            return

    # -------------------------------------------------------- Step 7: skill_profiles
    print(f"\n   Step 7: Seeding analytics.skill_profiles ({len(PERSONA_SPECS)} rows)...")

    with Session() as db:
        try:
            for email, spec in PERSONA_SPECS.items():
                uh = user_hashes[email]
                s = spec["skills"]
                db.execute(
                    text(
                        """
                        INSERT INTO analytics.skill_profiles
                            (user_hash, tenant_id, technical, communication, leadership,
                             collaboration, adaptability, creativity, updated_at)
                        VALUES
                            (:user_hash, CAST(:tenant_id AS uuid), :technical, :communication,
                             :leadership, :collaboration, :adaptability, :creativity, NOW())
                        ON CONFLICT (user_hash) DO UPDATE SET
                            tenant_id     = EXCLUDED.tenant_id,
                            technical     = EXCLUDED.technical,
                            communication = EXCLUDED.communication,
                            leadership    = EXCLUDED.leadership,
                            collaboration = EXCLUDED.collaboration,
                            adaptability  = EXCLUDED.adaptability,
                            creativity    = EXCLUDED.creativity,
                            updated_at    = NOW()
                        """
                    ),
                    {
                        "user_hash":    uh,
                        "tenant_id":    tenant_id,
                        "technical":    float(s["technical"]),
                        "communication": float(s["communication"]),
                        "leadership":   float(s["leadership"]),
                        "collaboration": float(s["collaboration"]),
                        "adaptability": float(s["adaptability"]),
                        "creativity":   float(s["creativity"]),
                    },
                )
                print(f"      {email} -> tech={s['technical']} comm={s['communication']}")
            db.commit()
            print(f"   skill_profiles committed.")
        except Exception as e:
            db.rollback()
            print(f"   Failed to seed skill_profiles: {e}")
            return

    print()
    print("=" * 60)
    print("   Engine data seeding complete.")
    print(f"   Users seeded : {len(PERSONA_SPECS)}")
    print(f"   Graph edges  : {len(EDGES)}")
    print("=" * 60)


if __name__ == "__main__":
    seed_auth_users()
    seed_database_records()
    seed_engine_data()
