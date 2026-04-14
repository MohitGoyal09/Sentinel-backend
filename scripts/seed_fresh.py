"""
Fresh Demo Seed — Persona-driven, deterministic data.

Wipes all existing data and creates a complete, realistic demo environment
with persona-driven narratives, rich event metadata, deterministic skill/centrality
profiles, trending risk history, graph edges with blocking type, audit timeline,
and pre-seeded chat sessions.

All randomness uses a seeded Random(42) instance for fully deterministic output.

Usage:
    cd backend && python -m scripts.seed_fresh

Demo login:
    All users have password: Demo123!
"""

import sys
import os
import logging
import random as _random
from datetime import datetime, timedelta
from uuid import uuid4

# Production safety guard
_env = os.getenv("ENVIRONMENT", "development")
if _env == "production":
    print("ERROR: Refusing to run seed script in production environment.")
    print("Set ENVIRONMENT to 'development' or 'staging' to proceed.")
    sys.exit(1)

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
from app.models.chat_history import ChatSession, ChatHistory
from app.models.invitation import Invitation
from app.services.audit_service import AuditAction
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("seed_fresh")

# Deterministic RNG — same seed produces identical data every run
rng = _random.Random(42)

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
    {"name": "Sales", "manager_email": "eng.manager@acme.com"},
    {"name": "People Ops", "manager_email": "admin@acme.com"},
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
    # Non-tech personas
    {"email": "sales1@acme.com",         "name": "Ryan Mitchell",   "role": "employee", "team": "Sales"},
    {"email": "hr1@acme.com",            "name": "Aisha Patel",     "role": "employee", "team": "People Ops"},
]

# ═══════════════════════════════════════════════════════════════════════════
# Risk profiles — deterministic for a good demo (not random)
# ═══════════════════════════════════════════════════════════════════════════

RISK_PROFILES = {
    "admin@acme.com":          {"risk": "LOW",      "velocity": 0.8,  "belonging": 0.75, "conf": 0.92},
    "cto@acme.com":            {"risk": "LOW",      "velocity": 1.0,  "belonging": 0.80, "conf": 0.88},
    "eng.manager@acme.com":    {"risk": "ELEVATED", "velocity": 1.8,  "belonging": 0.45, "conf": 0.85},
    "design.manager@acme.com": {"risk": "LOW",      "velocity": 0.9,  "belonging": 0.70, "conf": 0.90},
    "data.lead@acme.com":      {"risk": "LOW",      "velocity": 0.7,  "belonging": 0.72, "conf": 0.87},
    "dev1@acme.com":           {"risk": "CRITICAL", "velocity": 3.2,  "belonging": 0.25, "conf": 0.91},
    "dev2@acme.com":           {"risk": "LOW",      "velocity": 0.6,  "belonging": 0.75, "conf": 0.89},
    "dev3@acme.com":           {"risk": "ELEVATED", "velocity": 2.0,  "belonging": 0.40, "conf": 0.82},
    "dev4@acme.com":           {"risk": "LOW",      "velocity": 0.5,  "belonging": 0.80, "conf": 0.94},
    "designer1@acme.com":      {"risk": "LOW",      "velocity": 0.6,  "belonging": 0.78, "conf": 0.88},
    "designer2@acme.com":      {"risk": "ELEVATED", "velocity": 1.7,  "belonging": 0.35, "conf": 0.80},
    "analyst1@acme.com":       {"risk": "LOW",      "velocity": 0.8,  "belonging": 0.72, "conf": 0.86},
    "analyst2@acme.com":       {"risk": "LOW",      "velocity": 0.7,  "belonging": 0.74, "conf": 0.90},
    "sales1@acme.com":         {"risk": "LOW",      "velocity": 0.6,  "belonging": 0.78, "conf": 0.88},
    "hr1@acme.com":            {"risk": "LOW",      "velocity": 0.5,  "belonging": 0.82, "conf": 0.91},
}

# ═══════════════════════════════════════════════════════════════════════════
# Deterministic Skill Profiles — curated per persona
# ═══════════════════════════════════════════════════════════════════════════

SKILL_PROFILES = {
    "admin@acme.com":          {"technical": 65, "communication": 88, "leadership": 92, "collaboration": 85, "adaptability": 80, "creativity": 70},
    "cto@acme.com":            {"technical": 82, "communication": 78, "leadership": 90, "collaboration": 72, "adaptability": 85, "creativity": 75},
    "eng.manager@acme.com":    {"technical": 75, "communication": 82, "leadership": 85, "collaboration": 78, "adaptability": 70, "creativity": 60},
    "design.manager@acme.com": {"technical": 55, "communication": 85, "leadership": 80, "collaboration": 88, "adaptability": 82, "creativity": 92},
    "data.lead@acme.com":      {"technical": 88, "communication": 65, "leadership": 70, "collaboration": 68, "adaptability": 75, "creativity": 72},
    "dev1@acme.com":           {"technical": 92, "communication": 35, "leadership": 30, "collaboration": 40, "adaptability": 55, "creativity": 45},
    "dev2@acme.com":           {"technical": 78, "communication": 72, "leadership": 45, "collaboration": 80, "adaptability": 75, "creativity": 65},
    "dev3@acme.com":           {"technical": 80, "communication": 68, "leadership": 40, "collaboration": 65, "adaptability": 60, "creativity": 55},
    "dev4@acme.com":           {"technical": 85, "communication": 70, "leadership": 55, "collaboration": 90, "adaptability": 78, "creativity": 68},
    "designer1@acme.com":      {"technical": 60, "communication": 75, "leadership": 35, "collaboration": 72, "adaptability": 70, "creativity": 88},
    "designer2@acme.com":      {"technical": 58, "communication": 45, "leadership": 30, "collaboration": 38, "adaptability": 65, "creativity": 85},
    "analyst1@acme.com":       {"technical": 82, "communication": 60, "leadership": 35, "collaboration": 65, "adaptability": 72, "creativity": 55},
    "analyst2@acme.com":       {"technical": 80, "communication": 68, "leadership": 38, "collaboration": 70, "adaptability": 75, "creativity": 60},
    "sales1@acme.com":         {"technical": 40, "communication": 90, "leadership": 50, "collaboration": 82, "adaptability": 78, "creativity": 65},
    "hr1@acme.com":            {"technical": 35, "communication": 88, "leadership": 60, "collaboration": 85, "adaptability": 80, "creativity": 58},
}

