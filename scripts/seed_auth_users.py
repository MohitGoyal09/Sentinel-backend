"""
Create test users in Supabase Auth for demo.
Emails match the unified seed_demo.py script.

Run:  python scripts/seed_auth_users.py
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


def _get_seed_password() -> str:
    pw = os.getenv("SEED_PASSWORD", "")
    if pw:
        return pw
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    pw = "".join(secrets.choice(alphabet) for _ in range(16))
    print(f"  [WARN] SEED_PASSWORD not set. Generated: {pw}")
    print("         Set SEED_PASSWORD in .env for a fixed password.\n")
    return pw


# Unified demo accounts — matches seed_demo.py exactly
DEMO_EMAILS = [
    "admin@sentinel.local",
    "jordan.chen@sentinel.local",
    "alex.rivera@sentinel.local",
    "sarah.kim@sentinel.local",
    "maria.santos@sentinel.local",
    # Team members
    "priya.sharma@sentinel.local",
    "marcus.johnson@sentinel.local",
    "yuki.tanaka@sentinel.local",
    "david.park@sentinel.local",
    "emma.wilson@sentinel.local",
    "lucas.martinez@sentinel.local",
    "aisha.patel@sentinel.local",
    "chen.wei@sentinel.local",
    "sofia.andersson@sentinel.local",
]


def create_auth_user(email, password):
    """Create a user in Supabase Auth via the admin API."""
    try:
        search_url = f"{SUPABASE_URL}/auth/v1/admin/users?filter=email.eq.{email}"
        search_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {SERVICE_KEY}",
            "apikey": SERVICE_KEY,
        }
        search_req = urllib.request.Request(
            search_url, headers=search_headers, method="GET"
        )
        search_resp = urllib.request.urlopen(search_req, timeout=15)
        search_data = json.loads(search_resp.read().decode())

        if search_data.get("users"):
            user_id = search_data["users"][0]["id"]
            delete_url = f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}"
            delete_req = urllib.request.Request(
                delete_url, headers=search_headers, method="DELETE"
            )
            urllib.request.urlopen(delete_req, timeout=15)
            print(f"  [DEL] Deleted existing: {email}")
    except Exception:
        pass

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
        print(f"  [OK] {email} (uid={uid})")
        return True
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        if "already been registered" in err_body or "already exists" in err_body:
            print(f"  [SKIP] Already exists: {email}")
            return True
        print(f"  [ERR] {email} -> HTTP {e.code}: {err_body[:120]}")
        return False
    except Exception as e:
        print(f"  [ERR] {email} -> {e}")
        return False


def main():
    print("=" * 60)
    print("SUPABASE AUTH — DEMO ACCOUNTS")
    print("=" * 60)

    password = _get_seed_password()
    success = 0
    for email in DEMO_EMAILS:
        if create_auth_user(email, password):
            success += 1

    print(f"\nResult: {success}/{len(DEMO_EMAILS)} accounts ready")

    if success == len(DEMO_EMAILS):
        print("\nDemo Accounts:")
        print("  admin@sentinel.local       — Admin dashboard")
        print("  jordan.chen@sentinel.local — Manager (healthy)")
        print("  alex.rivera@sentinel.local — Employee (burnout demo)")
        print("  sarah.kim@sentinel.local   — Employee (hidden gem)")
        print("  maria.santos@sentinel.local — Employee (contagion)")
        print("\nPassword: (your SEED_PASSWORD from .env)")
    print("=" * 60)


if __name__ == "__main__":
    main()
