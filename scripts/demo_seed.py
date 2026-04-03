"""
Demo Seed Script — Creates a complete demo environment for hackathon presentation.
Usage: cd backend && python -m scripts.demo_seed
"""

import sys
import os
import logging
from uuid import uuid4
from datetime import datetime, timedelta
import random

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "backend"))

from app.core.database import SessionLocal, get_supabase_admin_client
from app.core.security import privacy
from app.models.identity import UserIdentity, AuditLog
from app.models.tenant import Tenant, TenantMember
from app.models.notification import Notification, NotificationPreference

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("demo_seed")

# Demo users
DEMO_USERS = [
    {"email": "admin@sentinel.demo", "role": "admin", "name": "Alex Rivera"},
    {"email": "sarah.kim@sentinel.demo", "role": "manager", "name": "Sarah Kim"},
    {"email": "jordan.smith@sentinel.demo", "role": "manager", "name": "Jordan Smith"},
    {"email": "maria.santos@sentinel.demo", "role": "employee", "name": "Maria Santos"},
    {"email": "priya.sharma@sentinel.demo", "role": "employee", "name": "Priya Sharma"},
    {"email": "david.lee@sentinel.demo", "role": "employee", "name": "David Lee"},
    {"email": "emma.wilson@sentinel.demo", "role": "employee", "name": "Emma Wilson"},
    {"email": "james.brown@sentinel.demo", "role": "employee", "name": "James Brown"},
    {"email": "lisa.chen@sentinel.demo", "role": "employee", "name": "Lisa Chen"},
    {"email": "mike.johnson@sentinel.demo", "role": "employee", "name": "Mike Johnson"},
]

DEMO_TENANT = {
    "name": "Acme Corporation",
    "slug": "acme-corp",
    "plan": "enterprise",
}

NOTIFICATION_TEMPLATES = [
    {
        "type": "auth",
        "title": "Welcome to Sentinel",
        "message": "Your account has been set up successfully. Explore the dashboard to get started.",
        "priority": "normal",
    },
    {
        "type": "team",
        "title": "Team Assignment",
        "message": "You have been added to the Engineering team.",
        "priority": "normal",
    },
    {
        "type": "security",
        "title": "Security Alert",
        "message": "A new login was detected from Chrome on Windows.",
        "priority": "high",
    },
    {
        "type": "system",
        "title": "System Update",
        "message": "Sentinel v2.1 has been deployed with new features.",
        "priority": "low",
    },
    {
        "type": "activity",
        "title": "Weekly Summary",
        "message": "Your wellbeing score improved by 12% this week.",
        "priority": "normal",
    },
    {
        "type": "team",
        "title": "New Team Member",
        "message": "Emma Wilson has joined the Engineering team.",
        "priority": "normal",
    },
    {
        "type": "security",
        "title": "Password Changed",
        "message": "Your password was successfully updated.",
        "priority": "high",
    },
    {
        "type": "system",
        "title": "Maintenance Window",
        "message": "Scheduled maintenance on Sunday 2-4 AM UTC.",
        "priority": "normal",
    },
    {
        "type": "activity",
        "title": "Risk Alert",
        "message": "Your burnout risk level has increased to ELEVATED.",
        "priority": "high",
    },
    {
        "type": "team",
        "title": "Manager Update",
        "message": "Sarah Kim has shared a new team report.",
        "priority": "normal",
    },
]