# ═══════════════════════════════════════════════════════════════════════════
# Deterministic Centrality Scores — curated per persona
# ═══════════════════════════════════════════════════════════════════════════

CENTRALITY_SCORES = {
    "admin@acme.com":          {"betweenness": 0.55, "eigenvector": 0.72, "unblocking_count": 8,  "knowledge_transfer_score": 65},
    "cto@acme.com":            {"betweenness": 0.45, "eigenvector": 0.80, "unblocking_count": 5,  "knowledge_transfer_score": 55},
    "eng.manager@acme.com":    {"betweenness": 0.60, "eigenvector": 0.68, "unblocking_count": 15, "knowledge_transfer_score": 72},
    "design.manager@acme.com": {"betweenness": 0.50, "eigenvector": 0.62, "unblocking_count": 10, "knowledge_transfer_score": 68},
    "data.lead@acme.com":      {"betweenness": 0.40, "eigenvector": 0.55, "unblocking_count": 7,  "knowledge_transfer_score": 60},
    "dev1@acme.com":           {"betweenness": 0.25, "eigenvector": 0.35, "unblocking_count": 4,  "knowledge_transfer_score": 40},
    "dev2@acme.com":           {"betweenness": 0.35, "eigenvector": 0.50, "unblocking_count": 8,  "knowledge_transfer_score": 55},
    "dev3@acme.com":           {"betweenness": 0.30, "eigenvector": 0.45, "unblocking_count": 6,  "knowledge_transfer_score": 48},
    "dev4@acme.com":           {"betweenness": 0.85, "eigenvector": 0.15, "unblocking_count": 22, "knowledge_transfer_score": 88},
    "designer1@acme.com":      {"betweenness": 0.20, "eigenvector": 0.40, "unblocking_count": 3,  "knowledge_transfer_score": 35},
    "designer2@acme.com":      {"betweenness": 0.15, "eigenvector": 0.25, "unblocking_count": 2,  "knowledge_transfer_score": 28},
    "analyst1@acme.com":       {"betweenness": 0.22, "eigenvector": 0.38, "unblocking_count": 4,  "knowledge_transfer_score": 42},
    "analyst2@acme.com":       {"betweenness": 0.18, "eigenvector": 0.35, "unblocking_count": 3,  "knowledge_transfer_score": 38},
    "sales1@acme.com":         {"betweenness": 0.12, "eigenvector": 0.30, "unblocking_count": 2,  "knowledge_transfer_score": 30},
    "hr1@acme.com":            {"betweenness": 0.28, "eigenvector": 0.42, "unblocking_count": 5,  "knowledge_transfer_score": 50},
}

# ═══════════════════════════════════════════════════════════════════════════
# Risk History Trends — deterministic patterns per persona
# ═══════════════════════════════════════════════════════════════════════════

def _risk_history_velocity(email: str, day: int) -> float:
    """Return deterministic velocity for a given user on day N (30=oldest, 1=newest).

    Velocity is on a 0-5 scale matching the Safety Valve engine output
    (scipy linregress slope). Thresholds: CRITICAL > 2.5, ELEVATED > 1.5.
    """
    profile = RISK_PROFILES[email]
    final = profile["velocity"]

    if email == "dev1@acme.com":
        # Jordan: velocity trending UP over 30 days (1.5 -> 3.2)
        return 1.5 + (final - 1.5) * ((30 - day) / 29.0)
    elif email == "dev3@acme.com":
        # David: velocity creeping UP (1.0 -> 2.0)
        return 1.0 + (final - 1.0) * ((30 - day) / 29.0)
    elif email == "designer2@acme.com":
        # Olivia: velocity stable around 1.7
        return final + rng.uniform(-0.15, 0.15)
    elif email == "dev2@acme.com":
        # Maria: velocity stable (0.4-0.8 range)
        return final + rng.uniform(-0.15, 0.15)
    else:
        # Others: stable within their range with minor drift
        return final + rng.uniform(-0.2, 0.2)


def _risk_history_belonging(email: str, day: int) -> float:
    """Return deterministic belongingness for a given user on day N.

    Belongingness is on a 0-1 scale matching the Safety Valve engine output.
    Lower values = more socially isolated / higher thwarted belongingness.
    """
    profile = RISK_PROFILES[email]
    final = profile["belonging"]

    if email == "dev1@acme.com":
        # Jordan: belongingness trending DOWN (0.55 -> 0.25, increasingly isolated)
        return 0.55 + (final - 0.55) * ((30 - day) / 29.0)
    elif email == "designer2@acme.com":
        # Olivia: belongingness trending DOWN (0.60 -> 0.35)
        start_belong = 0.60
        return start_belong + (final - start_belong) * ((30 - day) / 29.0)
    elif email == "dev2@acme.com":
        # Maria: stable belongingness
        return final + rng.uniform(-0.03, 0.03)
    else:
        return final + rng.uniform(-0.05, 0.05)


# ═══════════════════════════════════════════════════════════════════════════
# Persona Event Generators
# ═══════════════════════════════════════════════════════════════════════════

EVENT_TYPES = ["commit", "pr_review", "slack_message", "unblocked", "standup", "code_review", "meeting", "email_sent", "ticket_created"]


def _pick_hour_jordan() -> int:
    """Jordan (chaotic): 40% 20:00-23:00, 30% 14:00-20:00, 30% random."""
    r = rng.random()
    if r < 0.40:
        return rng.choice([20, 21, 22, 23])
    elif r < 0.70:
        return rng.randint(14, 20)
    else:
        return rng.randint(8, 23)


def _pick_hour_david() -> int:
    """David (creeping): 60% 09:00-18:00, 30% 18:00-22:00, 10% after 22:00."""
    r = rng.random()
    if r < 0.60:
        return rng.randint(9, 17)
    elif r < 0.90:
        return rng.randint(18, 21)
    else:
        return rng.randint(22, 23)


