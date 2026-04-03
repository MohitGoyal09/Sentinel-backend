"""
Fresh Demo Seed — Phase 1-6 compatible.

Wipes all existing data and creates a complete, realistic demo environment
with proper Team model, TenantMember roles, and standardized audit logs.

Usage:
    cd backend && python -m scripts.seed_fresh

Demo login:
    All users have password: Demo123!
"""

import sys
import os
import logging
import random
from datetime import datetime, timedelta
from uuid import uuid4

sys.path.insert(0, os.getcwd())

from app.core.database import SessionLocal, engine, get_supabase_admin_client
from app.core.security import privacy
from app.models.identity import Base as IdentityBase, UserIdentity, AuditLog
from app.models.analytics import (
    Base as AnalyticsBase,
    Event,
    RiskScore,
    RiskHistory,
    GraphEdge,
    CentralityScore,
    SkillProfile,
)
from app.models.tenant import Tenant, TenantMember
from app.models.team import Team
from app.models.notification import (
    Base as NotificationBase,
    Notification,
    NotificationPreference,
)
from app.models.chat_history import ChatHistory
from app.models.invitation import Invitation
from app.services.audit_service import AuditAction

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("seed_fresh")

# ═══════════════════════════════════════════════════════════════════════════
# Demo Organization
# ═══════════════════════════════════════════════════════════════════════════

ORG_NAME = "Acme Technologies"
ORG_SLUG = "acme-technologies"
PASSWORD = "Demo123!"

# ═══════════════════════════════════════════════════════════════════════════
# Teams
# ═══════════════════════════════════════════════════════════════════════════

TEAMS = [
    {"name": "Engineering", "manager_email": "eng.manager@acme.com"},
    {"name": "Design", "manager_email": "design.manager@acme.com"},
    {"name": "Data Science", "manager_email": "data.lead@acme.com"},
]

# ═══════════════════════════════════════════════════════════════════════════
# Demo Users — role is for TenantMember, NOT UserIdentity
# ═══════════════════════════════════════════════════════════════════════════

DEMO_USERS = [
    # Admins
    {"email": "admin@acme.com",          "name": "Sarah Chen",      "role": "admin",    "team": None},
    {"email": "cto@acme.com",            "name": "James Wilson",    "role": "admin",    "team": None},
    # Managers
    {"email": "eng.manager@acme.com",    "name": "Priya Sharma",    "role": "manager",  "team": "Engineering"},
    {"email": "design.manager@acme.com", "name": "Alex Rivera",     "role": "manager",  "team": "Design"},
    {"email": "data.lead@acme.com",      "name": "Chen Wei",        "role": "manager",  "team": "Data Science"},
    # Engineering team
    {"email": "dev1@acme.com",           "name": "Jordan Lee",      "role": "employee", "team": "Engineering"},
    {"email": "dev2@acme.com",           "name": "Maria Santos",    "role": "employee", "team": "Engineering"},
    {"email": "dev3@acme.com",           "name": "David Kim",       "role": "employee", "team": "Engineering"},
    {"email": "dev4@acme.com",           "name": "Emma Thompson",   "role": "employee", "team": "Engineering"},
    # Design team
    {"email": "designer1@acme.com",      "name": "Noah Patel",      "role": "employee", "team": "Design"},
    {"email": "designer2@acme.com",      "name": "Olivia Zhang",    "role": "employee", "team": "Design"},
    # Data Science team
    {"email": "analyst1@acme.com",       "name": "Liam Carter",     "role": "employee", "team": "Data Science"},
    {"email": "analyst2@acme.com",       "name": "Sofia Martinez",  "role": "employee", "team": "Data Science"},
]

# ═══════════════════════════════════════════════════════════════════════════
# Risk profiles — deterministic for a good demo (not purely random)
# ═══════════════════════════════════════════════════════════════════════════

