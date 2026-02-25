import os
import json
import urllib.request
import urllib.error

# Load env
from dotenv import load_dotenv

load_dotenv(".env")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
PASSWORD = os.getenv("SEED_PASSWORD", "")

TEST_EMAILS = [
    "admin@company.com",
    "manager.one@company.com",
    "manager.two@company.com",
    "alex.chen@company.com",
    "sarah.jones@company.com",
    "jordan.smith@company.com",
]

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {SERVICE_KEY}",
    "apikey": SERVICE_KEY,
}

print(f"Setting password to: {PASSWORD}")

for email in TEST_EMAILS:
    try:
        # Search for user
        search_url = f"{SUPABASE_URL}/auth/v1/admin/users?email={email}"
        req = urllib.request.Request(search_url, headers=headers, method="GET")
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode())

        if data.get("users"):
            user_id = data["users"][0]["id"]

            # Update password
            update_url = f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}"
            update_body = json.dumps({"password": PASSWORD})
            update_req = urllib.request.Request(
                update_url, data=update_body.encode(), headers=headers, method="PUT"
            )
            update_resp = urllib.request.urlopen(update_req, timeout=15)
            print(f"[OK] {email} - password updated")
        else:
            print(f"[SKIP] {email} - not found")

    except Exception as e:
        print(f"[ERR] {email} - {e}")

print("\nDone! Use these credentials:")
print(f"Email: any of the emails above")
print(f"Password: {PASSWORD}")