def _pick_hour_olivia() -> int:
    """Olivia (regular but sparse): 90% 09:00-17:00, 10% 17:00-18:00."""
    r = rng.random()
    if r < 0.90:
        return rng.randint(9, 16)
    else:
        return rng.choice([17, 18])


def _pick_hour_maria() -> int:
    """Maria (healthy): 95% 09:00-17:30, 5% 08:00-09:00."""
    r = rng.random()
    if r < 0.95:
        return rng.randint(9, 17)
    else:
        return 8


def _pick_hour_default() -> int:
    """Others: 85% 09:00-18:00, 15% 08:00-09:00 or 18:00-19:00."""
    r = rng.random()
    if r < 0.85:
        return rng.randint(9, 17)
    else:
        return rng.choice([8, 18, 19])


def _pick_hour_sales() -> int:
    """Ryan (sales): 80% 08:00-17:00, 20% 07:00-08:00 (early starts)."""
    r = rng.random()
    if r < 0.80:
        return rng.randint(8, 16)
    else:
        return 7


def _pick_hour_hr() -> int:
    """Aisha (HR): 90% 09:00-17:00, 10% 08:00."""
    r = rng.random()
    if r < 0.90:
        return rng.randint(9, 16)
    else:
        return 8


HOUR_PICKERS = {
    "dev1@acme.com": _pick_hour_jordan,
    "dev3@acme.com": _pick_hour_david,
    "designer2@acme.com": _pick_hour_olivia,
    "dev2@acme.com": _pick_hour_maria,
    "sales1@acme.com": _pick_hour_sales,
    "hr1@acme.com": _pick_hour_hr,
}


def _is_after_hours(hour: int) -> bool:
    return hour >= 18 or hour < 8


def _generate_event_metadata(
    email: str,
    event_type: str,
    hour: int,
    day: int,
) -> dict:
    """Generate rich event metadata per persona and event type."""
    after_hours = _is_after_hours(hour)

    # Context switches per persona
    if email == "dev1@acme.com":
        ctx = rng.randint(8, 12)
    elif email == "dev3@acme.com":
        ctx = rng.randint(5, 7)
    elif email == "designer2@acme.com":
        ctx = rng.randint(2, 4)
    elif email == "dev2@acme.com":
        ctx = rng.randint(3, 5)
    elif email == "dev4@acme.com":
        ctx = rng.randint(4, 6)
    else:
        ctx = rng.randint(2, 5)

    meta = {
        "source": "demo_seed",
        "after_hours": after_hours,
        "context_switches": ctx,
    }

    if event_type == "slack_message":
        if email == "dev1@acme.com":
            # Jordan: declining replies
            meta["is_reply"] = (day > 7)  # only replies in week 1
            meta["mentions_others"] = rng.random() < 0.2
            meta["channel"] = rng.choice(["engineering", "general"])
        elif email == "designer2@acme.com":
            meta["is_reply"] = rng.random() < 0.3
            meta["mentions_others"] = rng.random() < 0.1
            meta["channel"] = rng.choice(["design", "general"])
        elif email == "dev2@acme.com":
            meta["is_reply"] = True
            meta["mentions_others"] = rng.random() < 0.5
            meta["channel"] = rng.choice(["engineering", "general", "random"])
        elif email == "dev4@acme.com":
            meta["is_reply"] = rng.random() < 0.7
            meta["mentions_others"] = rng.random() < 0.6
            meta["channel"] = rng.choice(["engineering", "design", "general"])
        else:
            meta["is_reply"] = rng.random() < 0.6
            meta["mentions_others"] = rng.random() < 0.4
            meta["channel"] = rng.choice(["engineering", "design", "data-science", "general"])

    elif event_type == "commit":
        if email == "dev1@acme.com":
            meta["files_changed"] = rng.randint(5, 15)
        elif email == "dev4@acme.com":
            meta["files_changed"] = rng.randint(2, 5)
        else:
            meta["files_changed"] = rng.randint(1, 8)

    elif event_type == "pr_review" or event_type == "code_review":
        meta["comment_length"] = rng.randint(50, 500)
        meta["files_changed"] = rng.randint(1, 10)

    elif event_type == "email_sent":
        meta["recipient_count"] = rng.randint(1, 5)
        meta["has_attachment"] = rng.random() < 0.3
        meta["subject_length"] = rng.randint(20, 100)

    elif event_type == "ticket_created":
        meta["priority"] = rng.choice(["low", "medium", "high"])
        meta["category"] = rng.choice(["support", "feature_request", "bug_report", "internal"])

    elif event_type == "meeting":
        meta["duration_minutes"] = rng.choice([30, 45, 60, 90])
        meta["attendee_count"] = rng.randint(2, 8)

    return meta


