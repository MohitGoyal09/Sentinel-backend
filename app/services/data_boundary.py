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
from app.models.identity import UserIdentity
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
_RISK_LEVELS = ["LOW", "ELEVATED", "MEDIUM", "HIGH", "CRITICAL"]


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
        """Fetch the caller's own risk + centrality data."""
        from app.models.analytics import CentralityScore

        score = (
            self.db.query(RiskScore)
            .filter(RiskScore.user_hash == user_hash)
            .first()
        )
        centrality = (
            self.db.query(CentralityScore)
            .filter(CentralityScore.user_hash == user_hash)
            .first()
        )

        data: dict = {}
        if score is not None:
            data.update({
                "risk_level": score.risk_level,
                "velocity": score.velocity,
                "confidence": score.confidence,
                "thwarted_belongingness": score.thwarted_belongingness,
                "updated_at": (
                    score.updated_at.isoformat() if score.updated_at else None
                ),
            })
        if centrality is not None:
            data.update({
                "betweenness": centrality.betweenness,
                "eigenvector": centrality.eigenvector,
                "unblocking_count": centrality.unblocking_count,
            })
        return data

    def _get_team_aggregates(self, team_id: str, tenant_id: str) -> dict:
        """Return aggregate stats and per-member coaching data for the given team.

        Per-member entries include names and risk signals to support
        manager coaching features.  Individual ``user_hash`` values are
        NEVER included in the result.
        Only members who belong to *tenant_id* and *team_id* are considered.
        """
        from app.core.security import privacy

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

        score_map = {s.user_hash: s for s in scores}

        team_size = len(members)
        at_risk_count = sum(
            1 for s in scores if s.risk_level in ("HIGH", "CRITICAL", "ELEVATED")
        )

        velocities = [s.velocity for s in scores if s.velocity is not None]
        avg_velocity = (
            round(sum(velocities) / len(velocities), 3) if velocities else None
        )

        risk_distribution = {level: 0 for level in _RISK_LEVELS}
        for s in scores:
            if s.risk_level in risk_distribution:
                risk_distribution[s.risk_level] += 1

        # Build per-member coaching detail for manager context
        team_members_detail: list[dict] = []
        for m in members:
            risk = score_map.get(m.user_hash)

            # Resolve display name (same pattern as _get_org_aggregates)
            name = (
                m.display_name
                if hasattr(m, "display_name") and m.display_name
                else None
            )
            if not name:
                identity = (
                    self.db.query(UserIdentity)
                    .filter(UserIdentity.user_hash == m.user_hash)
                    .first()
                )
                if identity and identity.email_encrypted:
                    try:
                        email = privacy.decrypt(identity.email_encrypted)
                        name = (
                            email.split("@")[0].replace(".", " ").title()
                            if email
                            else m.user_hash[:8]
                        )
                    except Exception:
                        name = m.user_hash[:8]
                else:
                    name = m.user_hash[:8]

            velocity_val = round(risk.velocity, 2) if risk and risk.velocity else 0.0
            belongingness_val = (
                round(risk.thwarted_belongingness, 2)
                if risk and risk.thwarted_belongingness is not None
                else 0.5
            )
            attrition_val = (
                round(risk.attrition_probability * 100, 1)
                if risk and risk.attrition_probability is not None
                else 0.0
            )

            # Derive indicators using same thresholds as SafetyValveEngine
            team_members_detail.append({
                "name": name,
                "role": m.role,
                "risk_level": risk.risk_level if risk else "UNKNOWN",
                "velocity": velocity_val,
                "confidence": round(risk.confidence, 2) if risk and risk.confidence else 0.0,
                "attrition_probability": attrition_val,
                "belongingness_score": belongingness_val,
                "indicators": {
                    "chaotic_hours": velocity_val > 1.5,
                    "social_withdrawal": belongingness_val < 0.4,
                    "sustained_intensity": velocity_val > 2.0,
                },
            })

        # Sort: highest-risk first
        risk_order = {"CRITICAL": 0, "HIGH": 1, "ELEVATED": 2, "MEDIUM": 3, "LOW": 4, "UNKNOWN": 5}
        team_members_detail.sort(
            key=lambda e: risk_order.get(e["risk_level"], 6)
        )

        return {
            "team_size": team_size,
            "at_risk_count": at_risk_count,
            "avg_velocity": avg_velocity,
            "risk_distribution": risk_distribution,
            "team_members": team_members_detail,
        }

    def _get_org_aggregates(self, tenant_id: str) -> dict:
        """Return org-wide stats for *tenant_id*, including individual employee details.

        Admins get full visibility: aggregate counts PLUS a list of all
        employees with their names, roles, risk levels, and key metrics.
        """
        from app.core.security import privacy

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

        score_map = {s.user_hash: s for s in scores}

        at_risk_count = sum(
            1 for s in scores if s.risk_level in ("ELEVATED", "HIGH", "CRITICAL")
        )
        critical_count = sum(
            1 for s in scores if s.risk_level == "CRITICAL"
        )

        # Build individual employee list for admin context
        employees_detail = []
        for m in members:
            risk = score_map.get(m.user_hash)

            # Use display_name from TenantMember (populated by seed/invite)
            # Fall back to decrypting email if display_name is not set
            name = m.display_name if hasattr(m, "display_name") and m.display_name else None
            if not name:
                identity = (
                    self.db.query(UserIdentity)
                    .filter(UserIdentity.user_hash == m.user_hash)
                    .first()
                )
                if identity and identity.email_encrypted:
                    try:
                        email = privacy.decrypt(identity.email_encrypted)
                        name = email.split("@")[0].replace(".", " ").title() if email else m.user_hash[:8]
                    except Exception:
                        name = m.user_hash[:8]
                else:
                    name = m.user_hash[:8]

            employees_detail.append({
                "name": name,
                "role": m.role,
                "risk_level": risk.risk_level if risk else "UNKNOWN",
                "velocity": round(risk.velocity, 2) if risk and risk.velocity else 0.0,
                "confidence": round(risk.confidence, 2) if risk and risk.confidence else 0.0,
            })

        # Sort: critical first, then elevated, then low
        risk_order = {"CRITICAL": 0, "HIGH": 1, "ELEVATED": 2, "MEDIUM": 3, "LOW": 4, "UNKNOWN": 5}
        employees_detail.sort(key=lambda e: risk_order.get(e["risk_level"], 6))

        # Team culture summary: graph fragmentation
        from app.models.analytics import GraphEdge

        edges = (
            self.db.query(GraphEdge)
            .filter(GraphEdge.tenant_id == tenant_id)
            .all()
        )
        if edges:
            import networkx as nx

            G = nx.Graph()
            for e in edges:
                G.add_edge(e.source_hash, e.target_hash, weight=e.weight or 1.0)
            fragmentation = (
                round(1.0 - nx.average_clustering(G), 2) if len(G) > 0 else 0.0
            )
        else:
            fragmentation = 0.0

        avg_velocity = (
            round(
                sum(e["velocity"] for e in employees_detail) / len(employees_detail),
                2,
            )
            if employees_detail
            else 0.0
        )

        return {
            "total_employees": total_employees,
            "total_teams": total_teams,
            "at_risk_count": at_risk_count,
            "critical_count": critical_count,
            "risk_percentage": round(at_risk_count / total_employees * 100, 1) if total_employees else 0,
            "employees": employees_detail,
            "fragmentation": fragmentation,
            "avg_velocity": avg_velocity,
        }