RISK_PROFILES = {
    "admin@acme.com":          {"risk": "LOW",      "velocity": 25.0, "belonging": 20.0, "conf": 0.92},
    "cto@acme.com":            {"risk": "LOW",      "velocity": 35.0, "belonging": 15.0, "conf": 0.88},
    "eng.manager@acme.com":    {"risk": "ELEVATED", "velocity": 55.0, "belonging": 35.0, "conf": 0.85},
    "design.manager@acme.com": {"risk": "LOW",      "velocity": 30.0, "belonging": 22.0, "conf": 0.90},
    "data.lead@acme.com":      {"risk": "LOW",      "velocity": 28.0, "belonging": 18.0, "conf": 0.87},
    "dev1@acme.com":           {"risk": "CRITICAL", "velocity": 78.0, "belonging": 65.0, "conf": 0.91},
    "dev2@acme.com":           {"risk": "LOW",      "velocity": 22.0, "belonging": 25.0, "conf": 0.89},
    "dev3@acme.com":           {"risk": "ELEVATED", "velocity": 60.0, "belonging": 50.0, "conf": 0.82},
    "dev4@acme.com":           {"risk": "LOW",      "velocity": 18.0, "belonging": 20.0, "conf": 0.94},
    "designer1@acme.com":      {"risk": "LOW",      "velocity": 20.0, "belonging": 15.0, "conf": 0.88},
    "designer2@acme.com":      {"risk": "ELEVATED", "velocity": 52.0, "belonging": 45.0, "conf": 0.80},
    "analyst1@acme.com":       {"risk": "LOW",      "velocity": 30.0, "belonging": 20.0, "conf": 0.86},
    "analyst2@acme.com":       {"risk": "LOW",      "velocity": 25.0, "belonging": 18.0, "conf": 0.90},
}

EVENT_TYPES = ["commit", "pr_review", "slack_message", "unblocked", "standup", "code_review", "meeting"]

NOTIFICATIONS = [
    {"type": "auth",     "title": "Welcome to Sentinel!",         "message": "Your account has been set up. Explore your wellbeing dashboard.",       "priority": "normal"},
    {"type": "team",     "title": "Team Standup Reminder",         "message": "Daily standup in 15 minutes. Don't forget to share your updates.",     "priority": "normal"},
    {"type": "security", "title": "New Login Detected",            "message": "A new login was detected from Chrome on macOS.",                       "priority": "high"},
    {"type": "system",   "title": "System Update",                 "message": "Sentinel v2.1 deployed with AI-powered burnout prediction.",           "priority": "low"},
    {"type": "activity", "title": "Risk Level Changed",            "message": "Your burnout risk has been updated. Review your dashboard.",            "priority": "high"},
    {"type": "team",     "title": "New Team Member",               "message": "A new colleague has joined your team!",                                "priority": "normal"},
    {"type": "activity", "title": "Weekly Wellbeing Summary",      "message": "Your weekly wellbeing summary is ready. Check your trends.",           "priority": "normal"},
    {"type": "system",   "title": "Scheduled Maintenance",         "message": "Sentinel will undergo maintenance this Saturday 2-4 AM UTC.",          "priority": "low"},
]


def _clear_supabase_users():
    """Delete all demo users from Supabase Auth."""
    try:
        admin = get_supabase_admin_client()
        users_resp = admin.auth.admin.list_users()
        users = users_resp if isinstance(users_resp, list) else (users_resp.users if hasattr(users_resp, 'users') else [])
        demo_emails = {u["email"] for u in DEMO_USERS}
        for su in users:
            email = getattr(su, 'email', None) or (su.get('email') if isinstance(su, dict) else None)
            if email and email in demo_emails:
                uid = getattr(su, 'id', None) or (su.get('id') if isinstance(su, dict) else None)
                if uid:
                    admin.auth.admin.delete_user(str(uid))
                    log.info(f"    [AUTH] Deleted Supabase user: {email}")
    except Exception as e:
        log.warning(f"    [AUTH] Could not clear Supabase users: {e}")


def _create_supabase_user(email: str, name: str, role: str):
    """Create a Supabase Auth user for the demo."""
    try:
        admin = get_supabase_admin_client()
        admin.auth.admin.create_user({
            "email": email,
            "password": PASSWORD,
            "email_confirm": True,
            "user_metadata": {"full_name": name, "role": role},
        })
        log.info(f"    [AUTH] Created: {email}")
    except Exception as e:
        log.info(f"    [AUTH] Exists or error: {email} ({type(e).__name__})")