def _generate_persona_events(
    email: str,
    user_hash: str,
    tenant_id,
    all_hashes: list,
    now: datetime,
) -> list:
    """Generate persona-specific events over 14 days."""
    events = []
    hour_picker = HOUR_PICKERS.get(email, _pick_hour_default)

    for day in range(14, 0, -1):
        is_weekend = (now - timedelta(days=day)).weekday() >= 5

        if email == "dev1@acme.com":
            # Jordan: 6-8 events/day, weekends too
            if is_weekend:
                n_events = rng.randint(3, 5)
            else:
                n_events = rng.randint(6, 8)
            # Week 1 vs week 2 patterns
            if day > 7:
                # Week 1: more PRs, some Slack
                type_weights = ["commit"] * 4 + ["pr_review"] * 3 + ["slack_message"] * 4 + ["standup"] * 1 + ["code_review"] * 1
            else:
                # Week 2: mostly commits, very few Slack
                type_weights = ["commit"] * 6 + ["pr_review"] * 1 + ["slack_message"] * 1 + ["code_review"] * 1

        elif email == "dev3@acme.com":
            # David: 4-5 events/day, no weekends
            if is_weekend:
                n_events = rng.randint(0, 1)
            else:
                n_events = rng.randint(4, 5)
            type_weights = ["commit"] * 3 + ["pr_review"] * 2 + ["slack_message"] * 3 + ["standup"] * 1 + ["code_review"] * 1

        elif email == "designer2@acme.com":
            # Olivia: 2-3 events/day, normal hours, declining Slack
            if is_weekend:
                n_events = 0
            else:
                n_events = rng.randint(2, 3)
            if day > 7:
                type_weights = ["commit"] * 2 + ["slack_message"] * 4 + ["meeting"] * 2 + ["standup"] * 1
            else:
                type_weights = ["commit"] * 3 + ["slack_message"] * 1 + ["meeting"] * 1

        elif email == "dev2@acme.com":
            # Maria: 3-4 events/day, 09:00-17:30, consistent
            if is_weekend:
                n_events = 0
            else:
                n_events = rng.randint(3, 4)
            type_weights = ["commit"] * 3 + ["pr_review"] * 2 + ["slack_message"] * 3 + ["standup"] * 1 + ["code_review"] * 1

        elif email == "dev4@acme.com":
            # Emma: 2-3 commits but 4-5 PR reviews across teams
            if is_weekend:
                n_events = 0
            else:
                n_events = rng.randint(6, 8)
            type_weights = ["commit"] * 2 + ["pr_review"] * 4 + ["unblocked"] * 3 + ["slack_message"] * 2 + ["code_review"] * 2

        elif email == "eng.manager@acme.com":
            # Priya: overloaded with meetings
            if is_weekend:
                n_events = rng.randint(0, 1)
            else:
                n_events = rng.randint(4, 6)
            type_weights = ["meeting"] * 4 + ["slack_message"] * 3 + ["pr_review"] * 1 + ["standup"] * 1

        elif email == "sales1@acme.com":
            # Ryan: sales rep, email-heavy, some meetings, no commits
            if is_weekend:
                n_events = 0
            else:
                n_events = rng.randint(4, 7)
            type_weights = ["email_sent"] * 4 + ["slack_message"] * 3 + ["meeting"] * 2 + ["ticket_created"] * 1

        elif email == "hr1@acme.com":
            # Aisha: HR, meetings + slack + tickets, no commits
            if is_weekend:
                n_events = 0
            else:
                n_events = rng.randint(3, 5)
            type_weights = ["meeting"] * 3 + ["slack_message"] * 3 + ["email_sent"] * 2 + ["ticket_created"] * 2

        else:
            # Default: 2-4 events/day, normal hours
            if is_weekend:
                n_events = 0
            else:
                n_events = rng.randint(2, 4)
            type_weights = ["commit"] * 3 + ["pr_review"] * 1 + ["slack_message"] * 2 + ["standup"] * 1 + ["meeting"] * 1

        for _ in range(n_events):
            hour = hour_picker()
            minute = rng.randint(0, 59)
            event_type = rng.choice(type_weights)

            # Pick a target user (not self)
            other_hashes = [h for h in all_hashes if h != user_hash]
            target = rng.choice(other_hashes) if rng.random() > 0.3 else None

            metadata = _generate_event_metadata(email, event_type, hour, day)

            events.append(Event(
                user_hash=user_hash,
                tenant_id=tenant_id,
                timestamp=now - timedelta(days=day, hours=24 - hour, minutes=60 - minute),
                event_type=event_type,
                target_user_hash=target,
                metadata_=metadata,
            ))

    return events


# ═══════════════════════════════════════════════════════════════════════════
# Graph Edge Definitions
# ═══════════════════════════════════════════════════════════════════════════

