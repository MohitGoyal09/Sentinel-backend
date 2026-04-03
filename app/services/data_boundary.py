"""
Data Boundary Enforcer — builds role-scoped context BEFORE the LLM call.

Guarantees that each role only receives data it is entitled to see:
  - Employee  : own risk data only
  - Manager   : own data + anonymised team aggregates (no user_hash)
  - Admin     : own data + org-wide aggregates
"""

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from app.models.analytics import RiskScore
from app.models.tenant import TenantMember


@dataclass(frozen=True)
class BoundaryContext:
    role: str
    user_data: dict = field(default_factory=dict)
    # Manager only — NEVER contains individual user_hash values
    team_aggregates: Optional[dict] = None
    # Admin only
    org_aggregates: Optional[dict] = None
    available_actions: list = field(default_factory=list)


# Actions available to each role
_EMPLOYEE_ACTIONS = ["view_own_wellbeing", "pause_monitoring"]
_MANAGER_ACTIONS = [
    "view_own_wellbeing",
    "pause_monitoring",
    "view_team_aggregates",
    "send_nudge",
    "schedule_checkin",
]
_ADMIN_ACTIONS = [
    "view_own_wellbeing",
    "pause_monitoring",
    "view_team_aggregates",
    "send_nudge",
    "schedule_checkin",
    "manage_teams",
    "view_audit_logs",
    "view_org_aggregates",
]

# Risk level ordering for distribution counts
_RISK_LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class DataBoundaryEnforcer:
    """Builds a ``BoundaryContext`` scoped to the caller's role.

    All database access is synchronous (``Session``, ``db.query()``).
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def build_context(
        self,
        user_hash: str,
        role: str,
        tenant_id: str,
        team_id: Optional[str] = None,
    ) -> BoundaryContext:
        """Return a ``BoundaryContext`` appropriate for *role*.

        Args:
            user_hash:  Caller's anonymised identifier.
            role:       "employee", "manager", or "admin".
            tenant_id:  Caller's tenant.
            team_id:    Caller's team (required for manager aggregate queries).
        """
        user_data = self._get_user_data(user_hash)

        if role == "employee":
            return BoundaryContext(
                role=role,
                user_data=user_data,
                available_actions=list(_EMPLOYEE_ACTIONS),
            )

        if role == "manager":
            team_agg = (
                self._get_team_aggregates(team_id, tenant_id)
                if team_id is not None
                else {}
            )
            return BoundaryContext(
                role=role,
                user_data=user_data,
                team_aggregates=team_agg,
                available_actions=list(_MANAGER_ACTIONS),
            )

        if role == "admin":
            org_agg = self._get_org_aggregates(tenant_id)
            return BoundaryContext(
                role=role,
                user_data=user_data,
                org_aggregates=org_agg,
                available_actions=list(_ADMIN_ACTIONS),
            )

        # Unknown role — return minimal context, no actions
        return BoundaryContext(role=role, user_data=user_data)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_user_data(self, user_hash: str) -> dict:
        """Fetch the caller's own risk data from RiskScore."""
        score = (
            self.db.query(RiskScore)
            .filter(RiskScore.user_hash == user_hash)
            .first()
        )
        if score is None:
            return {}

        return {
            "risk_level": score.risk_level,
            "velocity": score.velocity,
            "confidence": score.confidence,
            "thwarted_belongingness": score.thwarted_belongingness,
            "updated_at": (
                score.updated_at.isoformat() if score.updated_at else None
            ),
        }

    def _get_team_aggregates(self, team_id: str, tenant_id: str) -> dict:
        """Return anonymised aggregate stats for the given team.

        Individual ``user_hash`` values are NEVER included in the result.
        Only members who belong to *tenant_id* and *team_id* are considered.
        """
        members = (
            self.db.query(TenantMember)
            .filter(
                TenantMember.team_id == team_id,
                TenantMember.tenant_id == tenant_id,
            )
            .all()
        )

        if not members:
            return {"team_size": 0}

        member_hashes = [m.user_hash for m in members]

        scores = (
            self.db.query(RiskScore)
            .filter(RiskScore.user_hash.in_(member_hashes))
            .all()
        )

        team_size = len(members)
        at_risk_count = sum(
            1 for s in scores if s.risk_level in ("HIGH", "CRITICAL")
        )

        velocities = [s.velocity for s in scores if s.velocity is not None]
        avg_velocity = (
            round(sum(velocities) / len(velocities), 3) if velocities else None
        )

        risk_distribution = {level: 0 for level in _RISK_LEVELS}
        for s in scores:
            if s.risk_level in risk_distribution:
                risk_distribution[s.risk_level] += 1

        return {
            "team_size": team_size,
            "at_risk_count": at_risk_count,
            "avg_velocity": avg_velocity,
            "risk_distribution": risk_distribution,
        }

    def _get_org_aggregates(self, tenant_id: str) -> dict:
        """Return org-wide aggregate stats for *tenant_id*."""
        members = (
            self.db.query(TenantMember)
            .filter(TenantMember.tenant_id == tenant_id)
            .all()
        )

        total_employees = len(members)

        # Distinct team count (excluding unassigned members)
        team_ids = {m.team_id for m in members if m.team_id is not None}
        total_teams = len(team_ids)

        member_hashes = [m.user_hash for m in members]
        scores = (
            self.db.query(RiskScore)
            .filter(RiskScore.user_hash.in_(member_hashes))
            .all()
        ) if member_hashes else []

        at_risk_count = sum(
            1 for s in scores if s.risk_level in ("HIGH", "CRITICAL")
        )

        return {
            "total_employees": total_employees,
            "total_teams": total_teams,
            "at_risk_count": at_risk_count,
        }
