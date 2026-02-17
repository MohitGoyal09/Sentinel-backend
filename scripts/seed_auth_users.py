"""
Create test users in Supabase Auth using the Admin API.
This is needed so users can log in via the frontend.

Passwords are read from the SEED_PASSWORD environment variable.
If not set, a secure random password is generated and printed once.
"""

import os
import sys
import json
import secrets
import string

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import urllib.request
import urllib.error

from app.config import get_settings

settings = get_settings()

SUPABASE_URL = settings.supabase_url
SERVICE_KEY = settings.supabase_service_key


def _generate_password(length: int = 16) -> str:
    """Generate a cryptographically secure random password."""
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _get_seed_password() -> str:
    """Read SEED_PASSWORD from env or generate one."""
    pw = os.getenv("SEED_PASSWORD", "")
    if pw:
        return pw
    pw = _generate_password()
    print(f"  [WARN] SEED_PASSWORD not set. Using generated password: {pw}")
    print(f"         Set SEED_PASSWORD in your .env to use a fixed password.\n")
    return pw


TEST_EMAILS = [
    "admin_user@company.com",
    "manager_one@company.com",
    "manager_two@company.com",
    "alex.chen@company.com",
    "sarah.jones@company.com",
    "jordan.smith@company.com",
]


def create_auth_user(email, password):
    """Create a user in Supabase Auth via the admin API."""
    url = f"{SUPABASE_URL}/auth/v1/admin/users"
    body = json.dumps(
        {
            "email": email,
            "password": password,
            "email_confirm": True,
        }
    ).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SERVICE_KEY}",
        "apikey": SERVICE_KEY,
    }

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode())
        uid = data.get("id", "?")
        print(f"  [OK] Created: {email}  (uid={uid})")
        return True
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        if "already been registered" in err_body or "already exists" in err_body:
            print(f"  [SKIP] Already exists: {email}")
            return True
        else:
            print(f"  [ERR] {email} -> HTTP {e.code}: {err_body[:120]}")
            return False
    except Exception as e:
        print(f"  [ERR] {email} -> {e}")
        return False


def main():
    print("=" * 60)
    print("CREATING SUPABASE AUTH ACCOUNTS")
    print("=" * 60)
    print(f"Supabase URL: {SUPABASE_URL}")
    print(f"Service key:  ...{SERVICE_KEY[-8:]}")
    print()

    password = _get_seed_password()

    success = 0
    for email in TEST_EMAILS:
        if create_auth_user(email, password):
            success += 1

    print()
    print(f"Result: {success}/{len(TEST_EMAILS)} users ready")
    print("=" * 60)

    if success == len(TEST_EMAILS):
        print(
            "\nAll test users are ready! You can now log in at http://localhost:3000/login"
        )
        print("\nEmails:")
        for email in TEST_EMAILS:
            print(f"  {email}")
        print(f"\nPassword for all users: (the SEED_PASSWORD you provided)")
    else:
        print("\nSome users failed. Check errors above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