def _generate_graph_edges(user_hashes: dict, team_map: dict, tenant_id, now: datetime) -> list:
    """Generate deterministic graph edges with blocking type for Emma."""
    edges = []

    # Intra-team edges: strong weight (0.6-1.0)
    team_weights = {
        ("Engineering", 0): 0.85,
        ("Engineering", 1): 0.78,
        ("Engineering", 2): 0.92,
        ("Engineering", 3): 0.70,
        ("Engineering", 4): 0.65,
        ("Engineering", 5): 0.88,
        ("Design", 0): 0.75,
        ("Design", 1): 0.68,
        ("Data Science", 0): 0.72,
        ("Data Science", 1): 0.80,
    }

    edge_idx = 0
    for team_name, team_obj in team_map.items():
        team_members = [e for e in DEMO_USERS if e["team"] == team_name]
        for i, m1 in enumerate(team_members):
            for j, m2 in enumerate(team_members):
                if i >= j:
                    continue
                h1 = user_hashes[m1["email"]]
                h2 = user_hashes[m2["email"]]
                w = team_weights.get((team_name, edge_idx % 10), 0.75)
                edge_idx += 1

                edge_type = rng.choice(["collaboration", "mentorship", "code_review"])
                days_ago = rng.randint(0, 7)

                for src, tgt in [(h1, h2), (h2, h1)]:
                    edges.append(GraphEdge(
                        source_hash=src,
                        target_hash=tgt,
                        tenant_id=tenant_id,
                        weight=w,
                        last_interaction=now - timedelta(days=days_ago),
                        edge_type=edge_type,
                    ))

    # Emma Thompson's blocking edges (hidden gem pattern)
    emma_hash = user_hashes["dev4@acme.com"]
    blocked_users = [
        "dev1@acme.com",       # Jordan
        "dev2@acme.com",       # Maria
        "dev3@acme.com",       # David
        "designer1@acme.com",  # Noah (cross-team!)
        "designer2@acme.com",  # Olivia (cross-team!)
        "analyst1@acme.com",   # Liam (cross-team!)
        "eng.manager@acme.com", # Priya
    ]
    blocking_weights = [0.72, 0.68, 0.65, 0.58, 0.55, 0.52, 0.60]

    for idx, blocked_email in enumerate(blocked_users):
        blocked_hash = user_hashes[blocked_email]
        edges.append(GraphEdge(
            source_hash=emma_hash,
            target_hash=blocked_hash,
            tenant_id=tenant_id,
            weight=blocking_weights[idx],
            last_interaction=now - timedelta(days=rng.randint(0, 5)),
            edge_type="blocking",
        ))

    # Emma's cross-team edges (Engineering <-> Design bridge)
    design_members = ["designer1@acme.com", "designer2@acme.com", "design.manager@acme.com"]
    for dm_email in design_members:
        dm_hash = user_hashes[dm_email]
        # Unusually strong for cross-team (0.5-0.7)
        w = rng.uniform(0.50, 0.70)
        edges.append(GraphEdge(
            source_hash=emma_hash,
            target_hash=dm_hash,
            tenant_id=tenant_id,
            weight=w,
            last_interaction=now - timedelta(days=rng.randint(0, 3)),
            edge_type="code_review",
        ))
        edges.append(GraphEdge(
            source_hash=dm_hash,
            target_hash=emma_hash,
            tenant_id=tenant_id,
            weight=w - 0.05,
            last_interaction=now - timedelta(days=rng.randint(0, 4)),
            edge_type="collaboration",
        ))

    # General cross-team edges (weak: 0.1-0.4)
    cross_team_pairs = [
        ("admin@acme.com", "eng.manager@acme.com"),
        ("admin@acme.com", "design.manager@acme.com"),
        ("admin@acme.com", "data.lead@acme.com"),
        ("cto@acme.com", "eng.manager@acme.com"),
        ("cto@acme.com", "design.manager@acme.com"),
        ("eng.manager@acme.com", "data.lead@acme.com"),
        ("dev1@acme.com", "analyst1@acme.com"),
        ("dev2@acme.com", "designer1@acme.com"),
        ("dev3@acme.com", "analyst2@acme.com"),
        ("analyst1@acme.com", "designer1@acme.com"),
        # Non-tech cross-team edges
        ("sales1@acme.com", "admin@acme.com"),
        ("sales1@acme.com", "eng.manager@acme.com"),
        ("hr1@acme.com", "admin@acme.com"),
        ("hr1@acme.com", "design.manager@acme.com"),
        ("hr1@acme.com", "sales1@acme.com"),
    ]
    cross_weights = [0.35, 0.32, 0.30, 0.38, 0.28, 0.25, 0.18, 0.22, 0.15, 0.12, 0.30, 0.20, 0.42, 0.25, 0.35]
    for idx, (e1, e2) in enumerate(cross_team_pairs):
        h1 = user_hashes[e1]
        h2 = user_hashes[e2]
        w = cross_weights[idx]
        edges.append(GraphEdge(
            source_hash=h1,
            target_hash=h2,
            tenant_id=tenant_id,
            weight=w,
            last_interaction=now - timedelta(days=rng.randint(0, 14)),
            edge_type="collaboration",
        ))

    return edges


# ═══════════════════════════════════════════════════════════════════════════
# Audit Log Timeline
# ═══════════════════════════════════════════════════════════════════════════

