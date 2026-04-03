"""
WorkflowIntentParser -- detects actionable workflow intents in chat queries.

Maps natural language to workflow actions that require confirmation before execution.
"""

import re
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("sentinel.workflow_intent")


@dataclass(frozen=True)
class WorkflowIntent:
    """A detected workflow action intent."""

    action: str  # e.g. "pause_monitoring", "reveal_identity", "export_data"
    description: str  # Human-readable description
    requires_confirmation: bool
    allowed_roles: tuple[str, ...]


# Pattern -> WorkflowIntent mapping
WORKFLOW_PATTERNS: list[tuple[re.Pattern, WorkflowIntent]] = [
    (
        re.compile(
            r"\b(pause|stop|disable)\b.*\b(monitor|tracking|surveillance)\b", re.I
        ),
        WorkflowIntent(
            action="pause_monitoring",
            description="Pause wellbeing monitoring for your account",
            requires_confirmation=True,
            allowed_roles=("employee", "manager", "admin"),
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
            allowed_roles=("employee", "manager", "admin"),
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
            allowed_roles=("employee", "manager", "admin"),
        ),
    ),
]


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