def seed():
    log.info("=" * 60)
    log.info("  SENTINEL — Fresh Demo Seed (Phase 1-6 compatible)")
    log.info("=" * 60)

    db = SessionLocal()
    now = datetime.utcnow()

    try:
        # ── Step 0: Ensure all tables exist, then clear data ──────────
        log.info("\n[RESET] Ensuring tables exist...")
        IdentityBase.metadata.create_all(engine, checkfirst=True)
        AnalyticsBase.metadata.create_all(engine, checkfirst=True)
        NotificationBase.metadata.create_all(engine, checkfirst=True)

        log.info("[RESET] Clearing existing data...")
        for table in [
            ChatHistory, Invitation, Notification, NotificationPreference,
            GraphEdge, Event, RiskHistory, SkillProfile, CentralityScore, RiskScore,
            AuditLog, TenantMember, Team, UserIdentity, Tenant,
        ]:
            try:
                count = db.query(table).delete()
                if count:
                    log.info(f"    Cleared {count} rows from {table.__tablename__}")
                db.flush()
            except Exception:
                db.rollback()
                log.info(f"    Skipped {table.__tablename__} (may not exist)")
        db.commit()

        log.info("\n[AUTH] Clearing Supabase Auth users...")
        _clear_supabase_users()

        # ── Step 1: Create Tenant ──────────────────────────────────────
        log.info("\n[1/8] Creating tenant...")
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
        log.info(f"    Tenant: {ORG_NAME} ({tenant.id})")

        # ── Step 2: Create Teams ───────────────────────────────────────
        log.info("\n[2/8] Creating teams...")
        team_map = {}  # name -> Team
        for t in TEAMS:
            manager_hash = privacy.hash_identity(t["manager_email"])
            team = Team(
                tenant_id=tenant.id,
                name=t["name"],
                manager_hash=manager_hash,
            )
            db.add(team)
            db.flush()
            team_map[t["name"]] = team
            log.info(f"    Team: {t['name']} (manager: {t['manager_email']})")

        # ── Step 3: Create Users + TenantMembers ───────────────────────
        log.info("\n[3/8] Creating users and memberships...")
        user_hashes = {}  # email -> user_hash

        for u in DEMO_USERS:
            user_hash = privacy.hash_identity(u["email"])
            user_hashes[u["email"]] = user_hash

            # Supabase Auth user
            _create_supabase_user(u["email"], u["name"], u["role"])

            # UserIdentity (no role column — Phase 1 dropped it)
            user = UserIdentity(
                user_hash=user_hash,
                tenant_id=tenant.id,
                email_encrypted=privacy.encrypt(u["email"]),
                consent_share_with_manager=(u["role"] != "admin"),
                consent_share_anonymized=True,
                is_active=True,
            )
            db.add(user)

            # TenantMember (canonical role source)
            team_id = team_map[u["team"]].id if u["team"] else None
            member = TenantMember(
                tenant_id=tenant.id,
                user_hash=user_hash,
                role=u["role"],  # admin/manager/employee
                team_id=team_id,
            )
            db.add(member)
            log.info(f"    {u['role']:>10} | {u['name']:<20} | {u['email']} | team={u['team'] or '-'}")

        db.flush()

        # ── Step 4: Risk Scores + History ──────────────────────────────
        log.info("\n[4/8] Creating risk scores and history...")
        for u in DEMO_USERS:
            uh = user_hashes[u["email"]]
            profile = RISK_PROFILES[u["email"]]

            db.add(RiskScore(
                user_hash=uh,
                tenant_id=tenant.id,
                velocity=profile["velocity"],
                risk_level=profile["risk"],
                confidence=profile["conf"],
                thwarted_belongingness=profile["belonging"],
                updated_at=now,
            ))

            # 30-day history with realistic trend
            base_vel = profile["velocity"]
            for day in range(30, 0, -1):
                drift = random.uniform(-8, 8)
                vel = max(5, min(95, base_vel + drift))
                risk = "CRITICAL" if vel > 70 else ("ELEVATED" if vel > 45 else "LOW")
                db.add(RiskHistory(
                    user_hash=uh,
                    tenant_id=tenant.id,
                    risk_level=risk,
                    velocity=vel,
                    confidence=profile["conf"] + random.uniform(-0.05, 0.05),
                    belongingness_score=profile["belonging"] + random.uniform(-5, 5),
                    timestamp=now - timedelta(days=day),
                ))

        log.info(f"    {len(DEMO_USERS)} risk scores + {len(DEMO_USERS) * 30} history entries")

        # ── Step 5: Skill Profiles + Centrality ────────────────────────
        log.info("\n[5/8] Creating skill profiles and network scores...")
        for u in DEMO_USERS:
            uh = user_hashes[u["email"]]
            is_lead = u["role"] in ("admin", "manager")

            db.add(SkillProfile(
                user_hash=uh,
                tenant_id=tenant.id,
                technical=random.uniform(65, 95) if u["role"] != "admin" else random.uniform(50, 75),
                communication=random.uniform(50, 90),
                leadership=random.uniform(60, 95) if is_lead else random.uniform(25, 60),
                collaboration=random.uniform(55, 95),
                adaptability=random.uniform(45, 90),
                creativity=random.uniform(40, 90),
            ))

            db.add(CentralityScore(
                user_hash=uh,
                tenant_id=tenant.id,
                betweenness=random.uniform(0.1, 0.9) if is_lead else random.uniform(0.0, 0.5),
                eigenvector=random.uniform(0.2, 0.95),
                unblocking_count=random.randint(5, 25) if is_lead else random.randint(0, 12),
                knowledge_transfer_score=random.uniform(30, 95),
                calculated_at=now,
            ))

        log.info(f"    {len(DEMO_USERS)} skill profiles + {len(DEMO_USERS)} centrality scores")

        # ── Step 6: Events + Graph Edges ───────────────────────────────
        log.info("\n[6/8] Creating behavioral events and graph edges...")
        event_count = 0
        all_hashes = list(user_hashes.values())
        for u in DEMO_USERS:
            uh = user_hashes[u["email"]]
            events_per_day = 5 if u["role"] == "employee" else 3
            for day in range(14, 0, -1):
                for _ in range(random.randint(2, events_per_day + 3)):
                    target = random.choice(all_hashes)
                    db.add(Event(
                        user_hash=uh,
                        tenant_id=tenant.id,
                        timestamp=now - timedelta(days=day, hours=random.randint(8, 20), minutes=random.randint(0, 59)),
                        event_type=random.choice(EVENT_TYPES),
                        target_user_hash=target if target != uh and random.random() > 0.3 else None,
                        metadata_={"source": "demo_seed"},
                    ))
                    event_count += 1

        # Graph edges — realistic team interactions
        edge_count = 0
        for team_name, team_obj in team_map.items():
            team_members = [e for e in DEMO_USERS if e["team"] == team_name]
            for i, m1 in enumerate(team_members):
                for m2 in team_members[i + 1:]:
                    h1, h2 = user_hashes[m1["email"]], user_hashes[m2["email"]]
                    w = random.uniform(0.3, 1.0)
                    for src, tgt in [(h1, h2), (h2, h1)]:
                        db.add(GraphEdge(
                            source_hash=src, target_hash=tgt,
                            tenant_id=tenant.id,
                            weight=w + random.uniform(-0.1, 0.1),
                            last_interaction=now - timedelta(days=random.randint(0, 7)),
                            edge_type=random.choice(["collaboration", "mentorship", "code_review"]),
                        ))
                        edge_count += 1

        # Cross-team edges (fewer, weaker)
        for _ in range(15):
            s, t = random.sample(all_hashes, 2)
            db.add(GraphEdge(
                source_hash=s, target_hash=t,
                tenant_id=tenant.id,
                weight=random.uniform(0.1, 0.4),
                last_interaction=now - timedelta(days=random.randint(0, 14)),
                edge_type="collaboration",
            ))
            edge_count += 1

        log.info(f"    {event_count} events + {edge_count} graph edges")

        # ── Step 7: Notifications ──────────────────────────────────────
        log.info("\n[7/8] Creating notifications...")
        notif_count = 0
        for u in DEMO_USERS:
            uh = user_hashes[u["email"]]
            for notif in random.sample(NOTIFICATIONS, k=random.randint(3, len(NOTIFICATIONS))):
                db.add(Notification(
                    user_hash=uh,
                    tenant_id=tenant.id,
                    type=notif["type"],
                    title=notif["title"],
                    message=notif["message"],
                    priority=notif["priority"],
                    read_at=(now - timedelta(hours=random.randint(1, 48))) if random.random() > 0.4 else None,
                    created_at=now - timedelta(hours=random.randint(1, 168)),
                ))
                notif_count += 1

            # Notification preferences
            for channel in ("in_app", "email"):
                for ntype in ("auth", "team", "system", "security", "activity"):
                    db.add(NotificationPreference(
                        user_hash=uh, channel=channel, notification_type=ntype, enabled=True,
                    ))

        log.info(f"    {notif_count} notifications + {len(DEMO_USERS) * 10} preferences")

        # ── Step 8: Audit Logs (standardized Phase 6 format) ──────────
        log.info("\n[8/8] Creating audit logs...")
        admin_hash = user_hashes["admin@acme.com"]
        audit_count = 0

        # Admin actions
        for u in DEMO_USERS:
            uh = user_hashes[u["email"]]
            db.add(AuditLog(
                tenant_id=tenant.id,
                actor_hash=admin_hash,
                actor_role="admin",
                user_hash=uh,
                action=AuditAction.USER_INVITED,
                details={"email_domain": "acme.com", "assigned_role": u["role"]},
                timestamp=now - timedelta(days=30, hours=random.randint(0, 23)),
            ))
            audit_count += 1

        # Login events
        for u in DEMO_USERS:
            uh = user_hashes[u["email"]]
            for d in random.sample(range(14), k=random.randint(3, 7)):
                db.add(AuditLog(
                    tenant_id=tenant.id,
                    actor_hash=uh,
                    actor_role=u["role"],
                    action="auth:login",
                    details={"method": random.choice(["email", "google_sso"]), "ip": "192.168.1.100"},
                    timestamp=now - timedelta(days=d, hours=random.randint(8, 18)),
                ))
                audit_count += 1

        # Role change example
        db.add(AuditLog(
            tenant_id=tenant.id,
            actor_hash=admin_hash,
            actor_role="admin",
            user_hash=user_hashes["data.lead@acme.com"],
            action=AuditAction.ROLE_CHANGED,
            details={"old_role": "employee", "new_role": "manager"},
            timestamp=now - timedelta(days=15),
        ))

        # Data export example
        db.add(AuditLog(
            tenant_id=tenant.id,
            actor_hash=admin_hash,
            actor_role="admin",
            action=AuditAction.DATA_EXPORTED,
            details={"format": "csv", "records": 150},
            timestamp=now - timedelta(days=5),
        ))

        # Consent change examples
        for email in ["dev1@acme.com", "dev3@acme.com"]:
            db.add(AuditLog(
                tenant_id=tenant.id,
                actor_hash=user_hashes[email],
                actor_role="employee",
                action=AuditAction.CONSENT_CHANGED,
                details={"consent_share_with_manager": True},
                timestamp=now - timedelta(days=random.randint(1, 20)),
            ))

        audit_count += 4
        log.info(f"    {audit_count} audit log entries")

        # ── Commit everything ─────────────────────────────────────────
        db.commit()

        log.info("\n" + "=" * 60)
        log.info("  SEED COMPLETE")
        log.info(f"  Organization: {ORG_NAME} (enterprise)")
        log.info(f"  Teams: {', '.join(team_map.keys())}")
        log.info(f"  Users: {len(DEMO_USERS)}")
        log.info("=" * 60)
        log.info("\n  Demo Credentials (all use password: Demo123!)")
        log.info("-" * 60)
        for u in DEMO_USERS:
            log.info(f"    {u['role']:>10} | {u['email']:>28} | {u['name']}")
        log.info("=" * 60)

    except Exception as e:
        db.rollback()
        log.error(f"\n  SEED FAILED: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    seed()