def _generate_audit_logs(
    user_hashes: dict,
    tenant_id,
    now: datetime,
) -> list:
    """Generate rich audit log timeline with 12 action types."""
    logs = []
    admin_hash = user_hashes["admin@acme.com"]
    system_hash = "system"

    # 1. user_invited — all 13 users (by admin, -30 days, staggered hours)
    for idx, u in enumerate(DEMO_USERS):
        uh = user_hashes[u["email"]]
        logs.append(AuditLog(
            tenant_id=tenant_id,
            actor_hash=admin_hash,
            actor_role="admin",
            user_hash=uh,
            action=AuditAction.USER_INVITED,
            details={"email_domain": "acme.com", "assigned_role": u["role"]},
            timestamp=now - timedelta(days=30, hours=idx * 2),
        ))

    # 2. invite_accepted — staggered -29 to -25 days
    accept_days = [29, 29, 28, 28, 28, 27, 27, 27, 27, 26, 26, 25, 25, 25, 24]
    for idx, u in enumerate(DEMO_USERS):
        uh = user_hashes[u["email"]]
        logs.append(AuditLog(
            tenant_id=tenant_id,
            actor_hash=uh,
            actor_role=u["role"],
            action="invite_accepted",
            details={"method": "email_link"},
            timestamp=now - timedelta(days=accept_days[idx], hours=10 + idx),
        ))

    # 3. auth:login — 3-7 logins per user, deterministic days
    login_schedules = {
        "admin@acme.com":          [1, 2, 3, 5, 7, 10, 13],
        "cto@acme.com":            [1, 3, 5, 8, 12],
        "eng.manager@acme.com":    [1, 2, 4, 6, 8, 11],
        "design.manager@acme.com": [1, 3, 6, 9, 13],
        "data.lead@acme.com":      [2, 4, 7, 10],
        "dev1@acme.com":           [1, 2, 3, 4, 5, 6, 7],
        "dev2@acme.com":           [1, 3, 5, 8, 11],
        "dev3@acme.com":           [1, 2, 4, 6, 9, 12],
        "dev4@acme.com":           [1, 3, 5, 7, 10],
        "designer1@acme.com":      [1, 4, 7, 11],
        "designer2@acme.com":      [2, 5, 9],
        "analyst1@acme.com":       [1, 3, 6, 10, 13],
        "analyst2@acme.com":       [2, 5, 8, 12],
        "sales1@acme.com":         [1, 2, 4, 7, 10],
        "hr1@acme.com":            [1, 3, 5, 8, 11, 13],
    }
    login_methods = ["email", "email", "email", "google_sso"]
    for u in DEMO_USERS:
        uh = user_hashes[u["email"]]
        days = login_schedules.get(u["email"], [1, 5, 10])
        for d_idx, d in enumerate(days):
            logs.append(AuditLog(
                tenant_id=tenant_id,
                actor_hash=uh,
                actor_role=u["role"],
                action="auth:login",
                details={
                    "method": login_methods[d_idx % len(login_methods)],
                    "ip": "192.168.1.100",
                },
                timestamp=now - timedelta(days=d, hours=9),
            ))

    # 4. role_changed — Chen Wei: employee -> manager (-15 days)
    logs.append(AuditLog(
        tenant_id=tenant_id,
        actor_hash=admin_hash,
        actor_role="admin",
        user_hash=user_hashes["data.lead@acme.com"],
        action=AuditAction.ROLE_CHANGED,
        details={"old_role": "employee", "new_role": "manager"},
        timestamp=now - timedelta(days=15),
    ))

    # 5. consent_changed — Jordan: true (-20 days)
    logs.append(AuditLog(
        tenant_id=tenant_id,
        actor_hash=user_hashes["dev1@acme.com"],
        actor_role="employee",
        action=AuditAction.CONSENT_CHANGED,
        details={"consent_share_with_manager": True},
        timestamp=now - timedelta(days=20),
    ))

    # 6. consent_changed — Olivia: false (-10 days)
    logs.append(AuditLog(
        tenant_id=tenant_id,
        actor_hash=user_hashes["designer2@acme.com"],
        actor_role="employee",
        action=AuditAction.CONSENT_CHANGED,
        details={"consent_share_with_manager": False},
        timestamp=now - timedelta(days=10),
    ))

    # 7. data_exported — admin, CSV export (-5 days)
    logs.append(AuditLog(
        tenant_id=tenant_id,
        actor_hash=admin_hash,
        actor_role="admin",
        action=AuditAction.DATA_EXPORTED,
        details={"format": "csv", "records": 150},
        timestamp=now - timedelta(days=5),
    ))

    # 8. engine_run — system, Safety Valve analysis (-1 day)
    logs.append(AuditLog(
        tenant_id=tenant_id,
        actor_hash=system_hash,
        actor_role="system",
        action=AuditAction.ENGINE_RECOMPUTED,
        details={"engine": "safety_valve", "users_analyzed": 13, "result": "completed"},
        timestamp=now - timedelta(days=1, hours=6),
    ))

    # 9. nudge_sent — system, burnout nudge to Jordan (-2 days)
    logs.append(AuditLog(
        tenant_id=tenant_id,
        actor_hash=system_hash,
        actor_role="system",
        user_hash=user_hashes["dev1@acme.com"],
        action="nudge_sent",
        details={"nudge_type": "burnout_warning", "recipient_risk_level": "CRITICAL"},
        timestamp=now - timedelta(days=2),
    ))

    # 10. identity_revealed — Priya, 36h critical override for Jordan (-3 days)
    logs.append(AuditLog(
        tenant_id=tenant_id,
        actor_hash=user_hashes["eng.manager@acme.com"],
        actor_role="manager",
        user_hash=user_hashes["dev1@acme.com"],
        action=AuditAction.IDENTITY_REVEALED,
        details={"reason": "critical_risk_override", "override_duration_hours": 36},
        timestamp=now - timedelta(days=3),
    ))

    # 11. tool_connected — admin, Gmail (-7 days)
    logs.append(AuditLog(
        tenant_id=tenant_id,
        actor_hash=admin_hash,
        actor_role="admin",
        action=AuditAction.TOOL_CONNECTED,
        details={"tool": "gmail", "status": "connected"},
        timestamp=now - timedelta(days=7),
    ))

    # 12. ask_sentinel_query — admin, "Who is at risk?" (-1 day)
    logs.append(AuditLog(
        tenant_id=tenant_id,
        actor_hash=admin_hash,
        actor_role="admin",
        action="ask_sentinel_query",
        details={"query": "Who is at risk?", "engine": "safety_valve"},
        timestamp=now - timedelta(days=1, hours=2),
    ))

    return logs


# ═══════════════════════════════════════════════════════════════════════════
# Shadow Deployment Validation Data
# ═══════════════════════════════════════════════════════════════════════════

SHADOW_DEPARTURES = [
    {
        "email": "dev1@acme.com",       # Jordan Lee — CRITICAL, correctly predicted
        "departure_date": "2026-04-10",
        "reason": "voluntary",
        "predicted_risk": "CRITICAL",
        "predicted_attrition_probability": 0.85,
        "correctly_predicted": True,
        "days_ago": 4,
    },
    {
        "email": "dev2@acme.com",       # Maria Santos — CRITICAL, correctly predicted
        "departure_date": "2026-04-12",
        "reason": "voluntary",
        "predicted_risk": "CRITICAL",
        "predicted_attrition_probability": 0.78,
        "correctly_predicted": True,
        "days_ago": 2,
    },
    {
        "email": "admin@acme.com",      # Sarah Chen — LOW, false negative (honest miss)
        "departure_date": "2026-04-08",
        "reason": "voluntary",
        "predicted_risk": "LOW",
        "predicted_attrition_probability": 0.08,
        "correctly_predicted": False,
        "days_ago": 6,
    },
]


def seed_shadow_departures(
    db,
    tenant_id,
    user_hashes: dict,
    now: datetime,
) -> list:
    """Create shadow departure audit log entries for demo validation.

    Produces 3 departures: 2 correctly predicted (Jordan, Maria),
    1 false negative (Sarah). Accuracy: 66.7% — honest, not perfect.
    """
    entries = []
    admin_hash = user_hashes["admin@acme.com"]

    for dep in SHADOW_DEPARTURES:
        target_hash = user_hashes[dep["email"]]
        entries.append(AuditLog(
            tenant_id=tenant_id,
            actor_hash=admin_hash,
            actor_role="admin",
            user_hash=target_hash,
            action="shadow_departure_logged",
            details={
                "departure_date": dep["departure_date"],
                "reason": dep["reason"],
                "predicted_risk": dep["predicted_risk"],
                "predicted_attrition_probability": dep["predicted_attrition_probability"],
                "correctly_predicted": dep["correctly_predicted"],
            },
            timestamp=now - timedelta(days=dep["days_ago"]),
        ))

    return entries


# ═══════════════════════════════════════════════════════════════════════════
# Chat Session Seeds
# ═══════════════════════════════════════════════════════════════════════════

SESSION_1_ID = "seed-session-team-risk-overview"
SESSION_2_ID = "seed-session-wellbeing-check"
CONVO_1_ID = "seed-convo-team-risk-overview"
CONVO_2_ID = "seed-convo-wellbeing-check"


