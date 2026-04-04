"""
WorkflowIntentParser -- detects actionable workflow intents in chat queries.

Maps natural language to workflow actions that require confirmation before execution.

Actions prefixed with ``tool_`` represent external tool requests (email, calendar,
Slack, GitHub) routed through Composio. The frontend uses this prefix to render
tool-specific UI cards.
"""

import re
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("sentinel.workflow_intent")

_ALL_ROLES: tuple[str, ...] = ("employee", "manager", "admin")


@dataclass(frozen=True)
class WorkflowIntent:
    """A detected workflow action intent."""

    action: str  # e.g. "pause_monitoring", "tool_email_read"
    description: str  # Human-readable description
    requires_confirmation: bool
    allowed_roles: tuple[str, ...]
    tool_name: str = ""  # Non-empty for tool_ actions (e.g. "email", "calendar")


# ── Internal Sentinel workflow patterns ──────────────────────────────────────

_INTERNAL_WORKFLOW_PATTERNS: list[tuple[re.Pattern, WorkflowIntent]] = [
    (
        re.compile(
            r"\b(pause|stop|disable)\b.*\b(monitor|tracking|surveillance)\b", re.I
        ),
        WorkflowIntent(
            action="pause_monitoring",
            description="Pause wellbeing monitoring for your account",
            requires_confirmation=True,
            allowed_roles=_ALL_ROLES,
        ),
    ),
    (
        re.compile(
            r"\b(resume|start|enable|unpause)\b.*\b(monitor|tracking)\b", re.I
        ),
        WorkflowIntent(
            action="resume_monitoring",
            description="Resume wellbeing monitoring for your account",
            requires_confirmation=True,
            allowed_roles=_ALL_ROLES,
        ),
    ),
    (
        re.compile(
            r"\b(reveal|unmask|show|identify)\b.*\b(identity|name|who)\b.*\b(critical|risk)\b",
            re.I,
        ),
        WorkflowIntent(
            action="reveal_identity",
            description=(
                "Reveal identity of a critical-risk team member "
                "(requires 36h critical threshold)"
            ),
            requires_confirmation=True,
            allowed_roles=("manager", "admin"),
        ),
    ),
    (
        re.compile(r"\b(export|download)\b.*\b(data|report|csv)\b", re.I),
        WorkflowIntent(
            action="export_data",
            description="Export data or generate a report",
            requires_confirmation=True,
            allowed_roles=("manager", "admin"),
        ),
    ),
    (
        re.compile(r"\b(update|change)\b.*\b(consent|privacy|sharing)\b", re.I),
        WorkflowIntent(
            action="update_consent",
            description="Update your data sharing preferences",
            requires_confirmation=True,
            allowed_roles=_ALL_ROLES,
        ),
    ),
]

# ── External tool request patterns ───────────────────────────────────────────
# Read operations -> requires_confirmation=False
# Write operations -> requires_confirmation=True

