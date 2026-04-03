"""
Refusal Classifier — pattern-based out-of-scope query detection for Ask Sentinel.

Rules by role:
  - Admin     : never refused (return None)
  - Employee  : refused on other-person, team, or org queries
  - Manager   : refused on org queries or queries about another team

Redirect messages NEVER confirm whether data exists.
Every refusal is written to AuditLog via PermissionService.log_data_access.
"""

import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.services.permission_service import PermissionService


@dataclass(frozen=True)
class RefusalResult:
    message: str
    reason_code: str


# ---------------------------------------------------------------------------
# Pattern sets
# ---------------------------------------------------------------------------

# Queries that ask about a specific other person (pronouns, names, possessives)
_OTHER_PERSON_PATTERNS = [
    re.compile(r"\b(his|her|their|him|them)\b", re.IGNORECASE),
    re.compile(r"\bsomeone else\b", re.IGNORECASE),
    re.compile(r"\bother (employee|person|user|colleague|member)\b", re.IGNORECASE),
    re.compile(r"\bwhat (is|are|does|did) \w+ (doing|scoring|feeling|showing)\b", re.IGNORECASE),
    re.compile(r"\b(show|tell|give) me (about )?\w+[''s]+\b", re.IGNORECASE),
]

# Queries that ask about a whole team
_TEAM_QUERY_PATTERNS = [
    re.compile(r"\bmy team\b", re.IGNORECASE),
    re.compile(r"\bteam (stats|data|score|risk|performance|metrics|overview|summary)\b", re.IGNORECASE),
    re.compile(r"\bhow is (my )?team\b", re.IGNORECASE),
    re.compile(r"\b(all|every(one)?|each) (team member|member|report|direct)\b", re.IGNORECASE),
    re.compile(r"\bteam (health|wellbeing|status)\b", re.IGNORECASE),
]

# Queries that ask about the entire organisation
_ORG_QUERY_PATTERNS = [
    re.compile(r"\b(company|organisation|organization|org|entire company)\b", re.IGNORECASE),
    re.compile(r"\ball teams?\b", re.IGNORECASE),
    re.compile(r"\beveryone\b", re.IGNORECASE),
    re.compile(r"\borg[ -]?wide\b", re.IGNORECASE),
    re.compile(r"\bcompany[ -]?wide\b", re.IGNORECASE),
    re.compile(r"\b(total|overall) (employees?|headcount|workforce)\b", re.IGNORECASE),
]

# Queries that reference another team by name or imply cross-team access
_OTHER_TEAM_PATTERNS = [
    re.compile(r"\b(another|other|different) team\b", re.IGNORECASE),
    re.compile(r"\bteam \w+ (data|stats|risk|score)\b", re.IGNORECASE),
    re.compile(r"\bnot (on )?my team\b", re.IGNORECASE),
]


def _matches_any(text: str, patterns: list[re.Pattern]) -> bool:
    return any(p.search(text) for p in patterns)


# ---------------------------------------------------------------------------
# Redirect messages (never confirm existence of specific data)
# ---------------------------------------------------------------------------

_MSG_OWN_DATA_ONLY = (
    "I can help with your personal wellbeing insights, risk trends, and "
    "velocity data. For information about other individuals, please speak "
    "directly with your manager or HR."
)

_MSG_NO_ORG_DATA = (
    "I can help with your personal wellbeing insights and team-level "
    "aggregates for your direct team. Organisation-wide data is available "
    "to administrators — please contact your Sentinel admin for that view."
)

_MSG_OWN_TEAM_ONLY = (
    "I can help with your personal wellbeing insights and aggregated data "
    "for your own team. For information about other teams, please contact "
    "your Sentinel administrator."
)


class RefusalClassifier:
    """Classify whether a query should be refused before it reaches the LLM.

    All database access is synchronous (``Session``, ``db.query()``).
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def classify(
        self,
        message: str,
        role: str,
        user_hash: str,
        tenant_id: str,
    ) -> Optional[RefusalResult]:
        """Return a ``RefusalResult`` if the query is out of scope, else None.

        Args:
            message:    Raw user query text.
            role:       Caller's role ("employee", "manager", "admin").
            user_hash:  Caller's anonymised identifier (for audit logging).
            tenant_id:  Caller's tenant (for audit logging).

        Returns:
            ``RefusalResult`` if the query must be refused, ``None`` otherwise.
        """
        # Admins are never refused
        if role == "admin":
            return None

        result = self._classify_for_role(message, role)
        if result is not None:
            self._write_audit(
                user_hash=user_hash,
                role=role,
                tenant_id=tenant_id,
                message=message,
                reason_code=result.reason_code,
            )
        return result

    # ------------------------------------------------------------------
    # Role-specific classification
    # ------------------------------------------------------------------

    def _classify_for_role(
        self, message: str, role: str
    ) -> Optional[RefusalResult]:
        if role == "employee":
            return self._classify_employee(message)
        if role == "manager":
            return self._classify_manager(message)
        # Unknown roles get a safe fallback refusal
        return RefusalResult(
            message=_MSG_OWN_DATA_ONLY,
            reason_code="unknown_role",
        )

    def _classify_employee(self, message: str) -> Optional[RefusalResult]:
        """Employees may only query their own data."""
        if _matches_any(message, _OTHER_PERSON_PATTERNS):
            return RefusalResult(
                message=_MSG_OWN_DATA_ONLY,
                reason_code="employee_other_person_query",
            )
        if _matches_any(message, _TEAM_QUERY_PATTERNS):
            return RefusalResult(
                message=_MSG_OWN_DATA_ONLY,
                reason_code="employee_team_query",
            )
        if _matches_any(message, _ORG_QUERY_PATTERNS):
            return RefusalResult(
                message=_MSG_OWN_DATA_ONLY,
                reason_code="employee_org_query",
            )
        return None

    def _classify_manager(self, message: str) -> Optional[RefusalResult]:
        """Managers may query their own data and their own team aggregates."""
        if _matches_any(message, _ORG_QUERY_PATTERNS):
            return RefusalResult(
                message=_MSG_NO_ORG_DATA,
                reason_code="manager_org_query",
            )
        if _matches_any(message, _OTHER_TEAM_PATTERNS):
            return RefusalResult(
                message=_MSG_OWN_TEAM_ONLY,
                reason_code="manager_other_team_query",
            )
        return None

    # ------------------------------------------------------------------
    # Audit trail
    # ------------------------------------------------------------------

    def _write_audit(
        self,
        user_hash: str,
        role: str,
        tenant_id: str,
        message: str,
        reason_code: str,
    ) -> None:
        """Write a refusal audit record via PermissionService.log_data_access."""
        truncated_message = message[:200] if len(message) > 200 else message
        PermissionService.log_data_access(
            self.db,
            actor_hash=user_hash,
            actor_role=role,
            target_hash=user_hash,
            action="ask_sentinel_refusal",
            tenant_id=tenant_id,
            details={
                "reason_code": reason_code,
                "query_preview": truncated_message,
            },
        )
