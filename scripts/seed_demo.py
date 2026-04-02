"""
Master Demo Seed Script — Creates a complete realistic demo environment.
Usage: cd backend && python -m scripts.seed_demo
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
from app.models.analytics import (
    Event,
    RiskScore,
    GraphEdge,
    CentralityScore,
    RiskHistory,
    SkillProfile,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("seed_demo")

# Demo organization
ORG_NAME = "Acme Technologies"
ORG_SLUG = "acme-technologies"

# Demo users with realistic data
DEMO_USERS = [
    {"email": "admin@acme.com", "name": "Sarah Chen", "role": "admin", "manager": None},
    {"email": "cto@acme.com", "name": "James Wilson", "role": "admin", "manager": None},
    {
        "email": "eng.manager@acme.com",
        "name": "Priya Sharma",
        "role": "manager",
        "manager": "cto@acme.com",
    },
    {
        "email": "design.manager@acme.com",
        "name": "Alex Rivera",
        "role": "manager",
        "manager": "cto@acme.com",
    },
    {
        "email": "dev1@acme.com",
        "name": "Jordan Lee",
        "role": "employee",
        "manager": "eng.manager@acme.com",
    },
    {
        "email": "dev2@acme.com",
        "name": "Maria Santos",
        "role": "employee",
        "manager": "eng.manager@acme.com",
    },
    {
        "email": "dev3@acme.com",
        "name": "David Kim",
        "role": "employee",
        "manager": "eng.manager@acme.com",
    },
    {
        "email": "dev4@acme.com",
        "name": "Emma Thompson",
        "role": "employee",
        "manager": "eng.manager@acme.com",
    },
    {
        "email": "designer1@acme.com",
        "name": "Noah Patel",
        "role": "employee",
        "manager": "design.manager@acme.com",
    },
    {
        "email": "designer2@acme.com",
        "name": "Olivia Zhang",
        "role": "employee",
        "manager": "design.manager@acme.com",
    },
]

RISK_LEVELS = ["LOW", "ELEVATED", "CRITICAL"]
EVENT_TYPES = [
    "commit",
    "pr_review",
    "slack_message",
    "unblocked",
    "standup",
    "code_review",
]

# Notification templates
DEMO_NOTIFICATIONS = [
    {
        "type": "auth",
        "title": "Welcome to Sentinel!",
        "message": "Your account has been set up. Explore your wellbeing dashboard.",
        "priority": "normal",
    },
    {
        "type": "team",
        "title": "Team Standup Reminder",
        "message": "Daily standup in 15 minutes. Don't forget to share your updates.",
        "priority": "normal",
    },
    {
        "type": "security",
        "title": "New Login Detected",
        "message": "A new login was detected from Chrome on macOS. If this wasn't you, secure your account.",
        "priority": "high",
    },
    {
        "type": "system",
        "title": "System Update",
        "message": "Sentinel v2.1 deployed. New features: AI-powered burnout prediction, network analysis improvements.",
        "priority": "low",
    },
    {
        "type": "activity",
        "title": "Risk Level Changed",
        "message": "Your burnout risk has been elevated to ELEVATED. Consider taking a break.",
        "priority": "high",
    },
    {
        "type": "team",
        "title": "New Team Member",
        "message": "Welcome Emma Thompson to the Engineering team!",
        "priority": "normal",
    },
    {
        "type": "security",
        "title": "Password Changed",
        "message": "Your password was successfully changed. If you didn't make this change, contact support.",
        "priority": "high",
    },
    {
        "type": "activity",
        "title": "Sprint Retrospective",
        "message": "Sprint 12 retrospective is scheduled for tomorrow at 2 PM.",
        "priority": "normal",
    },
]


def seed_demo():
    log.info("=" * 60)
    log.info("  SENTINEL — Master Demo Seed Script")
    log.info("=" * 60)

    db = SessionLocal()

    try:
        # 1. Create organization tenant
        existing_tenant = db.query(Tenant).filter_by(slug=ORG_SLUG).first()
        if existing_tenant:
            log.info(f"  [EXISTS] Tenant '{ORG_NAME}'")
            tenant = existing_tenant
        else:
            tenant = Tenant(
                name=ORG_NAME,
                slug=ORG_SLUG,
                plan="enterprise",
                status="active",
                settings={
                    "timezone": "America/New_York",
                    "features": {"sso": True, "mfa": True, "audit_log": True},
                },
            )
            db.add(tenant)
            db.flush()
            log.info(f"  [CREATE] Tenant '{ORG_NAME}' (enterprise)")

        # 2. Create users
        user_hashes = {}
        for u in DEMO_USERS:
            user_hash = privacy.hash_identity(u["email"])
            user_hashes[u["email"]] = user_hash

            existing = db.query(UserIdentity).filter_by(user_hash=user_hash).first()
            if not existing:
                # Create Supabase Auth user
                try:
                    admin_client = get_supabase_admin_client()
                    admin_client.auth.admin.create_user(
                        {
                            "email": u["email"],
                            "password": "Demo123!",
                            "email_confirm": True,
                            "user_metadata": {
                                "role": u["role"],
                                "name": u.get("name", ""),
                            },
                        }
                    )
                    log.info(f"    [AUTH] Supabase user created: {u['email']}")
                except Exception as e:
                    # User may already exist in Supabase
                    log.info(
                        f"    [AUTH] Supabase user may already exist: {u['email']} ({e})"
                    )

                manager_hash = user_hashes.get(u["manager"]) if u["manager"] else None
                user = UserIdentity(
                    user_hash=user_hash,
                    email_encrypted=privacy.encrypt(u["email"]),
                    role=u["role"],
                    manager_hash=manager_hash,
                    consent_share_with_manager=(u["role"] != "admin"),
                    consent_share_anonymized=True,
                )
                db.add(user)
                log.info(f"  [CREATE] User {u['name']} ({u['email']}) — {u['role']}")
            else:
                log.info(f"  [EXISTS] User {u['name']}")

            # Add to tenant
            existing_member = (
                db.query(TenantMember)
                .filter_by(tenant_id=tenant.id, user_hash=user_hash)
                .first()
            )
            if not existing_member:
                tenant_role = "owner" if u["role"] == "admin" else "member"
                db.add(
                    TenantMember(
                        tenant_id=tenant.id, user_hash=user_hash, role=tenant_role
                    )
                )

            # Create notification preferences
            existing_prefs = (
                db.query(NotificationPreference).filter_by(user_hash=user_hash).first()
            )
            if not existing_prefs:
                for channel in ["in_app", "email"]:
                    for ntype in ["auth", "team", "system", "security", "activity"]:
                        db.add(
                            NotificationPreference(
                                user_hash=user_hash,
                                channel=channel,
                                notification_type=ntype,
                                enabled=True,
                            )
                        )

        db.flush()

        # 3. Create analytics data for each user
        now = datetime.utcnow()
        for u in DEMO_USERS:
            user_hash = user_hashes[u["email"]]

            # Risk scores
            risk_level = random.choice(RISK_LEVELS)
            if u["role"] == "admin":
                risk_level = random.choice(["LOW", "ELEVATED"])

            existing_risk = db.query(RiskScore).filter_by(user_hash=user_hash).first()
            if not existing_risk:
                db.add(
                    RiskScore(
                        user_hash=user_hash,
                        tenant_id=tenant.id,
                        velocity=random.uniform(10, 80),
                        risk_level=risk_level,
                        confidence=random.uniform(0.6, 0.95),
                        thwarted_belongingness=random.uniform(10, 70),
                        updated_at=now,
                    )
                )

            # Risk history (last 30 days)
            existing_history = (
                db.query(RiskHistory).filter_by(user_hash=user_hash).first()
            )
            if not existing_history:
                for day in range(30, 0, -1):
                    db.add(
                        RiskHistory(
                            user_hash=user_hash,
                            tenant_id=tenant.id,
                            risk_level=random.choice(RISK_LEVELS),
                            velocity=random.uniform(10, 80),
                            confidence=random.uniform(0.5, 0.95),
                            belongingness_score=random.uniform(20, 80),
                            timestamp=now - timedelta(days=day),
                        )
                    )

            # Skill profiles
            existing_skills = (
                db.query(SkillProfile).filter_by(user_hash=user_hash).first()
            )
            if not existing_skills:
                db.add(
                    SkillProfile(
                        user_hash=user_hash,
                        tenant_id=tenant.id,
                        technical=random.uniform(40, 95)
                        if u["role"] in ["employee", "manager"]
                        else random.uniform(60, 95),
                        communication=random.uniform(30, 90),
                        leadership=random.uniform(20, 95)
                        if u["role"] in ["admin", "manager"]
                        else random.uniform(20, 60),
                        collaboration=random.uniform(40, 95),
                        adaptability=random.uniform(35, 90),
                        creativity=random.uniform(30, 90),
                    )
                )

            # Centrality scores
            existing_centrality = (
                db.query(CentralityScore).filter_by(user_hash=user_hash).first()
            )
            if not existing_centrality:
                db.add(
                    CentralityScore(
                        user_hash=user_hash,
                        tenant_id=tenant.id,
                        betweenness=random.uniform(0, 1),
                        eigenvector=random.uniform(0, 1),
                        unblocking_count=random.randint(0, 20),
                        knowledge_transfer_score=random.uniform(0, 100),
                        calculated_at=now,
                    )
                )

            # Behavioral events (last 14 days)
            for day in range(14, 0, -1):
                for _ in range(random.randint(2, 8)):
                    target = random.choice(list(user_hashes.values()))
                    db.add(
                        Event(
                            user_hash=user_hash,
                            tenant_id=tenant.id,
                            timestamp=now
                            - timedelta(days=day, hours=random.randint(0, 23)),
                            event_type=random.choice(EVENT_TYPES),
                            target_user_hash=target if random.random() > 0.5 else None,
                            metadata={"source": "demo_seed"},
                        )
                    )

        # 4. Create graph edges (team interactions)
        emails = list(user_hashes.keys())
        for _ in range(30):
            source = user_hashes[random.choice(emails)]
            target = user_hashes[random.choice(emails)]
            if source != target:
                db.add(
                    GraphEdge(
                        source_hash=source,
                        target_hash=target,
                        tenant_id=tenant.id,
                        weight=random.uniform(0.1, 1.0),
                        last_interaction=now - timedelta(days=random.randint(0, 14)),
                        edge_type=random.choice(
                            ["collaboration", "mentorship", "blocking"]
                        ),
                    )
                )

        # 5. Create notifications for demo users
        for u in DEMO_USERS[:5]:  # Notifications for first 5 users
            user_hash = user_hashes[u["email"]]
            for i, notif in enumerate(DEMO_NOTIFICATIONS):
                existing_notif = (
                    db.query(Notification)
                    .filter_by(user_hash=user_hash, title=notif["title"])
                    .first()
                )
                if not existing_notif:
                    db.add(
                        Notification(
                            user_hash=user_hash,
                            tenant_id=tenant.id,
                            type=notif["type"],
                            title=notif["title"],
                            message=notif["message"],
                            priority=notif["priority"],
                            read_at=(now - timedelta(hours=random.randint(1, 48)))
                            if random.random() > 0.4
                            else None,
                            created_at=now - timedelta(hours=random.randint(1, 72)),
                        )
                    )

        # 6. Create audit log entries
        for u in DEMO_USERS:
            user_hash = user_hashes[u["email"]]
            actions = [
                ("auth:login", {"method": "email", "ip": "192.168.1.100"}),
                ("auth:login", {"method": "google_sso", "ip": "10.0.0.50"}),
                ("user:profile_updated", {"fields": ["name", "avatar"]}),
                ("team:member_added", {"team": "Engineering"}),
                ("data:export", {"format": "csv", "records": 150}),
            ]
            for action, details in random.sample(actions, k=random.randint(2, 4)):
                db.add(
                    AuditLog(
                        user_hash=user_hash,
                        action=action,
                        details=details,
                        timestamp=now
                        - timedelta(
                            days=random.randint(0, 30), hours=random.randint(0, 23)
                        ),
                    )
                )

        db.commit()
        log.info("\n" + "=" * 60)
        log.info("  DEMO SEED COMPLETE")
        log.info(f"  Organization: {ORG_NAME}")
        log.info(f"  Users: {len(DEMO_USERS)}")
        log.info(f"  Tenant: {tenant.name} ({tenant.plan})")
        log.info("=" * 60)
        log.info("\n  Demo Credentials:")
        for u in DEMO_USERS:
            log.info(f"    {u['role']:>10} | {u['email']:>25} | password: Demo123!")
        log.info("=" * 60)

    except Exception as e:
        log.error(f"\n Seed failed: {e}")
        db.rollback()
        import traceback

        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    seed_demo()