_TOOL_WORKFLOW_PATTERNS: list[tuple[re.Pattern, WorkflowIntent]] = [
    # ── Email (read) ─────────────────────────────────────────────────────────
    (
        re.compile(
            r"\b(check|read|show|get|open|view|see|look at|any new)\b.*\b(email|emails|mail|inbox|messages?)\b",
            re.I,
        ),
        WorkflowIntent(
            action="tool_email_read",
            description="Check your email inbox for recent messages",
            requires_confirmation=False,
            allowed_roles=_ALL_ROLES,
            tool_name="email",
        ),
    ),
    # ── Email (write) ────────────────────────────────────────────────────────
    (
        re.compile(
            r"\b(send|compose|write|draft|reply|forward)\b.*\b(email|mail|message)\b",
            re.I,
        ),
        WorkflowIntent(
            action="tool_email_send",
            description="Compose and send an email",
            requires_confirmation=True,
            allowed_roles=_ALL_ROLES,
            tool_name="email",
        ),
    ),
    # ── Calendar (read) ──────────────────────────────────────────────────────
    (
        re.compile(
            r"\b(check|show|get|view|see|what|list|any)\b.*\b(calendar|schedule|meeting|meetings|event|events|appointment|appointments)\b",
            re.I,
        ),
        WorkflowIntent(
            action="tool_calendar_read",
            description="Check your calendar for upcoming events and meetings",
            requires_confirmation=False,
            allowed_roles=_ALL_ROLES,
            tool_name="calendar",
        ),
    ),
    # ── Calendar (write) ─────────────────────────────────────────────────────
    (
        re.compile(
            r"\b(schedule|create|book|set up|add|cancel|reschedule)\b.*\b(meeting|event|appointment|call|sync)\b",
            re.I,
        ),
        WorkflowIntent(
            action="tool_calendar_write",
            description="Create or modify a calendar event",
            requires_confirmation=True,
            allowed_roles=_ALL_ROLES,
            tool_name="calendar",
        ),
    ),
    # ── Slack (read) ─────────────────────────────────────────────────────────
    (
        re.compile(
            r"\b(check|read|show|get|view|see|any new)\b.*\b(slack|slack messages?|channels?|DMs?)\b",
            re.I,
        ),
        WorkflowIntent(
            action="tool_slack_read",
            description="Check your Slack messages and channels",
            requires_confirmation=False,
            allowed_roles=_ALL_ROLES,
            tool_name="slack",
        ),
    ),
    # ── Slack (write) ────────────────────────────────────────────────────────
    (
        re.compile(
            r"\b(send|post|write|message)\b.*\b(slack|channel|DM)\b",
            re.I,
        ),
        WorkflowIntent(
            action="tool_slack_send",
            description="Send a Slack message",
            requires_confirmation=True,
            allowed_roles=_ALL_ROLES,
            tool_name="slack",
        ),
    ),
    # ── GitHub (read) ────────────────────────────────────────────────────────
    (
        re.compile(
            r"\b(check|show|get|view|see|list|any|review)\b.*\b(PRs?|pull requests?|issues?|commits?|repos?|repositories)\b",
            re.I,
        ),
        WorkflowIntent(
            action="tool_github_read",
            description="Check your GitHub activity (PRs, issues, commits)",
            requires_confirmation=False,
            allowed_roles=_ALL_ROLES,
            tool_name="github",
        ),
    ),
    # ── GitHub (write) ───────────────────────────────────────────────────────
    (
        re.compile(
            r"\b(create|open|submit|close|merge)\b.*\b(PR|pull request|issue|branch)\b",
            re.I,
        ),
        WorkflowIntent(
            action="tool_github_write",
            description="Perform a GitHub action (create PR, open issue, etc.)",
            requires_confirmation=True,
            allowed_roles=_ALL_ROLES,
            tool_name="github",
        ),
    ),
    # ── Tool management ──────────────────────────────────────────────────────
    (
        re.compile(
            r"\b(connect|link|set up|configure|add)\b.*\b(tool|integration|app|service|email|slack|github|calendar)\b",
            re.I,
        ),
        WorkflowIntent(
            action="tool_connect",
            description="Connect a new tool or integration to your account",
            requires_confirmation=True,
            allowed_roles=_ALL_ROLES,
            tool_name="integrations",
        ),
    ),
    (
        re.compile(
            r"\b(what|which|list|show)\b.*\b(tools?|integrations?|apps?|connected)\b",
            re.I,
        ),
        WorkflowIntent(
            action="tool_list_connected",
            description="List your currently connected tools and integrations",
            requires_confirmation=False,
            allowed_roles=_ALL_ROLES,
            tool_name="integrations",
        ),
    ),
    (
        re.compile(
            r"\b(disconnect|remove|unlink)\b.*\b(tool|integration|app|service|email|slack|github|calendar)\b",
            re.I,
        ),
        WorkflowIntent(
            action="tool_disconnect",
            description="Disconnect a tool or integration from your account",
            requires_confirmation=True,
            allowed_roles=_ALL_ROLES,
            tool_name="integrations",
        ),
    ),
]

# Combined list: internal workflows checked first, then tool patterns
WORKFLOW_PATTERNS: list[tuple[re.Pattern, WorkflowIntent]] = (
    _INTERNAL_WORKFLOW_PATTERNS + _TOOL_WORKFLOW_PATTERNS
)


class WorkflowIntentParser:
    """Detects workflow intents from chat messages."""

    def parse(self, query: str, role: str) -> Optional[WorkflowIntent]:
        """Match query against workflow patterns.

        Returns the first matching ``WorkflowIntent`` whose ``allowed_roles``
        includes *role*, or ``None`` if no match or the role is not permitted.
        """
        for pattern, intent in WORKFLOW_PATTERNS:
            if pattern.search(query):
                if role in intent.allowed_roles:
                    logger.info(
                        "Workflow intent detected: %s for role=%s",
                        intent.action,
                        role,
                    )
                    return intent
                logger.info(
                    "Workflow intent %s matched but role=%s not allowed",
                    intent.action,
                    role,
                )
                continue
        return None