def seed_demo():
    log.info("=" * 60)
    log.info("  SENTINEL — Demo Seed Script")
    log.info("=" * 60)

    db = SessionLocal()

    try:
        # Create tenant
        existing_tenant = db.query(Tenant).filter_by(slug=DEMO_TENANT["slug"]).first()
        if existing_tenant:
            log.info(f"  [EXISTS] Tenant '{DEMO_TENANT['name']}'")
            tenant = existing_tenant
        else:
            tenant = Tenant(
                name=DEMO_TENANT["name"],
                slug=DEMO_TENANT["slug"],
                plan=DEMO_TENANT["plan"],
                status="active",
                settings={
                    "timezone": "UTC",
                    "language": "en",
                    "date_format": "YYYY-MM-DD",
                },
            )
            db.add(tenant)
            db.flush()
            log.info(
                f"  [CREATE] Tenant '{DEMO_TENANT['name']}' — plan={DEMO_TENANT['plan']}"
            )

        # Create users
        created_users = []
        for u in DEMO_USERS:
            user_hash = privacy.hash_identity(u["email"])
            existing_user = (
                db.query(UserIdentity).filter_by(user_hash=user_hash).first()
            )

            # Create Supabase Auth user
            try:
                admin_client = get_supabase_admin_client()
                admin_client.auth.admin.create_user(
                    {
                        "email": u["email"],
                        "password": "Demo123!",
                        "email_confirm": True,
                        "user_metadata": {"role": u["role"], "name": u.get("name", "")},
                    }
                )
                log.info(f"    [AUTH] Supabase user created: {u['email']}")
            except Exception as e:
                # User may already exist in Supabase
                log.info(
                    f"    [AUTH] Supabase user may already exist: {u['email']} ({e})"
                )

            if not existing_user:
                user = UserIdentity(
                    user_hash=user_hash,
                    email_encrypted=privacy.encrypt(u["email"]),
                    role=u["role"],
                    consent_share_with_manager=True if u["role"] != "admin" else True,
                    consent_share_anonymized=True,
                )
                db.add(user)
                db.flush()
                log.info(f"  [CREATE] User {u['email']} (role={u['role']})")
            else:
                user = existing_user
                log.info(f"  [EXISTS] User {u['email']}")

            created_users.append((user, u))

            # Add to tenant
            existing_member = (
                db.query(TenantMember)
                .filter_by(tenant_id=tenant.id, user_hash=user_hash)
                .first()
            )
            if not existing_member:
                member_role = "owner" if u["role"] == "admin" else "member"
                db.add(
                    TenantMember(
                        tenant_id=tenant.id,
                        user_hash=user_hash,
                        role=member_role,
                    )
                )
                log.info(f"    [MEMBER] {u['email']} → {member_role}")

        # Compute hashes needed for audit log entries
        admin_hash = privacy.hash_identity("admin@sentinel.demo")
        manager1_hash = privacy.hash_identity("sarah.kim@sentinel.demo")

        # Create notifications for all users
        for user, user_data in created_users:
            # Check if user already has notifications
            existing_count = (
                db.query(Notification).filter_by(user_hash=user.user_hash).count()
            )
            if existing_count > 0:
                log.info(
                    f"  [SKIP] Notifications for {user_data['email']} ({existing_count} exist)"
                )
                continue

            # Create 3-5 notifications per user
            num_notifications = random.randint(3, 5)
            selected = random.sample(
                NOTIFICATION_TEMPLATES,
                min(num_notifications, len(NOTIFICATION_TEMPLATES)),
            )

            for i, tmpl in enumerate(selected):
                notification = Notification(
                    user_hash=user.user_hash,
                    tenant_id=tenant.id,
                    type=tmpl["type"],
                    title=tmpl["title"],
                    message=tmpl["message"],
                    priority=tmpl["priority"],
                    read_at=datetime.utcnow() - timedelta(hours=random.randint(1, 48))
                    if i > 1
                    else None,
                    created_at=datetime.utcnow()
                    - timedelta(hours=random.randint(1, 72)),
                )
                db.add(notification)

            log.info(
                f"  [NOTIFY] {num_notifications} notifications for {user_data['email']}"
            )

        # Create default notification preferences for all users
        for user, user_data in created_users:
            existing_prefs = (
                db.query(NotificationPreference)
                .filter_by(user_hash=user.user_hash)
                .count()
            )
            if existing_prefs > 0:
                continue

            for channel in ["in_app", "email"]:
                for ntype in ["auth", "team", "system", "security", "activity"]:
                    db.add(
                        NotificationPreference(
                            user_hash=user.user_hash,
                            channel=channel,
                            notification_type=ntype,
                            enabled=True
                            if channel == "in_app"
                            else ntype in ["auth", "security"],
                        )
                    )

        # Create audit log entries
        for user, user_data in created_users:
            db.add(
                AuditLog(
                    user_hash=user.user_hash,
                    action="auth:login",
                    details={"method": "email", "ip": "192.168.1.1"},
                )
            )

        db.add(
            AuditLog(
                user_hash=admin_hash,
                action="user:role_changed",
                details={
                    "target": manager1_hash,
                    "old_role": "employee",
                    "new_role": "manager",
                },
            )
        )
        db.add(
            AuditLog(
                user_hash=admin_hash,
                action="tenant:created",
                details={"tenant": DEMO_TENANT["name"]},
            )
        )

        db.commit()
        log.info("\n" + "=" * 60)
        log.info("  DEMO SEED COMPLETE")
        log.info("=" * 60)
        log.info(f"\n  Tenant: {DEMO_TENANT['name']} ({DEMO_TENANT['plan']})")
        log.info(f"  Users:  {len(DEMO_USERS)}")
        log.info(f"\n  Demo Login: admin@sentinel.demo / Admin123!")
        log.info("=" * 60)

    except Exception as e:
        log.error(f"\n  Seed failed: {e}")
        db.rollback()
        import traceback

        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    seed_demo()