def _generate_chat_sessions(
    user_hashes: dict,
    tenant_id,
    now: datetime,
) -> tuple:
    """Return (sessions, history_entries) for pre-seeded conversations."""
    admin_hash = user_hashes["admin@acme.com"]
    maria_hash = user_hashes["dev2@acme.com"]
    tid = str(tenant_id)

    sessions = [
        ChatSession(
            id=SESSION_1_ID,
            user_hash=admin_hash,
            tenant_id=tid,
            title="Team Risk Overview",
            is_active=True,
            is_favorite=False,
            created_at=now - timedelta(days=1, hours=3),
            updated_at=now - timedelta(days=1, hours=3),
        ),
        ChatSession(
            id=SESSION_2_ID,
            user_hash=maria_hash,
            tenant_id=tid,
            title="My Wellbeing Check",
            is_active=True,
            is_favorite=False,
            created_at=now - timedelta(hours=12),
            updated_at=now - timedelta(hours=12),
        ),
    ]

    history = [
        # Session 1, Turn 1 — user
        ChatHistory(
            user_hash=admin_hash,
            tenant_id=tid,
            conversation_id=CONVO_1_ID,
            session_id=SESSION_1_ID,
            role="user",
            type="message",
            content="Which employees are at risk of burnout?",
            created_at=now - timedelta(days=1, hours=3),
        ),
        # Session 1, Turn 1 — assistant
        ChatHistory(
            user_hash=admin_hash,
            tenant_id=tid,
            conversation_id=CONVO_1_ID,
            session_id=SESSION_1_ID,
            role="assistant",
            type="message",
            content=(
                "Based on current data, 1 employee is at CRITICAL risk and 3 are at ELEVATED risk:\n\n"
                "1. **Jordan Lee** - CRITICAL (velocity: 3.2, declining communication)\n"
                "2. **David Kim** - ELEVATED (increasing work hours)\n"
                "3. **Olivia Zhang** - ELEVATED (isolation pattern detected)\n"
                "4. **Priya Sharma** - ELEVATED (management overload)\n\n"
                "I recommend scheduling immediate 1:1s with Jordan and reviewing workload "
                "distribution for the elevated-risk employees."
            ),
            created_at=now - timedelta(days=1, hours=3) + timedelta(seconds=5),
        ),
        # Session 2, Turn 1 — user
        ChatHistory(
            user_hash=maria_hash,
            tenant_id=tid,
            conversation_id=CONVO_2_ID,
            session_id=SESSION_2_ID,
            role="user",
            type="message",
            content="How am I doing this week?",
            created_at=now - timedelta(hours=12),
        ),
        # Session 2, Turn 1 — assistant
        ChatHistory(
            user_hash=maria_hash,
            tenant_id=tid,
            conversation_id=CONVO_2_ID,
            session_id=SESSION_2_ID,
            role="assistant",
            type="message",
            content=(
                "Your wellbeing metrics look great this week! Your work pattern velocity is stable "
                "at 0.6, which is well within the healthy range. Your social engagement score is "
                "0.75, indicating strong team connections. Keep up the balanced approach!"
            ),
            created_at=now - timedelta(hours=12) + timedelta(seconds=5),
        ),
    ]

    return sessions, history


# ═══════════════════════════════════════════════════════════════════════════
# Notifications
# ═══════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════
# Supabase Auth helpers (UNCHANGED)
# ═══════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════
# Main Seed Function
# ═══════════════════════════════════════════════════════════════════════════

