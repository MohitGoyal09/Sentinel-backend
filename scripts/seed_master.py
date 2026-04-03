#!/usr/bin/env python3
"""
AlgoQuest Master Demo Seed
==========================
Unified, persona-driven seed data for hackathon demo.
Tells the story of TechFlow Inc -- a B2B SaaS startup navigating real
engineering team dynamics: burnout, hidden gems, new hires, and contagion.

Usage:
    cd backend
    python scripts/seed_master.py           # seed (idempotent)
    python scripts/seed_master.py --reset   # wipe and re-seed
"""

import sys
import os
import logging
import random
from uuid import uuid4
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Path setup -- works whether run from repo root or backend/
# ---------------------------------------------------------------------------
_script_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_script_dir)
for _p in (_backend_dir, os.path.dirname(_backend_dir)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.core.database import SessionLocal, engine
from app.core.security import privacy
from app.models.workflow import Base as WorkflowBase
from app.models.identity import UserIdentity, AuditLog
from app.models.tenant import Tenant, TenantMember
from app.models.analytics import (
    Event,
    RiskScore,
    RiskHistory,
    GraphEdge,
    CentralityScore,
    SkillProfile,
)
from app.models.notification import (
    Notification,
    NotificationPreference,
    NotificationTemplate,
)
from app.models.workflow import WorkflowTemplate, WorkflowExecution

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("seed_master")

# Reproducible randomness -- same data every run
random.seed(42)


def _ensure_tables():
    """
    Create all required schemas and tables on Supabase if they don't exist,
    then patch any missing columns on tables that were created by older schema
    versions (create_all + checkfirst=True skips existing tables entirely).
    """
    from sqlalchemy import text
    from app.models.identity import Base as IdentityBase
    from app.models.analytics import Base as AnalyticsBase
    from app.models.workflow import UserIntegration, WorkflowTemplate, WorkflowExecution

    with engine.connect() as conn:
        # 1. Create schemas (Supabase only has 'public' by default)
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS identity"))
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS analytics"))
        conn.commit()
    log.info("  Schemas ready (identity, analytics, public).")

    # 2. Create tables that don't exist yet
    IdentityBase.metadata.create_all(engine, checkfirst=True)
    AnalyticsBase.metadata.create_all(engine, checkfirst=True)
    WorkflowBase.metadata.create_all(
        engine,
        tables=[
            UserIntegration.__table__,
            WorkflowTemplate.__table__,
            WorkflowExecution.__table__,
        ],
        checkfirst=True,
    )
    log.info("  Base tables created (or already exist).")

    # 3. Patch missing columns on tables that may have been created by an older
    #    schema version. ADD COLUMN IF NOT EXISTS is idempotent on PostgreSQL.
    patches = [
        # identity.users — columns added after initial create_all
        "ALTER TABLE identity.users ADD COLUMN IF NOT EXISTS tenant_id UUID",
        "ALTER TABLE identity.users ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'employee'",
        "ALTER TABLE identity.users ADD COLUMN IF NOT EXISTS consent_share_with_manager BOOLEAN DEFAULT FALSE",
        "ALTER TABLE identity.users ADD COLUMN IF NOT EXISTS consent_share_anonymized BOOLEAN DEFAULT TRUE",
        "ALTER TABLE identity.users ADD COLUMN IF NOT EXISTS monitoring_paused_until TIMESTAMP",
        "ALTER TABLE identity.users ADD COLUMN IF NOT EXISTS slack_id_encrypted BYTEA",
        # identity.tenants — ensure all columns exist
        "ALTER TABLE identity.tenants ADD COLUMN IF NOT EXISTS plan VARCHAR(50) DEFAULT 'free'",
        "ALTER TABLE identity.tenants ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active'",
        "ALTER TABLE identity.tenants ADD COLUMN IF NOT EXISTS settings JSONB",
        "ALTER TABLE identity.tenants ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP",
        # identity.tenant_members — ensure all columns exist
        "ALTER TABLE identity.tenant_members ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'member'",
        "ALTER TABLE identity.tenant_members ADD COLUMN IF NOT EXISTS invited_by VARCHAR(64)",
        "ALTER TABLE identity.tenant_members ADD COLUMN IF NOT EXISTS joined_at TIMESTAMP",
        # identity.notifications — ensure all columns exist
        "ALTER TABLE identity.notifications ADD COLUMN IF NOT EXISTS user_hash VARCHAR(64)",
        "ALTER TABLE identity.notifications ADD COLUMN IF NOT EXISTS tenant_id UUID",
        "ALTER TABLE identity.notifications ADD COLUMN IF NOT EXISTS type VARCHAR(50)",
        "ALTER TABLE identity.notifications ADD COLUMN IF NOT EXISTS title VARCHAR(255)",
        "ALTER TABLE identity.notifications ADD COLUMN IF NOT EXISTS message VARCHAR(1000)",
        "ALTER TABLE identity.notifications ADD COLUMN IF NOT EXISTS data JSONB",
        "ALTER TABLE identity.notifications ADD COLUMN IF NOT EXISTS priority VARCHAR(20) DEFAULT 'normal'",
        "ALTER TABLE identity.notifications ADD COLUMN IF NOT EXISTS action_url VARCHAR(500)",
        "ALTER TABLE identity.notifications ADD COLUMN IF NOT EXISTS read_at TIMESTAMP",
        "ALTER TABLE identity.notifications ADD COLUMN IF NOT EXISTS created_at TIMESTAMP",
        # identity.notification_preferences — ensure all columns exist
        "ALTER TABLE identity.notification_preferences ADD COLUMN IF NOT EXISTS user_hash VARCHAR(64)",
        "ALTER TABLE identity.notification_preferences ADD COLUMN IF NOT EXISTS channel VARCHAR(20)",
        "ALTER TABLE identity.notification_preferences ADD COLUMN IF NOT EXISTS notification_type VARCHAR(50)",
        "ALTER TABLE identity.notification_preferences ADD COLUMN IF NOT EXISTS enabled BOOLEAN DEFAULT TRUE",
        # identity.notification_templates — ensure all columns exist
        "ALTER TABLE identity.notification_templates ADD COLUMN IF NOT EXISTS type VARCHAR(50)",
        "ALTER TABLE identity.notification_templates ADD COLUMN IF NOT EXISTS subject VARCHAR(255)",
        "ALTER TABLE identity.notification_templates ADD COLUMN IF NOT EXISTS body_template VARCHAR(2000)",
        "ALTER TABLE identity.notification_templates ADD COLUMN IF NOT EXISTS variables JSONB",
        # analytics.events — ensure all columns exist
        "ALTER TABLE analytics.events ADD COLUMN IF NOT EXISTS user_hash VARCHAR(64)",
        "ALTER TABLE analytics.events ADD COLUMN IF NOT EXISTS tenant_id UUID",
        "ALTER TABLE analytics.events ADD COLUMN IF NOT EXISTS timestamp TIMESTAMP",
        "ALTER TABLE analytics.events ADD COLUMN IF NOT EXISTS event_type VARCHAR(50)",
        "ALTER TABLE analytics.events ADD COLUMN IF NOT EXISTS target_user_hash VARCHAR(64)",
        "ALTER TABLE analytics.events ADD COLUMN IF NOT EXISTS metadata JSONB",
        # analytics.risk_scores — ensure all columns exist
        "ALTER TABLE analytics.risk_scores ADD COLUMN IF NOT EXISTS user_hash VARCHAR(64)",
        "ALTER TABLE analytics.risk_scores ADD COLUMN IF NOT EXISTS tenant_id UUID",
        "ALTER TABLE analytics.risk_scores ADD COLUMN IF NOT EXISTS velocity FLOAT",
        "ALTER TABLE analytics.risk_scores ADD COLUMN IF NOT EXISTS risk_level VARCHAR(20)",
        "ALTER TABLE analytics.risk_scores ADD COLUMN IF NOT EXISTS confidence FLOAT",
        "ALTER TABLE analytics.risk_scores ADD COLUMN IF NOT EXISTS thwarted_belongingness FLOAT",
        "ALTER TABLE analytics.risk_scores ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP",
        # analytics.graph_edges — ensure all columns exist
        "ALTER TABLE analytics.graph_edges ADD COLUMN IF NOT EXISTS tenant_id UUID",
        "ALTER TABLE analytics.graph_edges ADD COLUMN IF NOT EXISTS source_hash VARCHAR(64)",
        "ALTER TABLE analytics.graph_edges ADD COLUMN IF NOT EXISTS target_hash VARCHAR(64)",
        "ALTER TABLE analytics.graph_edges ADD COLUMN IF NOT EXISTS weight FLOAT",
        "ALTER TABLE analytics.graph_edges ADD COLUMN IF NOT EXISTS last_interaction TIMESTAMP",
        "ALTER TABLE analytics.graph_edges ADD COLUMN IF NOT EXISTS edge_type VARCHAR(30)",
        # analytics.centrality_scores — ensure all columns exist
        "ALTER TABLE analytics.centrality_scores ADD COLUMN IF NOT EXISTS user_hash VARCHAR(64)",
        "ALTER TABLE analytics.centrality_scores ADD COLUMN IF NOT EXISTS tenant_id UUID",
        "ALTER TABLE analytics.centrality_scores ADD COLUMN IF NOT EXISTS betweenness FLOAT",
        "ALTER TABLE analytics.centrality_scores ADD COLUMN IF NOT EXISTS eigenvector FLOAT",
        "ALTER TABLE analytics.centrality_scores ADD COLUMN IF NOT EXISTS unblocking_count INTEGER",
        "ALTER TABLE analytics.centrality_scores ADD COLUMN IF NOT EXISTS knowledge_transfer_score FLOAT",
        "ALTER TABLE analytics.centrality_scores ADD COLUMN IF NOT EXISTS calculated_at TIMESTAMP",
        # analytics.risk_history — ensure all columns exist
        "ALTER TABLE analytics.risk_history ADD COLUMN IF NOT EXISTS user_hash VARCHAR(64)",
        "ALTER TABLE analytics.risk_history ADD COLUMN IF NOT EXISTS tenant_id UUID",
        "ALTER TABLE analytics.risk_history ADD COLUMN IF NOT EXISTS risk_level VARCHAR(20)",
        "ALTER TABLE analytics.risk_history ADD COLUMN IF NOT EXISTS velocity FLOAT",
        "ALTER TABLE analytics.risk_history ADD COLUMN IF NOT EXISTS confidence FLOAT",
        "ALTER TABLE analytics.risk_history ADD COLUMN IF NOT EXISTS belongingness_score FLOAT",
        "ALTER TABLE analytics.risk_history ADD COLUMN IF NOT EXISTS timestamp TIMESTAMP",
        # analytics.skill_profiles — ensure all columns exist
        "ALTER TABLE analytics.skill_profiles ADD COLUMN IF NOT EXISTS user_hash VARCHAR(64)",
        "ALTER TABLE analytics.skill_profiles ADD COLUMN IF NOT EXISTS tenant_id UUID",
        "ALTER TABLE analytics.skill_profiles ADD COLUMN IF NOT EXISTS technical FLOAT DEFAULT 50.0",
        "ALTER TABLE analytics.skill_profiles ADD COLUMN IF NOT EXISTS communication FLOAT DEFAULT 50.0",
        "ALTER TABLE analytics.skill_profiles ADD COLUMN IF NOT EXISTS leadership FLOAT DEFAULT 50.0",
        "ALTER TABLE analytics.skill_profiles ADD COLUMN IF NOT EXISTS collaboration FLOAT DEFAULT 50.0",
        "ALTER TABLE analytics.skill_profiles ADD COLUMN IF NOT EXISTS adaptability FLOAT DEFAULT 50.0",
        "ALTER TABLE analytics.skill_profiles ADD COLUMN IF NOT EXISTS creativity FLOAT DEFAULT 50.0",
        "ALTER TABLE analytics.skill_profiles ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP",
    ]

    with engine.connect() as conn:
        for sql in patches:
            try:
                conn.execute(text(sql))
            except Exception:
                pass  # column already exists with different syntax on older PG
        conn.commit()
    log.info("  Schema patches applied (missing columns added).")

# ===========================================================================
# COMPANY CONFIG
# ===========================================================================
TENANT_NAME = "TechFlow Inc"
TENANT_SLUG = "techflow-inc"
TENANT_PLAN = "enterprise"
TENANT_DOMAIN = "techflow.io"

# ===========================================================================
# THE CAST -- 12 personas
# Roles: admin, manager, employee
# Personas: cto, manager_healthy, burnout, hidden_gem, high_performer,
#           steady, new_hire, struggling, contagion, healthy
# ===========================================================================
CAST = [
    # Leadership
    {"email": "admin@techflow.io",          "name": "Chen Wei",       "title": "CTO",
     "role": "admin",    "manager": None,                       "slack_id": "U001CHEN",   "persona": "cto"},
    {"email": "alex.rivera@techflow.io",    "name": "Alex Rivera",    "title": "Engineering Manager",
     "role": "manager",  "manager": "admin@techflow.io",        "slack_id": "U002ALEX",   "persona": "manager_healthy"},
    # Engineers under Alex
    {"email": "sarah.chen@techflow.io",     "name": "Sarah Chen",     "title": "Senior Engineer",
     "role": "employee", "manager": "alex.rivera@techflow.io",  "slack_id": "U003SARAH",  "persona": "burnout"},
    {"email": "marcus.johnson@techflow.io", "name": "Marcus Johnson", "title": "Senior Engineer",
     "role": "employee", "manager": "alex.rivera@techflow.io",  "slack_id": "U004MARCUS", "persona": "hidden_gem"},
    {"email": "priya.sharma@techflow.io",   "name": "Priya Sharma",   "title": "Senior Engineer",
     "role": "employee", "manager": "alex.rivera@techflow.io",  "slack_id": "U005PRIYA",  "persona": "high_performer"},
    {"email": "jordan.lee@techflow.io",     "name": "Jordan Lee",     "title": "Mid Engineer",
     "role": "employee", "manager": "alex.rivera@techflow.io",  "slack_id": "U006JORDAN", "persona": "steady"},
    {"email": "yuki.tanaka@techflow.io",    "name": "Yuki Tanaka",    "title": "Junior Engineer",
     "role": "employee", "manager": "alex.rivera@techflow.io",  "slack_id": "U007YUKI",   "persona": "new_hire"},
    {"email": "emma.wilson@techflow.io",    "name": "Emma Wilson",    "title": "Mid Engineer",
     "role": "employee", "manager": "alex.rivera@techflow.io",  "slack_id": "U008EMMA",   "persona": "struggling"},
    # Product / Design / Ops under Chen
    {"email": "david.park@techflow.io",     "name": "David Park",     "title": "Product Manager",
     "role": "manager",  "manager": "admin@techflow.io",        "slack_id": "U009DAVID",  "persona": "contagion"},
    {"email": "lucas.martinez@techflow.io", "name": "Lucas Martinez", "title": "Designer",
     "role": "employee", "manager": "admin@techflow.io",        "slack_id": "U010LUCAS",  "persona": "healthy"},
    {"email": "aisha.patel@techflow.io",    "name": "Aisha Patel",    "title": "People Ops",
     "role": "employee", "manager": "admin@techflow.io",        "slack_id": "U011AISHA",  "persona": "healthy"},
    {"email": "manager2@techflow.io",       "name": "Jamie Kim",      "title": "Eng Manager 2",
     "role": "manager",  "manager": "admin@techflow.io",        "slack_id": "U012JAMIE",  "persona": "manager_healthy"},
]

# ===========================================================================
# RISK SCORE SPECS (current snapshot)
# ===========================================================================
RISK_SPECS = {
    "sarah.chen@techflow.io":     {"velocity": 94.2, "risk_level": "CRITICAL",  "confidence": 0.91, "belongingness": 0.18},
    "marcus.johnson@techflow.io": {"velocity": 41.3, "risk_level": "LOW",       "confidence": 0.88, "belongingness": 0.85},
    "priya.sharma@techflow.io":   {"velocity": 51.7, "risk_level": "LOW",       "confidence": 0.90, "belongingness": 0.71},
    "jordan.lee@techflow.io":     {"velocity": 40.1, "risk_level": "LOW",       "confidence": 0.86, "belongingness": 0.71},
    "yuki.tanaka@techflow.io":    {"velocity": 52.1, "risk_level": "ELEVATED",  "confidence": 0.65, "belongingness": 0.62},
    "emma.wilson@techflow.io":    {"velocity": 64.5, "risk_level": "ELEVATED",  "confidence": 0.82, "belongingness": 0.25},
    "david.park@techflow.io":     {"velocity": 42.0, "risk_level": "ELEVATED",  "confidence": 0.77, "belongingness": 0.55},
    "alex.rivera@techflow.io":    {"velocity": 33.0, "risk_level": "LOW",       "confidence": 0.89, "belongingness": 0.78},
    "admin@techflow.io":          {"velocity": 28.0, "risk_level": "LOW",       "confidence": 0.92, "belongingness": 0.82},
    "lucas.martinez@techflow.io": {"velocity": 36.5, "risk_level": "LOW",       "confidence": 0.84, "belongingness": 0.74},
    "aisha.patel@techflow.io":    {"velocity": 34.2, "risk_level": "LOW",       "confidence": 0.87, "belongingness": 0.80},
    "manager2@techflow.io":       {"velocity": 31.8, "risk_level": "LOW",       "confidence": 0.85, "belongingness": 0.76},
}

# ===========================================================================
# SKILL PROFILES (0-100 scale)
# ===========================================================================
SKILL_SPECS = {
    "sarah.chen@techflow.io":     {"technical": 91, "communication": 54, "leadership": 42, "collaboration": 48, "adaptability": 35, "creativity": 72},
    "marcus.johnson@techflow.io": {"technical": 86, "communication": 88, "leadership": 74, "collaboration": 95, "adaptability": 82, "creativity": 71},
    "priya.sharma@techflow.io":   {"technical": 94, "communication": 88, "leadership": 82, "collaboration": 85, "adaptability": 88, "creativity": 79},
    "jordan.lee@techflow.io":     {"technical": 74, "communication": 72, "leadership": 55, "collaboration": 78, "adaptability": 71, "creativity": 68},
    "yuki.tanaka@techflow.io":    {"technical": 65, "communication": 70, "leadership": 40, "collaboration": 72, "adaptability": 88, "creativity": 75},
    "emma.wilson@techflow.io":    {"technical": 78, "communication": 42, "leadership": 38, "collaboration": 31, "adaptability": 45, "creativity": 84},
    "david.park@techflow.io":     {"technical": 52, "communication": 61, "leadership": 55, "collaboration": 38, "adaptability": 48, "creativity": 58},
    "alex.rivera@techflow.io":    {"technical": 72, "communication": 88, "leadership": 91, "collaboration": 88, "adaptability": 85, "creativity": 74},
    "admin@techflow.io":          {"technical": 88, "communication": 92, "leadership": 95, "collaboration": 88, "adaptability": 90, "creativity": 88},
    "lucas.martinez@techflow.io": {"technical": 58, "communication": 82, "leadership": 58, "collaboration": 78, "adaptability": 75, "creativity": 95},
    "aisha.patel@techflow.io":    {"technical": 48, "communication": 94, "leadership": 72, "collaboration": 92, "adaptability": 85, "creativity": 68},
    "manager2@techflow.io":       {"technical": 68, "communication": 82, "leadership": 82, "collaboration": 85, "adaptability": 78, "creativity": 65},
}

# ===========================================================================
# CENTRALITY SCORES
# ===========================================================================
CENTRALITY_SPECS = {
    "marcus.johnson@techflow.io": {"betweenness": 0.89, "eigenvector": 0.71, "unblocking_count": 47, "knowledge_transfer": 0.91},
    "priya.sharma@techflow.io":   {"betweenness": 0.71, "eigenvector": 0.83, "unblocking_count": 28, "knowledge_transfer": 0.93},
    "alex.rivera@techflow.io":    {"betweenness": 0.65, "eigenvector": 0.78, "unblocking_count": 15, "knowledge_transfer": 0.72},
    "sarah.chen@techflow.io":     {"betweenness": 0.42, "eigenvector": 0.61, "unblocking_count": 8,  "knowledge_transfer": 0.68},
    "jordan.lee@techflow.io":     {"betweenness": 0.35, "eigenvector": 0.52, "unblocking_count": 5,  "knowledge_transfer": 0.55},
    "david.park@techflow.io":     {"betweenness": 0.28, "eigenvector": 0.31, "unblocking_count": 2,  "knowledge_transfer": 0.28},
    "yuki.tanaka@techflow.io":    {"betweenness": 0.18, "eigenvector": 0.34, "unblocking_count": 1,  "knowledge_transfer": 0.32},
    "emma.wilson@techflow.io":    {"betweenness": 0.12, "eigenvector": 0.22, "unblocking_count": 0,  "knowledge_transfer": 0.18},
    "lucas.martinez@techflow.io": {"betweenness": 0.31, "eigenvector": 0.42, "unblocking_count": 6,  "knowledge_transfer": 0.48},
    "admin@techflow.io":          {"betweenness": 0.55, "eigenvector": 0.90, "unblocking_count": 12, "knowledge_transfer": 0.85},
    "manager2@techflow.io":       {"betweenness": 0.40, "eigenvector": 0.58, "unblocking_count": 10, "knowledge_transfer": 0.62},
    "aisha.patel@techflow.io":    {"betweenness": 0.33, "eigenvector": 0.41, "unblocking_count": 4,  "knowledge_transfer": 0.45},
}

# ===========================================================================
# WORKFLOW TEMPLATES
# ===========================================================================
WORKFLOW_TEMPLATES = [
    {
        "template_id": "tmpl_daily_standup",
        "name": "Daily Standup Summary",
        "description": "Fetch yesterday's commits, PRs, and Slack messages. Summarize blockers and accomplishments for standup.",
        "category": "productivity",
        "icon": "layout-list",
        "prompt_template": "Fetch my GitHub activity, open PRs, and Slack messages from the last {hours} hours. Summarize what I accomplished and flag any blockers for my daily standup.",
        "required_integrations": ["github", "slack"],
        "optional_integrations": ["jira"],
        "parameters": [{"name": "hours", "type": "number", "default": 24, "label": "Lookback hours"}],
        "is_public": True, "is_system": True, "usage_count": 342,
    },
    {
        "template_id": "tmpl_weekly_email_digest",
        "name": "Weekly Email Digest",
        "description": "Summarize your most important emails from the past week and draft replies for items needing action.",
        "category": "communication",
        "icon": "mail",
        "prompt_template": "Review my Gmail inbox for the last 7 days. Summarize the {max_emails} most important emails by priority, identify which need replies, and draft responses for the top {draft_count} action items.",
        "required_integrations": ["gmail"],
        "optional_integrations": ["googlecalendar"],
        "parameters": [
            {"name": "max_emails", "type": "number", "default": 50, "label": "Max emails to scan"},
            {"name": "draft_count", "type": "number", "default": 3,  "label": "Emails to draft replies for"},
        ],
        "is_public": True, "is_system": True, "usage_count": 218,
    },
    {
        "template_id": "tmpl_meeting_prep",
        "name": "Meeting Preparation Brief",
        "description": "Auto-prepare for your next meeting: pull context from calendar, emails, and Slack threads about the meeting topic.",
        "category": "productivity",
        "icon": "calendar-check",
        "prompt_template": "I have a meeting about {topic} in {minutes_until} minutes. Pull the calendar invite details, find related email threads and Slack conversations from the last {lookback_days} days, and create a preparation brief with key context and suggested questions.",
        "required_integrations": ["googlecalendar", "gmail"],
        "optional_integrations": ["slack", "notion"],
        "parameters": [
            {"name": "topic",         "type": "string", "default": "",  "label": "Meeting topic"},
            {"name": "minutes_until", "type": "number", "default": 30,  "label": "Minutes until meeting"},
            {"name": "lookback_days", "type": "number", "default": 7,   "label": "Days to look back"},
        ],
        "is_public": True, "is_system": True, "usage_count": 187,
    },
    {
        "template_id": "tmpl_jira_sprint_review",
        "name": "Sprint Review Report",
        "description": "Generate a sprint review report from Jira: completed tickets, velocity, blockers, and carry-overs.",
        "category": "engineering",
        "icon": "bar-chart-2",
        "prompt_template": "Fetch all Jira tickets from the current sprint for project {project_key}. Create a sprint review report showing: completed tickets by assignee, story points delivered vs planned, carry-over items with reasons, and top 3 blockers encountered.",
        "required_integrations": ["jira"],
        "optional_integrations": ["slack", "github"],
        "parameters": [
            {"name": "project_key", "type": "string", "default": "", "label": "Jira project key"},
        ],
        "is_public": True, "is_system": True, "usage_count": 156,
    },
    {
        "template_id": "tmpl_slack_catch_up",
        "name": "Slack Catch-Up Summary",
        "description": "Summarize what you missed on Slack while you were away.",
        "category": "communication",
        "icon": "message-square",
        "prompt_template": "I was away for {hours} hours. Summarize all Slack activity in channels {channels} and direct messages. Flag any @mentions, decisions made, action items assigned to me, and urgent threads I need to respond to.",
        "required_integrations": ["slack"],
        "optional_integrations": [],
        "parameters": [
            {"name": "hours",    "type": "number", "default": 8,                       "label": "Hours away"},
            {"name": "channels", "type": "string", "default": "#general, #engineering", "label": "Channels to check"},
        ],
        "is_public": True, "is_system": True, "usage_count": 289,
    },
    {
        "template_id": "tmpl_team_health_report",
        "name": "Team Health Report",
        "description": "Generate a manager-friendly team health report: risk flags, workload distribution, and recommended actions.",
        "category": "management",
        "icon": "users",
        "prompt_template": "Pull team health data for the {team_size} people on my team. Identify anyone showing burnout signals, hidden gems being underutilized, or isolation patterns. Create an actionable report with specific recommended 1:1 talking points for each team member.",
        "required_integrations": ["slack"],
        "optional_integrations": ["googlecalendar", "jira"],
        "parameters": [
            {"name": "team_size", "type": "number", "default": 8, "label": "Team size"},
        ],
        "is_public": True, "is_system": True, "usage_count": 134,
    },
    {
        "template_id": "tmpl_github_pr_review",
        "name": "PR Review Summary",
        "description": "Summarize all open PRs needing your review -- key changes, risk assessment, and suggested review order.",
        "category": "engineering",
        "icon": "git-pull-request",
        "prompt_template": "Fetch all open GitHub pull requests assigned to me for review in repos {repos}. For each PR: summarize key changes, estimate review effort, flag potential risks or breaking changes, and recommend review order by priority.",
        "required_integrations": ["github"],
        "optional_integrations": ["slack"],
        "parameters": [
            {"name": "repos", "type": "string", "default": "", "label": "Repositories (comma-separated)"},
        ],
        "is_public": True, "is_system": True, "usage_count": 198,
    },
    {
        "template_id": "tmpl_notion_weekly_notes",
        "name": "Weekly Notes to Action Items",
        "description": "Convert your weekly Notion meeting notes into structured action items, owners, and deadlines.",
        "category": "productivity",
        "icon": "file-text",
        "prompt_template": "Read my Notion page or database titled {page_title} and extract all action items, decisions, and follow-ups from the past {days} days. Format them as a structured TODO list with owners and due dates.",
        "required_integrations": ["notion"],
        "optional_integrations": ["slack", "jira"],
        "parameters": [
            {"name": "page_title", "type": "string", "default": "Meeting Notes", "label": "Notion page/database title"},
            {"name": "days",       "type": "number", "default": 7,               "label": "Days to look back"},
        ],
        "is_public": True, "is_system": True, "usage_count": 112,
    },
]

# ===========================================================================
# NOTIFICATION TEMPLATES
# ===========================================================================
NOTIFICATION_TEMPLATES = [
    {
        "type": "risk_critical",
        "subject": "Critical Risk Alert: {{employee_name}}",
        "body_template": "{{employee_name}} has entered a critical burnout risk zone. Velocity: {{velocity}}. Recommended action: Schedule 1:1 immediately and consider workload reduction.",
        "variables": ["employee_name", "velocity"],
    },
    {
        "type": "risk_elevated",
        "subject": "Elevated Risk Detected: {{employee_name}}",
        "body_template": "{{employee_name}} is showing elevated risk signals. Belongingness score has dropped to {{belongingness_score}}. Consider a check-in.",
        "variables": ["employee_name", "belongingness_score"],
    },
    {
        "type": "hidden_gem_detected",
        "subject": "Hidden Gem Identified: {{employee_name}}",
        "body_template": "{{employee_name}} has been identified as a hidden gem -- unblocking {{unblocking_count}} colleagues but receiving little recognition. Consider public acknowledgement.",
        "variables": ["employee_name", "unblocking_count"],
    },
    {
        "type": "auth_login",
        "subject": "New sign-in to your account",
        "body_template": "A new sign-in was detected from {{device}} in {{location}}. If this wasn't you, contact your admin.",
        "variables": ["device", "location"],
    },
    {
        "type": "team_member_added",
        "subject": "New team member: {{member_name}}",
        "body_template": "{{member_name}} has joined your team. Their onboarding journey has begun. Check in within the first week.",
        "variables": ["member_name"],
    },
    {
        "type": "workflow_completed",
        "subject": "Workflow completed: {{workflow_name}}",
        "body_template": "Your workflow '{{workflow_name}}' completed in {{duration_seconds}}s. {{result_preview}}",
        "variables": ["workflow_name", "duration_seconds", "result_preview"],
    },
    {
        "type": "weekly_insight",
        "subject": "Weekly Team Insights -- {{week_of}}",
        "body_template": "Your weekly team health summary is ready. {{num_alerts}} alerts, {{num_highlights}} highlights. Top priority: {{top_priority}}.",
        "variables": ["week_of", "num_alerts", "num_highlights", "top_priority"],
    },
    {
        "type": "security_alert",
        "subject": "Security: Unusual access pattern detected",
        "body_template": "An unusual data access pattern was detected for {{user_name}}. {{detail}}. Review in the audit log.",
        "variables": ["user_name", "detail"],
    },
]


# ===========================================================================
# RISK HISTORY GENERATORS
# Produces 30 daily snapshots per user matching each persona's trajectory.
# ===========================================================================

def _risk_history_for(email, user_hash, tenant_id, now):
    records = []

    def snap(day, velocity, risk_level, confidence, belongingness):
        return RiskHistory(
            user_hash=user_hash,
            tenant_id=tenant_id,
            risk_level=risk_level,
            velocity=round(velocity + random.uniform(-0.5, 0.5), 2),
            confidence=round(confidence + random.uniform(-0.02, 0.02), 3),
            belongingness_score=round(max(0.0, min(1.0, belongingness + random.uniform(-0.01, 0.01))), 3),
            timestamp=now - timedelta(days=day),
        )

    if email == "sarah.chen@techflow.io":
        # Burnout trajectory: rising velocity, crashing belongingness
        for d in range(30, 20, -1):
            records.append(snap(d, random.uniform(52, 58), "LOW",      0.82, random.uniform(0.70, 0.74)))
        for d in range(20, 10, -1):
            records.append(snap(d, random.uniform(68, 76), "ELEVATED", 0.84, random.uniform(0.52, 0.60)))
        for d in range(10, 5, -1):
            records.append(snap(d, random.uniform(85, 91), "CRITICAL", 0.88, random.uniform(0.28, 0.34)))
        for d in range(5, 0, -1):
            records.append(snap(d, random.uniform(92, 96), "CRITICAL", 0.91, random.uniform(0.15, 0.21)))

    elif email == "marcus.johnson@techflow.io":
        # Hidden gem: rock-solid low risk, consistently high belongingness
        for d in range(30, 0, -1):
            records.append(snap(d, random.uniform(38, 45), "LOW", 0.87, random.uniform(0.82, 0.88)))

    elif email == "priya.sharma@techflow.io":
        # High performer: optimal zone throughout
        for d in range(30, 0, -1):
            records.append(snap(d, random.uniform(47, 55), "LOW", 0.90, random.uniform(0.68, 0.75)))

    elif email == "jordan.lee@techflow.io":
        # Steady: gentle growth arc
        for d in range(30, 0, -1):
            base_vel = 35 + (30 - d) * 0.17
            records.append(snap(d, base_vel, "LOW", 0.86, random.uniform(0.69, 0.74)))

    elif email == "yuki.tanaka@techflow.io":
        # New hire: onboarding dip then learning curve spike
        for d in range(30, 20, -1):
            records.append(snap(d, random.uniform(25, 31), "LOW",      0.60, random.uniform(0.60, 0.66)))
        for d in range(20, 10, -1):
            records.append(snap(d, random.uniform(36, 41), "LOW",      0.63, random.uniform(0.62, 0.68)))
        for d in range(10, 0, -1):
            records.append(snap(d, random.uniform(48, 55), "ELEVATED", 0.65, random.uniform(0.60, 0.65)))

    elif email == "emma.wilson@techflow.io":
        # Struggling: persistently elevated, declining belongingness
        for d in range(30, 15, -1):
            records.append(snap(d, random.uniform(59, 65), "ELEVATED", 0.79, random.uniform(0.28, 0.33)))
        for d in range(15, 0, -1):
            records.append(snap(d, random.uniform(63, 69), "ELEVATED", 0.82, random.uniform(0.20, 0.27)))

    elif email == "david.park@techflow.io":
        # Contagion: elevated with occasional LOW blips
        for d in range(30, 0, -1):
            rl = "ELEVATED" if random.random() > 0.25 else "LOW"
            records.append(snap(d, random.uniform(39, 46), rl, 0.76, random.uniform(0.50, 0.60)))

    elif email == "alex.rivera@techflow.io":
        for d in range(30, 0, -1):
            records.append(snap(d, random.uniform(30, 37), "LOW", 0.89, random.uniform(0.75, 0.82)))

    elif email == "admin@techflow.io":
        for d in range(30, 0, -1):
            records.append(snap(d, random.uniform(24, 32), "LOW", 0.92, random.uniform(0.80, 0.85)))

    else:  # lucas, aisha, jamie
        for d in range(30, 0, -1):
            records.append(snap(d, random.uniform(30, 40), "LOW", 0.85, random.uniform(0.72, 0.80)))

    return records


# ===========================================================================
# EVENT GENERATORS
# One function dispatched by persona -- returns Event ORM objects.
# ===========================================================================

def _make_event(user_hash, tenant_id, timestamp, event_type, target_hash=None, meta=None):
    return Event(
        user_hash=user_hash,
        tenant_id=tenant_id,
        timestamp=timestamp,
        event_type=event_type,
        target_user_hash=target_hash,
        metadata_=meta or {},
    )


def _ts(now, days_ago, hour):
    """Build a datetime offset: now minus days_ago days, at the given hour."""
    return now - timedelta(days=days_ago) + timedelta(
        hours=hour - 24, minutes=random.randint(0, 59)
    )


def generate_events(email, user_hash, tenant_id, now, all_hashes):
    """Return a list of Event ORM objects for one persona over 30 days."""
    events = []
    others = [h for e, h in all_hashes.items() if e != email]

    def pick():
        return random.choice(others) if others else None

    # ── Burnout (Sarah): rapidly escalating workload ─────────────────────────
    if email == "sarah.chen@techflow.io":
        for day in range(30, 0, -1):
            if day > 20:
                n, hour_lo, hour_hi, after_h, ctx = 5, 9, 18, False, random.randint(3, 5)
            elif day > 10:
                n, hour_lo, hour_hi, after_h, ctx = 8, 7, 23, random.random() > 0.4, random.randint(6, 9)
            else:
                n, hour_lo, hour_hi, after_h, ctx = 10, 6, 23, random.random() > 0.25, random.randint(9, 13)
            hours = [random.randint(hour_lo, hour_hi) for _ in range(n)]
            for h in hours[:6]:
                et = random.choices(["commit", "pr_review", "slack_message"], weights=[50, 30, 20])[0]
                events.append(_make_event(user_hash, tenant_id, _ts(now, day, h), et, None,
                    {"after_hours": after_h, "context_switches": ctx,
                     "lines_changed": random.randint(20, 400) if et == "commit" else None}))

    # ── Hidden Gem (Marcus): consistent, collaborative, unblocking ───────────
    elif email == "marcus.johnson@techflow.io":
        targets = [all_hashes.get(e) for e in (
            "sarah.chen@techflow.io", "jordan.lee@techflow.io",
            "yuki.tanaka@techflow.io", "priya.sharma@techflow.io"
        ) if all_hashes.get(e)]
        for day in range(30, 0, -1):
            hours = sorted([random.randint(9, 17) for _ in range(random.randint(4, 6))])
            for h in hours[:5]:
                et = random.choices(
                    ["commit", "pr_review", "code_review", "unblocked"],
                    weights=[35, 25, 20, 20]
                )[0]
                tgt = random.choice(targets) if et in ("pr_review", "code_review", "unblocked") and targets else None
                events.append(_make_event(user_hash, tenant_id, _ts(now, day, h), et, tgt,
                    {"after_hours": False, "context_switches": random.randint(1, 3),
                     "is_question": False, "unblocking_count": 1 if et == "unblocked" else 0}))
        # Additional weekly unblocked events (the hidden gem story)
        for week_start in [25, 18, 11, 4]:
            for _ in range(random.randint(3, 5)):
                day = max(1, week_start - random.randint(0, 6))
                tgt = random.choice(targets) if targets else None
                events.append(_make_event(user_hash, tenant_id, _ts(now, day, random.randint(10, 16)),
                    "unblocked", tgt, {"after_hours": False, "context_switches": 1, "unblocking_count": 1}))

    # ── High Performer (Priya): balanced, mentoring Yuki ────────────────────
    elif email == "priya.sharma@techflow.io":
        yuki_hash = all_hashes.get("yuki.tanaka@techflow.io")
        for day in range(30, 0, -1):
            for h in sorted([random.randint(9, 18) for _ in range(random.randint(5, 7))])[:6]:
                et = random.choices(
                    ["commit", "pr_review", "code_review", "slack_message"],
                    weights=[40, 30, 20, 10]
                )[0]
                tgt = yuki_hash if et == "code_review" and yuki_hash else (pick() if et != "commit" else None)
                events.append(_make_event(user_hash, tenant_id, _ts(now, day, h), et, tgt,
                    {"after_hours": False, "context_switches": random.randint(2, 4),
                     "comment_length": random.randint(80, 350) if "review" in et else None}))

    # ── Steady (Jordan): predictable, improving ──────────────────────────────
    elif email == "jordan.lee@techflow.io":
        for day in range(30, 0, -1):
            for h in [random.randint(9, 17) for _ in range(random.randint(3, 5))][:4]:
                et = random.choices(["commit", "pr_review", "slack_message"], weights=[55, 25, 20])[0]
                events.append(_make_event(user_hash, tenant_id, _ts(now, day, h), et,
                    pick() if et != "commit" else None,
                    {"after_hours": False, "context_switches": random.randint(2, 4)}))

    # ── New Hire (Yuki): questions early, commits later ──────────────────────
    elif email == "yuki.tanaka@techflow.io":
        mentors = [all_hashes.get(e) for e in (
            "marcus.johnson@techflow.io", "priya.sharma@techflow.io"
        ) if all_hashes.get(e)]
        for day in range(30, 0, -1):
            if day > 20:
                et = random.choices(["slack_message", "commit"], weights=[65, 35])[0]
                is_q = et == "slack_message" and random.random() > 0.3
                tgt = random.choice(mentors) if mentors and is_q else None
                events.append(_make_event(user_hash, tenant_id, _ts(now, day, random.randint(9, 16)), et, tgt,
                    {"after_hours": False, "is_question": is_q, "context_switches": 4}))
            elif day > 10:
                for _ in range(random.randint(2, 4)):
                    et = random.choices(["commit", "slack_message", "pr_review"], weights=[55, 30, 15])[0]
                    is_q = et == "slack_message" and random.random() > 0.5
                    events.append(_make_event(user_hash, tenant_id, _ts(now, day, random.randint(9, 17)), et,
                        random.choice(mentors) if mentors and is_q else None,
                        {"after_hours": False, "is_question": is_q, "context_switches": 3}))
            else:
                for _ in range(random.randint(3, 5)):
                    et = random.choices(["commit", "pr_review", "slack_message"], weights=[65, 20, 15])[0]
                    events.append(_make_event(user_hash, tenant_id, _ts(now, day, random.randint(9, 17)), et, None,
                        {"after_hours": False, "is_question": False, "context_switches": 2}))

    # ── Struggling (Emma): isolated, sparse collaboration ────────────────────
    elif email == "emma.wilson@techflow.io":
        for day in range(30, 0, -1):
            for _ in range(random.randint(3, 5)):
                et = random.choices(["commit", "slack_message", "pr_review"], weights=[60, 25, 15])[0]
                tgt = pick() if random.random() > 0.75 else None  # rarely collaborates
                events.append(_make_event(user_hash, tenant_id, _ts(now, day, random.randint(9, 18)), et, tgt,
                    {"after_hours": random.random() > 0.85, "context_switches": random.randint(2, 5),
                     "isolated": True}))

    # ── Contagion (David): negative sentiment in Slack, blocking ─────────────
    elif email == "david.park@techflow.io":
        neg_targets = [all_hashes.get(e) for e in (
            "marcus.johnson@techflow.io", "sarah.chen@techflow.io"
        ) if all_hashes.get(e)]
        for day in range(30, 0, -1):
            for _ in range(random.randint(2, 4)):
                et = random.choices(["slack_message", "commit", "pr_review"], weights=[55, 30, 15])[0]
                sentiment = round(random.uniform(-0.7, -0.3), 2) if et == "slack_message" else None
                tgt = random.choice(neg_targets) if neg_targets and et == "slack_message" and random.random() > 0.4 else None
                events.append(_make_event(user_hash, tenant_id, _ts(now, day, random.randint(9, 17)), et, tgt,
                    {"after_hours": False, "sentiment": sentiment,
                     "context_switches": random.randint(3, 7),
                     "topic": random.choice(["deadline", "scope_creep", "process", "priorities"])}))

    # ── Healthy Managers (Alex, Jamie): coordination-heavy ───────────────────
    elif email in ("alex.rivera@techflow.io", "manager2@techflow.io"):
        for day in range(30, 0, -1):
            for _ in range(random.randint(2, 4)):
                et = random.choices(
                    ["slack_message", "pr_review", "standup", "commit"],
                    weights=[40, 25, 25, 10]
                )[0]
                events.append(_make_event(user_hash, tenant_id, _ts(now, day, random.randint(9, 17)), et,
                    pick() if et != "commit" else None,
                    {"after_hours": False, "context_switches": random.randint(3, 6)}))

    # ── CTO (Chen): sparse, strategic ────────────────────────────────────────
    elif email == "admin@techflow.io":
        for day in range(30, 0, -1):
            if random.random() > 0.4:
                et = random.choice(["slack_message", "standup", "pr_review"])
                events.append(_make_event(user_hash, tenant_id, _ts(now, day, random.randint(9, 16)), et,
                    pick() if et != "standup" else None,
                    {"after_hours": False, "context_switches": random.randint(4, 8)}))

    # ── Healthy individual contributors (Lucas, Aisha) ────────────────────────
    else:
        for day in range(30, 0, -1):
            for _ in range(random.randint(2, 4)):
                et = random.choices(["slack_message", "commit", "pr_review"], weights=[45, 35, 20])[0]
                events.append(_make_event(user_hash, tenant_id, _ts(now, day, random.randint(9, 17)), et,
                    pick() if random.random() > 0.5 else None,
                    {"after_hours": False, "context_switches": random.randint(2, 4)}))

    return events


# ===========================================================================
# GRAPH EDGES
# ===========================================================================

def build_graph_edges(hashes, tenant_id, now):
    """Return GraphEdge ORM objects for the TechFlow collaboration network."""
    H = hashes
    edges = []

    def edge(src_email, tgt_email, weight, edge_type, days_ago=None):
        src = H.get(src_email)
        tgt = H.get(tgt_email)
        if not src or not tgt:
            return
        d = days_ago if days_ago is not None else random.randint(0, 3)
        edges.append(GraphEdge(
            tenant_id=tenant_id,
            source_hash=src,
            target_hash=tgt,
            weight=round(weight + random.uniform(-0.02, 0.02), 3),
            last_interaction=now - timedelta(days=d),
            edge_type=edge_type,
        ))

    # Collaboration (bidirectional)
    edge("alex.rivera@techflow.io",    "sarah.chen@techflow.io",    0.85, "collaboration")
    edge("sarah.chen@techflow.io",     "alex.rivera@techflow.io",   0.85, "collaboration")
    edge("alex.rivera@techflow.io",    "marcus.johnson@techflow.io",0.72, "collaboration")
    edge("marcus.johnson@techflow.io", "alex.rivera@techflow.io",   0.72, "collaboration")
    edge("alex.rivera@techflow.io",    "priya.sharma@techflow.io",  0.78, "collaboration")
    edge("priya.sharma@techflow.io",   "alex.rivera@techflow.io",   0.78, "collaboration")
    edge("alex.rivera@techflow.io",    "jordan.lee@techflow.io",    0.65, "collaboration")
    edge("jordan.lee@techflow.io",     "alex.rivera@techflow.io",   0.65, "collaboration")
    edge("alex.rivera@techflow.io",    "yuki.tanaka@techflow.io",   0.60, "collaboration")  # onboarding
    edge("yuki.tanaka@techflow.io",    "alex.rivera@techflow.io",   0.60, "collaboration")
    edge("alex.rivera@techflow.io",    "admin@techflow.io",         0.50, "collaboration")  # up-chain
    edge("priya.sharma@techflow.io",   "jordan.lee@techflow.io",    0.71, "collaboration")
    edge("jordan.lee@techflow.io",     "priya.sharma@techflow.io",  0.71, "collaboration")
    edge("marcus.johnson@techflow.io", "sarah.chen@techflow.io",    0.45, "collaboration")  # unblocks
    edge("marcus.johnson@techflow.io", "jordan.lee@techflow.io",    0.68, "collaboration")
    edge("marcus.johnson@techflow.io", "yuki.tanaka@techflow.io",   0.72, "collaboration")  # onboarding
    edge("priya.sharma@techflow.io",   "yuki.tanaka@techflow.io",   0.55, "collaboration")  # code reviews
    edge("lucas.martinez@techflow.io", "david.park@techflow.io",    0.61, "collaboration")
    edge("david.park@techflow.io",     "lucas.martinez@techflow.io",0.61, "collaboration")
    edge("aisha.patel@techflow.io",    "alex.rivera@techflow.io",   0.55, "collaboration")  # HR coord
    edge("alex.rivera@techflow.io",    "aisha.patel@techflow.io",   0.55, "collaboration")

    # Mentorship
    edge("priya.sharma@techflow.io",   "yuki.tanaka@techflow.io",   0.82, "mentorship")  # formal
    edge("marcus.johnson@techflow.io", "jordan.lee@techflow.io",    0.74, "mentorship")
    edge("alex.rivera@techflow.io",    "emma.wilson@techflow.io",   0.45, "mentorship")  # trying to help

    # Blocking (David is the bottleneck)
    edge("david.park@techflow.io", "marcus.johnson@techflow.io", 0.40, "blocking", days_ago=2)
    edge("david.park@techflow.io", "sarah.chen@techflow.io",     0.35, "blocking", days_ago=4)
    edge("david.park@techflow.io", "priya.sharma@techflow.io",   0.30, "blocking", days_ago=7)
    edge("david.park@techflow.io", "jordan.lee@techflow.io",     0.28, "blocking", days_ago=5)

    return edges


# ===========================================================================
# WORKFLOW EXECUTIONS
# ===========================================================================

def build_executions(hashes, tenant_id, now):
    """Return WorkflowExecution ORM objects for the 15-item demo history."""
    results = []

    def exe(execution_id, workflow_id, exec_type, template_id, email,
            user_message, status, days_ago, exec_ms,
            tools_used, integrations, result_summary,
            intent_ms, session_ms, llm_ms):
        uh = hashes.get(email)
        if not uh:
            return None
        started = now - timedelta(days=days_ago, hours=random.randint(6, 10))
        completed = started + timedelta(milliseconds=exec_ms)
        return WorkflowExecution(
            execution_id=execution_id,
            workflow_id=workflow_id,
            execution_type=exec_type,
            template_id=template_id,
            user_hash=uh,
            tenant_id=tenant_id,
            user_message=user_message,
            status=status,
            started_at=started,
            completed_at=completed,
            execution_time_ms=exec_ms,
            tools_used=tools_used,
            integrations_used=integrations,
            result_summary=result_summary,
            intent_classification_ms=intent_ms,
            session_creation_ms=session_ms,
            llm_execution_ms=llm_ms,
            created_at=started,
        )

    raw = [
        # exec_001: Sarah daily standup (burnout signal -- active 6:30am-11:45pm)
        exe("exec_001", "wf_001", "template", "tmpl_daily_standup", "sarah.chen@techflow.io",
            "Run my daily standup summary for the last 24 hours", "completed", 1, 2341,
            [{"tool": "github_list_commits",       "duration_ms": 820, "success": True, "result_count": 7},
             {"tool": "github_list_pull_requests", "duration_ms": 634, "success": True, "result_count": 3},
             {"tool": "slack_list_messages",        "duration_ms": 445, "success": True, "result_count": 28}],
            ["github", "slack"],
            "Yesterday: merged 2 PRs (auth-refactor, rate-limit-fix), 7 commits across 3 repos, 28 Slack messages sent. Active 6:30am-11:45pm. BLOCKER: API gateway PR blocked waiting for David's security review (3 days pending).",
            45, 312, 1564),

        # exec_002: Alex weekly email digest
        exe("exec_002", "wf_002", "template", "tmpl_weekly_email_digest", "alex.rivera@techflow.io",
            "Summarize my emails from this week and draft replies for anything urgent", "completed", 2, 3892,
            [{"tool": "gmail_list_emails",  "duration_ms": 1240, "success": True, "result_count": 47},
             {"tool": "gmail_get_thread",   "duration_ms": 891,  "success": True, "result_count": 8},
             {"tool": "gmail_create_draft", "duration_ms": 621,  "success": True, "result_count": 3}],
            ["gmail"],
            "47 emails reviewed. Priority: (1) Q3 budget approval from CFO -- URGENT, drafted reply requesting 2-week extension. (2) Sarah's PTO request -- drafted approval. (3) Vendor contract renewal -- flagged for legal review. 3 drafts created.",
            38, 298, 2356),

        # exec_003: Marcus GitHub issue triage (hidden gem unblocking others)
        exe("exec_003", "wf_003", "ad_hoc", None, "marcus.johnson@techflow.io",
            "Check all the GitHub issues I'm tagged in across our repos and summarize what needs my attention today", "completed", 1, 1876,
            [{"tool": "github_list_issues", "duration_ms": 734, "success": True, "result_count": 12},
             {"tool": "github_get_issue",   "duration_ms": 445, "success": True, "result_count": 5}],
            ["github"],
            "12 open issues with your mention. Top priority: (1) Issue #234 -- performance regression in search (blocking 2 teams, Yuki needs guidance). (2) Issue #241 -- auth token race condition (Sarah tagged you). (3) Issue #228 -- Redis connection pooling question from Jordan. Recommend addressing #234 first.",
            52, 287, 1112),

        # exec_004: Priya meeting prep
        exe("exec_004", "wf_004", "template", "tmpl_meeting_prep", "priya.sharma@techflow.io",
            "Prep brief for my architecture review meeting in 20 minutes", "completed", 3, 2654,
            [{"tool": "googlecalendar_get_events", "duration_ms": 421, "success": True, "result_count": 1},
             {"tool": "gmail_search_emails",       "duration_ms": 834, "success": True, "result_count": 14},
             {"tool": "slack_search_messages",     "duration_ms": 612, "success": True, "result_count": 23}],
            ["googlecalendar", "gmail", "slack"],
            "Architecture Review (45 min, 5 attendees). Context: Migrating from monolith to microservices (3 weeks of discussion). Key concerns from emails: David raised API versioning question (unanswered). Slack thread in #architecture shows team split on gRPC vs REST. Suggested questions: (1) How do we handle backwards compatibility? (2) Timeline for auth service extraction?",
            41, 301, 1791),

        # exec_005: Jordan slack catch-up
        exe("exec_005", "wf_005", "template", "tmpl_slack_catch_up", "jordan.lee@techflow.io",
            "Catch me up on what I missed on Slack while I was in a 4-hour focus block", "completed", 1, 1543,
            [{"tool": "slack_list_messages", "duration_ms": 623, "success": True, "result_count": 87},
             {"tool": "slack_get_thread",    "duration_ms": 412, "success": True, "result_count": 3}],
            ["slack"],
            "87 messages in 4 hours. Your @mentions: (1) Marcus asked you to review PR #189 -- needs response. (2) Alex asked for EOD status update. Key decisions made: team agreed to postpone v2.1 release by 1 week (blocker: Sarah's API changes). Action items: Review PR #189, send EOD update to Alex.",
            33, 278, 832),

        # exec_006: Alex team health report (this is the manager demo moment)
        exe("exec_006", "wf_006", "template", "tmpl_team_health_report", "alex.rivera@techflow.io",
            "Generate a team health report with 1:1 talking points for each engineer", "completed", 5, 4231,
            [{"tool": "slack_list_messages",       "duration_ms": 1234, "success": True, "result_count": 342},
             {"tool": "googlecalendar_get_events", "duration_ms": 678,  "success": True, "result_count": 28}],
            ["slack", "googlecalendar"],
            "Team Health Summary -- 6 engineers:\nSARAH: Working 14+ hour days, 71% after-hours activity. URGENT: burnout risk. 1:1 topic: reduce scope, take PTO.\nYUKI: New hire adjustment, asking 3x more questions this week vs last -- healthy learning curve.\nMARCUS: Quietly unblocking 5+ people/week, may be undervalued -- discuss recognition.\nPRIYA: Consistent, high-quality output. Consider for tech lead role.\nEMMA: Isolated from team Slack threads. 1:1 topic: inclusion and collaboration.\nJORDAN: Steady progress, good velocity.",
            67, 334, 2718),

        # exec_007: David sprint review
        exe("exec_007", "wf_007", "template", "tmpl_jira_sprint_review", "david.park@techflow.io",
            "Generate sprint review report for project TFLO", "completed", 7, 3124,
            [{"tool": "jira_get_sprint",  "duration_ms": 891,  "success": True, "result_count": 1},
             {"tool": "jira_list_issues", "duration_ms": 1234, "success": True, "result_count": 34}],
            ["jira"],
            "Sprint 14 Review -- TFLO:\nCompleted: 23/31 tickets (74%), 47/62 story points\nCarry-over: 8 tickets (auth module: 3, API gateway: 3, payments: 2)\nTop blocker: Security review process bottleneck (avg 3.2 days waiting)\nVelocity trend: DOWN 12% from Sprint 13\nRecommendation: Dedicate 1 engineer to unblocking security reviews next sprint.",
            48, 312, 1873),

        # exec_008: Sarah payments email research (overloaded, context-switching)
        exe("exec_008", "wf_008", "ad_hoc", None, "sarah.chen@techflow.io",
            "Find all emails from the last month about the payments integration and summarize key decisions and open questions", "completed", 3, 2891,
            [{"tool": "gmail_search_emails", "duration_ms": 1123, "success": True, "result_count": 31},
             {"tool": "gmail_get_thread",    "duration_ms": 891,  "success": True, "result_count": 9}],
            ["gmail"],
            "31 emails about payments integration (Oct 1 - Nov 1). Key decisions: (1) Chose Stripe over Braintree -- Nov 3 final call. (2) PCI compliance scope limited to SAQ-A. (3) Launch date set for Dec 15. Open questions: (1) How to handle failed payment retry logic? (2) Multi-currency support timeline? (3) Refund workflow approval matrix?",
            55, 298, 1618),

        # exec_009: Marcus PR review summary
        exe("exec_009", "wf_009", "template", "tmpl_github_pr_review", "marcus.johnson@techflow.io",
            "Summarize PRs waiting for my review across all our repos", "completed", 2, 2234,
            [{"tool": "github_list_pull_requests", "duration_ms": 934, "success": True, "result_count": 8},
             {"tool": "github_get_pull_request",   "duration_ms": 678, "success": True, "result_count": 8}],
            ["github"],
            "8 PRs awaiting your review:\nHIGH: PR #234 (Sarah) -- major auth refactor, 847 lines, blocks deploy. Review FIRST.\nMED: PR #238 (Jordan) -- Redis caching layer, 234 lines, good tests.\nMED: PR #241 (Yuki) -- login bug fix, 45 lines, easy review.\nLOW: PRs #242-245 -- docs and minor fixes. Estimated total review time: 3.5 hours.",
            44, 289, 1201),

        # exec_010: Priya creates Jira ticket + posts to Slack
        exe("exec_010", "wf_010", "ad_hoc", None, "priya.sharma@techflow.io",
            "Create a Jira ticket for the performance regression we discussed in today's standup and assign it to the right person", "completed", 1, 1654,
            [{"tool": "jira_create_issue",  "duration_ms": 834, "success": True, "result_count": 1},
             {"tool": "slack_send_message", "duration_ms": 412, "success": True, "result_count": 1}],
            ["jira", "slack"],
            "Created Jira ticket TFLO-289: 'Performance regression in /search endpoint -- P1'. Assigned to Marcus Johnson (search service owner). Story points: 5. Sprint: Current. Also posted to #engineering: 'TFLO-289 created for the search perf regression. Marcus taking point. Tracking in current sprint.'",
            38, 291, 925),

        # exec_011: Sarah daily standup day 2 (still blocked by David)
        exe("exec_011", "wf_011", "template", "tmpl_daily_standup", "sarah.chen@techflow.io",
            "Daily standup summary", "completed", 2, 2187,
            [{"tool": "github_list_commits",       "duration_ms": 712, "success": True, "result_count": 9},
             {"tool": "github_list_pull_requests", "duration_ms": 534, "success": True, "result_count": 4},
             {"tool": "slack_list_messages",        "duration_ms": 412, "success": True, "result_count": 34}],
            ["github", "slack"],
            "Yesterday: 9 commits (auth service), 4 PRs open (2 waiting review), 34 Slack messages. Active 7:15am-11:30pm. BLOCKER: Waiting on David's approval for security architecture decision (day 4 of wait).",
            41, 298, 1348),

        # exec_012: Yuki onboarding doc organization (new hire day 5)
        exe("exec_012", "wf_012", "ad_hoc", None, "yuki.tanaka@techflow.io",
            "Find all the onboarding documentation and Notion pages I should read as a new engineer and organize them by priority", "completed", 25, 3421,
            [{"tool": "notion_search_pages", "duration_ms": 1234, "success": True, "result_count": 23},
             {"tool": "slack_list_messages", "duration_ms": 891,  "success": True, "result_count": 15}],
            ["notion", "slack"],
            "Found 23 relevant pages. Priority order:\nMUST READ: Engineering Handbook, Dev Environment Setup, Code Review Process, Deployment Runbook\nWEEK 2: Architecture Overview, API Design Guide, Security Practices\nLATER: Historical ADRs, Product Roadmap, Team Norms\nEstimated reading time: 6.5 hours total.",
            62, 318, 1941),

        # exec_013: Alex deployment freeze announcement
        exe("exec_013", "wf_013", "ad_hoc", None, "alex.rivera@techflow.io",
            "Send a message to the engineering team on Slack about the new deployment freeze this Friday and what they need to do before then", "completed", 4, 1234,
            [{"tool": "slack_send_message",         "duration_ms": 412, "success": True, "result_count": 1},
             {"tool": "googlecalendar_create_event","duration_ms": 523, "success": True, "result_count": 1}],
            ["slack", "googlecalendar"],
            "Posted to #engineering: 'Deployment Freeze: Friday 6pm through Monday 9am. Please merge all in-flight PRs by Thursday EOD. If you have critical changes, flag in #deployments.' Also created calendar event 'Deployment Freeze' (Fri 6pm - Mon 9am) and added team members.",
            35, 278, 421),

        # exec_014: Aisha extracts action items from Notion
        exe("exec_014", "wf_014", "template", "tmpl_notion_weekly_notes", "aisha.patel@techflow.io",
            "Extract action items from this week's all-hands and 1:1 notes in Notion", "completed", 5, 2876,
            [{"tool": "notion_get_page",       "duration_ms": 1123, "success": True, "result_count": 4},
             {"tool": "notion_query_database", "duration_ms": 678,  "success": True, "result_count": 12}],
            ["notion"],
            "12 action items extracted from 4 pages:\n- Alex (owner): Revise PTO policy doc -- Due: Nov 15\n- Aisha (owner): Schedule sensitivity training -- Due: Nov 20\n- Chen (owner): Approve Q4 headcount -- Due: Nov 10 OVERDUE\n- Sarah (owner): Submit architecture proposal -- Due: Nov 18\n- Team: Review and sign updated team norms -- Due: Nov 22",
            49, 301, 1703),

        # exec_015: Priya Slack catch-up after vacation
        exe("exec_015", "wf_015", "template", "tmpl_slack_catch_up", "priya.sharma@techflow.io",
            "Catch me up on Slack -- I was on vacation for 5 days", "completed", 8, 3782,
            [{"tool": "slack_list_messages", "duration_ms": 1567, "success": True, "result_count": 423},
             {"tool": "slack_get_thread",    "duration_ms": 891,  "success": True, "result_count": 12}],
            ["slack"],
            "423 messages while you were away. Critical: (1) Architecture decision made in your absence -- microservices migration approved, you're leading payments service. (2) 3 @mentions requesting PR reviews (still open). (3) Alex announced new sprint process -- daily async standups in Slack. Key decisions needing your input: API versioning strategy (vote ends tomorrow).",
            58, 312, 2312),
    ]
    return [x for x in raw if x is not None]


# ===========================================================================
# NOTIFICATIONS
# ===========================================================================

def build_notifications(hashes, tenant_id, now):
    """Return rich Notification ORM objects spread across key personas."""
    notes = []

    def notif(email, ntype, title, message, priority, days_ago, read=False):
        uh = hashes.get(email)
        if not uh:
            return None
        created = now - timedelta(days=days_ago, hours=random.randint(0, 5))
        read_at = (created + timedelta(hours=random.randint(1, 8))) if read else None
        return Notification(
            id=uuid4(),
            user_hash=uh,
            tenant_id=tenant_id,
            type=ntype,
            title=title,
            message=message,
            priority=priority,
            read_at=read_at,
            created_at=created,
        )

    raw = [
        # Risk alerts
        notif("alex.rivera@techflow.io", "activity", "Critical Risk Alert: Sarah Chen",
              "Sarah Chen has entered a critical burnout risk zone. Velocity: 94.2. Recommended action: Schedule 1:1 immediately and consider workload reduction.",
              "critical", 1, read=False),
        notif("alex.rivera@techflow.io", "activity", "Elevated Risk Detected: Emma Wilson",
              "Emma Wilson is showing elevated risk signals. Belongingness score has dropped to 0.25. Consider a check-in.",
              "high", 3, read=True),
        notif("alex.rivera@techflow.io", "activity", "Elevated Risk: Yuki Tanaka -- Learning Curve",
              "Yuki Tanaka's velocity spiked to ELEVATED. This pattern is consistent with new hire learning curve adjustment, not true burnout. Monitor for 1 more week before intervening.",
              "normal", 5, read=True),
        notif("admin@techflow.io", "activity", "Critical Risk Alert: Sarah Chen",
              "URGENT: Sarah Chen has been in CRITICAL risk zone for 5 consecutive days. Velocity 94.2, belongingness 0.18. Immediate manager escalation recommended.",
              "critical", 0, read=False),
        notif("admin@techflow.io", "activity", "Contagion Risk Detected: David Park",
              "David Park's negative sentiment pattern is spreading to 4 adjacent team members. Slack sentiment scores indicate chronic negativity. People Ops review recommended.",
              "high", 2, read=False),
        # Hidden gem
        notif("alex.rivera@techflow.io", "activity", "Hidden Gem Identified: Marcus Johnson",
              "Marcus Johnson has been identified as a hidden gem -- unblocking 47 colleagues per quarter but receiving little public recognition. Consider public acknowledgement and a role title review.",
              "high", 4, read=False),
        notif("admin@techflow.io", "activity", "Hidden Gem: Marcus Johnson -- Betweenness Centrality 0.89",
              "Network analysis shows Marcus Johnson is the top connector on the engineering team, bridging 89% of cross-team communication. His departure would fragment 3 critical knowledge flows.",
              "high", 6, read=True),
        # Onboarding
        notif("yuki.tanaka@techflow.io", "system", "Welcome to AlgoQuest, Yuki!",
              "Your account is active. Your onboarding journey has started. Marcus Johnson and Priya Sharma have been assigned as your mentors. Check in with them this week.",
              "normal", 30, read=True),
        notif("yuki.tanaka@techflow.io", "team", "New team member resources",
              "We found 23 onboarding docs for you. Priority reading: Engineering Handbook, Dev Environment Setup, Code Review Process. Estimated reading time: 6.5 hours.",
              "normal", 28, read=True),
        # Team updates
        notif("alex.rivera@techflow.io", "team", "New team member: Yuki Tanaka",
              "Yuki Tanaka has joined your team as Junior Engineer. Their onboarding journey has begun. Check in within the first week.",
              "normal", 30, read=True),
        notif("priya.sharma@techflow.io", "team", "You have been assigned as a mentor",
              "You have been assigned as a formal mentor for Yuki Tanaka. Suggested first check-in: within 3 days. Mentorship guide in the team wiki.",
              "normal", 29, read=True),
        notif("marcus.johnson@techflow.io", "team", "Mentorship assignment: Yuki Tanaka",
              "You have been assigned as an informal mentor for Yuki Tanaka. Network analysis shows you're the best connector for their onboarding. Your unblocking track record: 47/quarter.",
              "normal", 29, read=True),
        # Workflow completions
        notif("alex.rivera@techflow.io", "activity", "Workflow completed: Team Health Report",
              "Your workflow 'Team Health Report' completed in 4.2s. 6 engineers analyzed, 3 action items flagged.",
              "normal", 5, read=True),
        notif("sarah.chen@techflow.io", "activity", "Workflow completed: Daily Standup Summary",
              "Your workflow 'Daily Standup Summary' completed in 2.3s. 7 commits, 3 PRs, 28 Slack messages summarized.",
              "normal", 1, read=False),
        notif("priya.sharma@techflow.io", "activity", "Workflow completed: Meeting Preparation Brief",
              "Your workflow 'Meeting Preparation Brief' completed in 2.7s. Architecture review context ready: 14 emails, 23 Slack threads surfaced.",
              "normal", 3, read=True),
        # Security alerts
        notif("admin@techflow.io", "security", "Security: Unusual access pattern detected",
              "An unusual data access pattern was detected for David Park -- accessed 47 employee records outside normal working hours at 2:14am. Review in the audit log.",
              "critical", 8, read=True),
        notif("sarah.chen@techflow.io", "security", "New sign-in to your account",
              "A new sign-in was detected from MacBook Pro in San Francisco, CA. If this wasn't you, contact your admin immediately.",
              "high", 15, read=True),
        # Weekly insights
        notif("alex.rivera@techflow.io", "team", "Weekly Team Insights -- Week of Nov 4",
              "Your weekly team health summary is ready. 2 alerts, 2 highlights. Top priority: Sarah Chen burnout risk escalated to CRITICAL.",
              "high", 7, read=True),
        notif("admin@techflow.io", "team", "Weekly Team Insights -- Week of Nov 4",
              "Company-wide health summary ready. 3 critical alerts, 1 hidden gem detected, 1 contagion risk. Review in dashboard.",
              "high", 7, read=True),
        # Auth welcome
        notif("emma.wilson@techflow.io", "auth", "Welcome to AlgoQuest",
              "Your account has been set up. Explore your personal wellbeing dashboard to understand your work patterns.",
              "normal", 45, read=True),
        notif("david.park@techflow.io", "auth", "Welcome to AlgoQuest",
              "Your account has been set up. Use the workflow agent to automate your daily Jira sprint reviews and Slack summaries.",
              "normal", 60, read=True),
    ]
    return [x for x in raw if x is not None]


# ===========================================================================
# AUDIT LOGS
# ===========================================================================

def build_audit_logs(hashes, now):
    """Return AuditLog ORM objects for a realistic activity trail."""
    logs = []
    entries = [
        ("admin@techflow.io",          "auth:login",          {"method": "google_sso", "ip": "10.0.1.50",   "device": "MacBook Pro"}),
        ("admin@techflow.io",          "data:dashboard_view", {"section": "risk_overview", "records_viewed": 12}),
        ("admin@techflow.io",          "data:export",         {"format": "csv", "records": 12, "section": "team_health"}),
        ("alex.rivera@techflow.io",    "auth:login",          {"method": "email",      "ip": "192.168.1.10", "device": "Chrome/macOS"}),
        ("alex.rivera@techflow.io",    "data:employee_view",  {"target": "sarah.chen", "section": "risk_detail"}),
        ("alex.rivera@techflow.io",    "nudge:sent",          {"target": "sarah.chen@techflow.io", "type": "burnout_checkin"}),
        ("sarah.chen@techflow.io",     "auth:login",          {"method": "email",      "ip": "192.168.1.22", "device": "Chrome/macOS"}),
        ("sarah.chen@techflow.io",     "consent:updated",     {"field": "consent_share_with_manager", "value": True}),
        ("marcus.johnson@techflow.io", "auth:login",          {"method": "google_sso", "ip": "192.168.1.31", "device": "Safari/macOS"}),
        ("priya.sharma@techflow.io",   "auth:login",          {"method": "google_sso", "ip": "192.168.1.45", "device": "Chrome/macOS"}),
        ("priya.sharma@techflow.io",   "workflow:executed",   {"execution_id": "exec_004", "template": "tmpl_meeting_prep"}),
        ("yuki.tanaka@techflow.io",    "auth:login",          {"method": "email",      "ip": "10.0.2.88",    "device": "Chrome/Windows"}),
        ("yuki.tanaka@techflow.io",    "onboarding:step",     {"step": "dev_environment_setup", "completed": True}),
        ("emma.wilson@techflow.io",    "auth:login",          {"method": "email",      "ip": "192.168.1.61", "device": "Firefox/Ubuntu"}),
        ("david.park@techflow.io",     "auth:login",          {"method": "email",      "ip": "192.168.1.72", "device": "Chrome/macOS"}),
        ("david.park@techflow.io",     "data:dashboard_view", {"section": "team_graph", "records_viewed": 47, "hour": 2}),
        ("aisha.patel@techflow.io",    "auth:login",          {"method": "google_sso", "ip": "10.0.1.91",    "device": "Chrome/macOS"}),
        ("aisha.patel@techflow.io",    "workflow:executed",   {"execution_id": "exec_014", "template": "tmpl_notion_weekly_notes"}),
    ]
    for email, action, details in entries:
        uh = hashes.get(email)
        if not uh:
            continue
        logs.append(AuditLog(
            user_hash=uh,
            action=action,
            details=details,
            timestamp=now - timedelta(days=random.randint(0, 14), hours=random.randint(0, 23)),
        ))
    return logs


# ===========================================================================
# RESET HELPER
# ===========================================================================

def _reset(db):
    """Delete all TechFlow demo data so we can re-seed cleanly."""
    log.info("  Resetting existing TechFlow demo data...")
    tenant = db.query(Tenant).filter_by(slug=TENANT_SLUG).first()
    if not tenant:
        log.info("  Nothing to reset.")
        return
    tid = tenant.id

    # Gather user hashes before deleting membership records
    member_hashes = [m.user_hash for m in db.query(TenantMember).filter_by(tenant_id=tid).all()]

    # Analytics schema
    db.query(Event).filter_by(tenant_id=tid).delete(synchronize_session=False)
    db.query(RiskScore).filter_by(tenant_id=tid).delete(synchronize_session=False)
    db.query(RiskHistory).filter_by(tenant_id=tid).delete(synchronize_session=False)
    db.query(GraphEdge).filter_by(tenant_id=tid).delete(synchronize_session=False)
    db.query(CentralityScore).filter_by(tenant_id=tid).delete(synchronize_session=False)
    db.query(SkillProfile).filter_by(tenant_id=tid).delete(synchronize_session=False)

    # Workflow executions (keyed on user_hash, no direct tenant FK)
    for uh in member_hashes:
        db.query(WorkflowExecution).filter_by(user_hash=uh).delete(synchronize_session=False)

    # Workflow templates seeded by us
    seeded_ids = [t["template_id"] for t in WORKFLOW_TEMPLATES]
    db.query(WorkflowTemplate).filter(
        WorkflowTemplate.template_id.in_(seeded_ids)
    ).delete(synchronize_session=False)

    # Notification templates
    seeded_types = [t["type"] for t in NOTIFICATION_TEMPLATES]
    db.query(NotificationTemplate).filter(
        NotificationTemplate.type.in_(seeded_types)
    ).delete(synchronize_session=False)

    # Identity schema
    db.query(Notification).filter_by(tenant_id=tid).delete(synchronize_session=False)
    for uh in member_hashes:
        db.query(NotificationPreference).filter_by(user_hash=uh).delete(synchronize_session=False)
        db.query(AuditLog).filter_by(user_hash=uh).delete(synchronize_session=False)

    db.query(TenantMember).filter_by(tenant_id=tid).delete(synchronize_session=False)
    for uh in member_hashes:
        db.query(UserIdentity).filter_by(user_hash=uh).delete(synchronize_session=False)
    db.query(Tenant).filter_by(id=tid).delete(synchronize_session=False)
    db.flush()
    log.info("  Reset complete.")


# ===========================================================================
# MAIN SEED FUNCTION
# ===========================================================================

def seed():
    log.info("")
    log.info("=" * 65)
    log.info("  AlgoQuest -- Master Demo Seed")
    log.info("  Company: TechFlow Inc  |  Plan: enterprise")
    log.info("=" * 65)

    _ensure_tables()

    reset_mode = "--reset" in sys.argv
    db = SessionLocal()
    now = datetime.utcnow()

    try:
        # Idempotency check
        existing = db.query(Tenant).filter_by(slug=TENANT_SLUG).first()
        if existing and not reset_mode:
            # Check if seed completed fully (users exist) or was partial
            member_count = db.query(TenantMember).filter_by(tenant_id=existing.id).count()
            if member_count >= 12:
                log.info("")
                log.info("  Already seeded. Tenant 'techflow-inc' exists with full data.")
                log.info("  Run with --reset to wipe and re-seed.")
                log.info("")
                return
            else:
                log.info(f"  Partial seed detected ({member_count} members). Resetting and re-seeding...")
                _reset(db)
                db.commit()
                existing = None

        if reset_mode and existing:
            _reset(db)
            db.commit()

        # Step 1: Tenant
        tenant = Tenant(
            id=uuid4(),
            name=TENANT_NAME,
            slug=TENANT_SLUG,
            plan=TENANT_PLAN,
            status="active",
            settings={
                "domain": TENANT_DOMAIN,
                "timezone": "America/Los_Angeles",
                "features": {
                    "sso": True, "mfa": True, "audit_log": True,
                    "workflow_agent": True, "risk_alerts": True,
                },
                "industry": "B2B SaaS",
                "team_size": 12,
            },
        )
        db.add(tenant)
        db.flush()
        log.info(f"\n  Created tenant: {TENANT_NAME} (id={str(tenant.id)[:8]}...)")

        # Pre-compute all user hashes so manager references resolve correctly
        hashes = {p["email"]: privacy.hash_identity(p["email"]) for p in CAST}

        # Step 2: UserIdentity + TenantMember + NotificationPreferences
        for person in CAST:
            uh = hashes[person["email"]]
            joined_days_ago = 30 if person["persona"] == "new_hire" else 60

            db.add(UserIdentity(
                user_hash=uh,
                tenant_id=tenant.id,
                email_encrypted=privacy.encrypt(person["email"]),
                slack_id_encrypted=privacy.encrypt(person["slack_id"]),
                role=person["role"],
                consent_share_with_manager=(person["role"] != "admin"),
                consent_share_anonymized=True,
                created_at=now - timedelta(days=joined_days_ago),
            ))

            tenant_role = "owner" if person["role"] == "admin" else "member"
            db.add(TenantMember(
                id=uuid4(),
                tenant_id=tenant.id,
                user_hash=uh,
                role=tenant_role,
                invited_by=hashes.get("admin@techflow.io"),
                joined_at=now - timedelta(days=joined_days_ago),
            ))

            for channel in ("in_app", "email"):
                for ntype in ("auth", "team", "system", "security", "activity"):
                    db.add(NotificationPreference(
                        id=uuid4(),
                        user_hash=uh,
                        channel=channel,
                        notification_type=ntype,
                        enabled=True,
                    ))

        db.flush()
        log.info(f"  Created {len(CAST)} users with identity + tenant memberships + notification prefs")

        # Step 3: Current risk score snapshots
        for email, spec in RISK_SPECS.items():
            uh = hashes.get(email)
            if not uh:
                continue
            db.add(RiskScore(
                user_hash=uh,
                tenant_id=tenant.id,
                velocity=spec["velocity"],
                risk_level=spec["risk_level"],
                confidence=spec["confidence"],
                thwarted_belongingness=spec["belongingness"],
                updated_at=now,
            ))
        db.flush()
        log.info(f"  Created {len(RISK_SPECS)} current risk score snapshots")

        # Step 4: 30-day risk history per user
        history_count = 0
        for person in CAST:
            records = _risk_history_for(person["email"], hashes[person["email"]], tenant.id, now)
            for r in records:
                db.add(r)
            history_count += len(records)
        db.flush()
        log.info(f"  Created {history_count} risk history records (30-day trajectories)")

        # Step 5: Skill profiles
        for email, skills in SKILL_SPECS.items():
            uh = hashes.get(email)
            if not uh:
                continue
            db.add(SkillProfile(
                user_hash=uh,
                tenant_id=tenant.id,
                technical=float(skills["technical"]),
                communication=float(skills["communication"]),
                leadership=float(skills["leadership"]),
                collaboration=float(skills["collaboration"]),
                adaptability=float(skills["adaptability"]),
                creativity=float(skills["creativity"]),
                updated_at=now,
            ))
        db.flush()
        log.info(f"  Created {len(SKILL_SPECS)} skill profiles")

        # Step 6: Centrality scores
        for email, c in CENTRALITY_SPECS.items():
            uh = hashes.get(email)
            if not uh:
                continue
            db.add(CentralityScore(
                user_hash=uh,
                tenant_id=tenant.id,
                betweenness=c["betweenness"],
                eigenvector=c["eigenvector"],
                unblocking_count=c["unblocking_count"],
                knowledge_transfer_score=c["knowledge_transfer"],
                calculated_at=now,
            ))
        db.flush()
        log.info(f"  Created {len(CENTRALITY_SPECS)} centrality scores")

        # Step 7: Behavioral events
        event_count = 0
        for person in CAST:
            evts = generate_events(person["email"], hashes[person["email"]], tenant.id, now, hashes)
            for e in evts:
                db.add(e)
            event_count += len(evts)
        db.flush()
        log.info(f"  Created {event_count} behavioral events across 30 days")

        # Step 8: Collaboration graph edges
        edges = build_graph_edges(hashes, tenant.id, now)
        for e in edges:
            db.add(e)
        db.flush()
        log.info(f"  Created {len(edges)} graph edges (collaboration / mentorship / blocking)")

        # Step 9: Workflow templates (idempotent by template_id)
        tmpl_count = 0
        for tmpl in WORKFLOW_TEMPLATES:
            if not db.query(WorkflowTemplate).filter_by(template_id=tmpl["template_id"]).first():
                db.add(WorkflowTemplate(
                    template_id=tmpl["template_id"],
                    name=tmpl["name"],
                    description=tmpl["description"],
                    category=tmpl["category"],
                    icon=tmpl["icon"],
                    prompt_template=tmpl["prompt_template"],
                    required_integrations=tmpl["required_integrations"],
                    optional_integrations=tmpl.get("optional_integrations", []),
                    parameters=tmpl.get("parameters", []),
                    is_public=tmpl["is_public"],
                    is_system=tmpl["is_system"],
                    usage_count=tmpl["usage_count"],
                    last_used_at=now - timedelta(days=random.randint(0, 3)),
                    created_at=now - timedelta(days=90),
                    updated_at=now,
                ))
                tmpl_count += 1
        db.flush()
        log.info(f"  Created {tmpl_count} workflow templates ({len(WORKFLOW_TEMPLATES) - tmpl_count} already existed)")

        # Step 10: Workflow execution history
        executions = build_executions(hashes, tenant.id, now)
        exec_count = 0
        for ex in executions:
            if not db.query(WorkflowExecution).filter_by(execution_id=ex.execution_id).first():
                db.add(ex)
                exec_count += 1
        db.flush()
        log.info(f"  Created {exec_count} workflow execution records")

        # Step 11: Notification templates (idempotent by type)
        ntmpl_count = 0
        for nt in NOTIFICATION_TEMPLATES:
            if not db.query(NotificationTemplate).filter_by(type=nt["type"]).first():
                db.add(NotificationTemplate(
                    id=uuid4(),
                    type=nt["type"],
                    subject=nt["subject"],
                    body_template=nt["body_template"],
                    variables=nt["variables"],
                ))
                ntmpl_count += 1
        db.flush()
        log.info(f"  Created {ntmpl_count} notification templates")

        # Step 12: Notifications
        notifications = build_notifications(hashes, tenant.id, now)
        for n in notifications:
            db.add(n)
        db.flush()
        log.info(f"  Created {len(notifications)} notifications across key personas")

        # Step 13: Audit logs
        audit_logs = build_audit_logs(hashes, now)
        for al in audit_logs:
            db.add(al)
        db.flush()
        log.info(f"  Created {len(audit_logs)} audit log entries")

        # Commit everything atomically
        db.commit()

        # Final summary
        log.info("")
        log.info("=" * 65)
        log.info("  SEED COMPLETE")
        log.info("=" * 65)
        log.info(f"  Tenant : {TENANT_NAME} ({TENANT_SLUG})")
        log.info(f"  Plan   : {TENANT_PLAN}")
        log.info(f"  Users  : {len(CAST)}")
        log.info("")
        log.info("  Demo Credentials (password: Demo123!):")
        log.info(f"  {'Role':>10} | {'Email':>35} | Persona")
        log.info(f"  {'-'*10}-+-{'-'*35}-+-{'-'*22}")
        for p in CAST:
            log.info(f"  {p['role']:>10} | {p['email']:>35} | {p['persona']}")
        log.info("")
        log.info("  Key Demo Stories:")
        log.info("  sarah.chen         --> BURNOUT       velocity=94.2, belongingness=0.18, risk=CRITICAL")
        log.info("  marcus.johnson     --> HIDDEN GEM    betweenness=0.89, unblocking=47/qtr")
        log.info("  priya.sharma       --> HIGH PERFORM  technical=94, knowledge_transfer=0.93")
        log.info("  yuki.tanaka        --> NEW HIRE      30 days in, learning curve ELEVATED spike")
        log.info("  emma.wilson        --> STRUGGLING    isolated, belongingness=0.25, collaboration=31")
        log.info("  david.park         --> CONTAGION     blocking 4 people, negative Slack sentiment")
        log.info("=" * 65)
        log.info("")

    except Exception as exc:
        log.error(f"\n  Seed FAILED: {exc}")
        db.rollback()
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()


# ===========================================================================
# ENTRY POINT
# ===========================================================================

if __name__ == "__main__":
    seed()
