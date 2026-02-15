"""
Seed Supabase Auth users for testing.

This script creates users in Supabase Auth (auth.users) table.
Run this AFTER setting correct SUPABASE_SERVICE_ROLE_KEY in .env

Usage:
    cd backend
    python scripts/seed_supabase_auth.py

Prerequisites:
    1. Set SUPABASE_SERVICE_KEY in backend/.env (must be a JWT with "role": "service_role")
    2. Get the key from: Supabase Dashboard → Project Settings → API → service_role secret
    3. Verify the key: Decode at jwt.io - payload should show "role": "service_role"
"""
import os
import sys
import json
import base64
import asyncio
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from supabase import create_client, Client

# Test users matching TEST_USERS.md
TEST_USERS = [
    {
        "email": "admin@sentinel.local",
        "password": "Admin123!",
        "role": "admin",
        "display_name": "Admin User",
        "user_metadata": {
            "role": "admin",
            "display_name": "Admin User"
        }
    },
    {
        "email": "manager1@sentinel.local",
        "password": "Manager123!",
        "role": "manager",
        "display_name": "Manager One",
        "user_metadata": {
            "role": "manager",
            "display_name": "Manager One"
        }
    },
    {
        "email": "manager2@sentinel.local",
        "password": "Manager456!",
        "role": "manager",
        "display_name": "Manager Two",
        "user_metadata": {
            "role": "manager",
            "display_name": "Manager Two"
        }
    },
    {
        "email": "employee1@sentinel.local",
        "password": "Employee123!",
        "role": "employee",
        "display_name": "Employee One",
        "user_metadata": {
            "role": "employee",
            "display_name": "Employee One",
            "manager_email": "manager1@sentinel.local"
        }
    },
    {
        "email": "employee2@sentinel.local",
        "password": "Employee456!",
        "role": "employee",
        "display_name": "Employee Two",
        "user_metadata": {
            "role": "employee",
            "display_name": "Employee Two",
            "manager_email": "manager1@sentinel.local"
        }
    },
    {
        "email": "employee3@sentinel.local",
        "password": "Employee789!",
        "role": "employee",
        "display_name": "Employee Three",
        "user_metadata": {
            "role": "employee",
            "display_name": "Employee Three",
            "manager_email": "manager2@sentinel.local"
        }
    }
]


def load_env_vars() -> tuple[str, str]:
    """Load Supabase credentials from environment or .env file."""
    # Try to load from .env file if python-dotenv is available
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).parent.parent / ".env"
        load_dotenv(env_path)
    except ImportError:
        print("Note: python-dotenv not installed, using system environment variables")
    
    supabase_url = os.getenv("SUPABASE_URL")
    service_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    
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
        parts = token.split('.')
        if len(parts) != 3:
            return {}
        
        # Decode the payload (second part)
        payload = parts[1]
        # Add padding if needed (base64url may have missing padding)
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += '=' * padding
        
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
            "Your JWT payload shows: \"role\": \"anon\"\n"
            "Required JWT payload: \"role\": \"service_role\"\n\n"
            "To fix this:\n"
            "1. Go to Supabase Dashboard → Project Settings → API\n"
            "2. Find 'Project API keys' section\n"
            "3. Copy the 'service_role' secret (click 'Reveal' to show it)\n"
            "4. Update SUPABASE_SERVICE_KEY in backend/.env with the service_role key\n"
            "5. Verify: Decode at jwt.io - payload should show \"role\": \"service_role\"\n\n"
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
        users_list = response if isinstance(response, list) else getattr(response, 'users', [])
        
        for user in users_list:
            # user might be a dict or an object with email attribute
            user_email = user.email if hasattr(user, 'email') else user.get('email')
            if user_email == email:
                # Return as dict for consistency
                if hasattr(user, 'model_dump'):
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
        response = client.auth.admin.create_user({
            "email": user_data["email"],
            "password": user_data["password"],
            "email_confirm": True,  # Auto-confirm email for testing
            "user_metadata": user_data.get("user_metadata", {})
        })
        return response.model_dump()
    except Exception as e:
        error_msg = str(e)
        # Check if user already exists
        if "already been registered" in error_msg.lower() or "already exists" in error_msg.lower():
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
                "email_confirm": True  # Ensure email stays confirmed
            }
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
                        print(f"      ✓ Updated password and metadata: role={user['role']}")
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


if __name__ == "__main__":
    seed_auth_users()