def seed():
    log.info("=" * 60)
    log.info("  SENTINEL -- Fresh Demo Seed (Persona-Driven Deterministic)")
    log.info("=" * 60)

    db = SessionLocal()
    now = datetime.utcnow()

    try:
        # -- Step 0: Ensure all tables exist, then clear data ------
        log.info("\n[RESET] Ensuring tables exist...")
        IdentityBase.metadata.create_all(engine, checkfirst=True)
        AnalyticsBase.metadata.create_all(engine, checkfirst=True)
        NotificationBase.metadata.create_all(engine, checkfirst=True)

        # Ensure display_name column exists on tenant_members
        try:
            with engine.connect() as conn:
                result = conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='identity' AND table_name='tenant_members' "
                    "AND column_name='display_name'"
                ))
                if result.fetchone() is None:
                    conn.execute(text(
                        "ALTER TABLE identity.tenant_members "
                        "ADD COLUMN display_name VARCHAR(100)"
                    ))
                    conn.commit()
                    log.info("    Added display_name column to tenant_members")
        except Exception as e:
            log.warning(f"    Could not check/add display_name column: {e}")

        log.info("[RESET] Clearing existing data...")
        for table in [
            ChatHistory, ChatSession, Invitation, Notification, NotificationPreference,
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

        # -- Step 1: Create Tenant ---------------------------------
        log.info("\n[1/10] Creating tenant...")
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

        # -- Step 2: Create Teams ----------------------------------
        log.info("\n[2/10] Creating teams...")
        team_map = {}
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

        # -- Step 3: Create Users + TenantMembers ------------------
        log.info("\n[3/10] Creating users and memberships...")
        user_hashes = {}

        for u in DEMO_USERS:
            user_hash = privacy.hash_identity(u["email"])
            user_hashes[u["email"]] = user_hash

            # Supabase Auth user
            _create_supabase_user(u["email"], u["name"], u["role"])

            # UserIdentity
            user = UserIdentity(
                user_hash=user_hash,
                tenant_id=tenant.id,
                email_encrypted=privacy.encrypt(u["email"]),
                consent_share_with_manager=(u["role"] != "admin"),
                consent_share_anonymized=True,
                is_active=True,
            )
            db.add(user)

            # TenantMember
            team_id = team_map[u["team"]].id if u["team"] else None
            member = TenantMember(
                tenant_id=tenant.id,
                user_hash=user_hash,
                role=u["role"],
                display_name=u["name"],
                team_id=team_id,
            )
            db.add(member)
            log.info(f"    {u['role']:>10} | {u['name']:<20} | {u['email']} | team={u['team'] or '-'}")

        db.flush()

        # -- Step 4: Risk Scores + Trending History ----------------
        log.info("\n[4/10] Creating risk scores and trending history...")
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

            # 30-day history with deterministic trends
            # Velocity on 0-5 scale (engine linregress slope), belongingness on 0-1 scale
            for day in range(30, 0, -1):
                vel = _risk_history_velocity(u["email"], day)
                vel = max(0.0, min(5.0, vel))
                risk = "CRITICAL" if vel > 2.5 else ("ELEVATED" if vel > 1.5 else "LOW")
                belong = _risk_history_belonging(u["email"], day)
                belong = max(0.0, min(1.0, belong))
                conf = profile["conf"] + rng.uniform(-0.03, 0.03)

                db.add(RiskHistory(
                    user_hash=uh,
                    tenant_id=tenant.id,
                    risk_level=risk,
                    velocity=round(vel, 1),
                    confidence=round(conf, 3),
                    belongingness_score=round(belong, 1),
                    timestamp=now - timedelta(days=day),
                ))

        log.info(f"    {len(DEMO_USERS)} risk scores + {len(DEMO_USERS) * 30} history entries")

        # -- Step 5: Deterministic Skill Profiles + Centrality -----
        log.info("\n[5/10] Creating skill profiles and network scores...")
        for u in DEMO_USERS:
            uh = user_hashes[u["email"]]
            sp = SKILL_PROFILES[u["email"]]
            cs = CENTRALITY_SCORES[u["email"]]

            db.add(SkillProfile(
                user_hash=uh,
                tenant_id=tenant.id,
                technical=sp["technical"],
                communication=sp["communication"],
                leadership=sp["leadership"],
                collaboration=sp["collaboration"],
                adaptability=sp["adaptability"],
                creativity=sp["creativity"],
            ))

            db.add(CentralityScore(
                user_hash=uh,
                tenant_id=tenant.id,
                betweenness=cs["betweenness"],
                eigenvector=cs["eigenvector"],
                unblocking_count=cs["unblocking_count"],
                knowledge_transfer_score=cs["knowledge_transfer_score"],
                calculated_at=now,
            ))

        log.info(f"    {len(DEMO_USERS)} skill profiles + {len(DEMO_USERS)} centrality scores")

        # -- Step 6: Persona-Driven Events + Graph Edges -----------
        log.info("\n[6/10] Creating persona-driven events and graph edges...")
        all_hashes = list(user_hashes.values())
        event_count = 0

        for u in DEMO_USERS:
            uh = user_hashes[u["email"]]
            events = _generate_persona_events(
                u["email"], uh, tenant.id, all_hashes, now,
            )
            for ev in events:
                db.add(ev)
            event_count += len(events)

        # Graph edges
        edges = _generate_graph_edges(user_hashes, team_map, tenant.id, now)
        for edge in edges:
            db.add(edge)

        log.info(f"    {event_count} events + {len(edges)} graph edges")

        # -- Step 7: Notifications ---------------------------------
        log.info("\n[7/10] Creating notifications...")
        notif_count = 0
        for u in DEMO_USERS:
            uh = user_hashes[u["email"]]
            k = rng.randint(3, len(NOTIFICATIONS))
            selected = rng.sample(NOTIFICATIONS, k=k)
            for notif in selected:
                read_hours = rng.randint(1, 48)
                created_hours = rng.randint(1, 168)
                db.add(Notification(
                    user_hash=uh,
                    tenant_id=tenant.id,
                    type=notif["type"],
                    title=notif["title"],
                    message=notif["message"],
                    priority=notif["priority"],
                    read_at=(now - timedelta(hours=read_hours)) if rng.random() > 0.4 else None,
                    created_at=now - timedelta(hours=created_hours),
                ))
                notif_count += 1

            for channel in ("in_app", "email"):
                for ntype in ("auth", "team", "system", "security", "activity"):
                    db.add(NotificationPreference(
                        user_hash=uh, channel=channel, notification_type=ntype, enabled=True,
                    ))

        log.info(f"    {notif_count} notifications + {len(DEMO_USERS) * 10} preferences")

        # -- Step 8: Rich Audit Log Timeline -----------------------
        log.info("\n[8/10] Creating rich audit log timeline...")
        audit_entries = _generate_audit_logs(user_hashes, tenant.id, now)
        for entry in audit_entries:
            db.add(entry)
        log.info(f"    {len(audit_entries)} audit log entries")

        # -- Step 9: Shadow Deployment Validation ------------------
        log.info("\n[9/10] Creating shadow deployment validation data...")
        shadow_entries = seed_shadow_departures(db, tenant.id, user_hashes, now)
        for entry in shadow_entries:
            db.add(entry)
        shadow_correct = sum(1 for e in shadow_entries if e.details.get("correctly_predicted"))
        log.info(
            f"    {len(shadow_entries)} shadow departures "
            f"({shadow_correct} correct, {len(shadow_entries) - shadow_correct} false negative, "
            f"accuracy {shadow_correct / max(len(shadow_entries), 1) * 100:.1f}%)"
        )

        # -- Step 10: Seed Chat Sessions ---------------------------
        log.info("\n[10/10] Creating pre-seeded chat sessions...")
        sessions, history = _generate_chat_sessions(user_hashes, tenant.id, now)
        for s in sessions:
            db.add(s)
        for h in history:
            db.add(h)
        log.info(f"    {len(sessions)} chat sessions + {len(history)} history entries")

        # -- Commit everything -------------------------------------
        db.commit()

        log.info("\n" + "=" * 60)
        log.info("  SEED COMPLETE")
        log.info(f"  Organization: {ORG_NAME} (enterprise)")
        log.info(f"  Teams: {', '.join(team_map.keys())}")
        log.info(f"  Users: {len(DEMO_USERS)}")
        log.info(f"  Events: {event_count}")
        log.info(f"  Graph Edges: {len(edges)}")
        log.info(f"  Audit Logs: {len(audit_entries)}")
        log.info(f"  Shadow Departures: {len(shadow_entries)} (accuracy {shadow_correct / max(len(shadow_entries), 1) * 100:.1f}%)")
        log.info(f"  Chat Sessions: {len(sessions)}")
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
