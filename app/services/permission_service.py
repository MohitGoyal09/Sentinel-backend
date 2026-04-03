"""
Permission Service - RBAC Engine for Sentinel

Full 52-permission matrix with Team-based scoping.
All role checks use plain strings; the UserRole enum is retained only for
backwards compatibility with existing endpoint code.
"""

from enum import Enum
from datetime import datetime, timedelta
from typing import Optional, List
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.models.identity import UserIdentity, AuditLog
from app.models.analytics import RiskScore, RiskHistory
from app.models.tenant import TenantMember
from app.models.team import Team


# ---------------------------------------------------------------------------
# Backwards-compatible enum (deprecated -- prefer plain strings)
# ---------------------------------------------------------------------------
class UserRole(str, Enum):
    """Three-tier role system. Kept for backwards compatibility only."""

    EMPLOYEE = "employee"
    MANAGER = "manager"
    ADMIN = "admin"


# ---------------------------------------------------------------------------
# Exception helpers
# ---------------------------------------------------------------------------
class PermissionDenied(HTTPException):
    """Raised when user doesn't have permission."""

    def __init__(self, detail: str = "Access denied"):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


class NotFound(HTTPException):
    """Raised when resource doesn't exist."""

    def __init__(self, detail: str = "Resource not found"):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


# ---------------------------------------------------------------------------
# 52-Permission static map
# ---------------------------------------------------------------------------
PERMISSIONS: dict[str, list[str]] = {
    # ---- Personal (all roles) -- 16 permissions ----
    "view_own_dashboard": ["employee", "manager", "admin"],
    "view_own_wellbeing": ["employee", "manager", "admin"],
    "view_own_risk": ["employee", "manager", "admin"],
    "view_own_velocity": ["employee", "manager", "admin"],
    "view_own_nudges": ["employee", "manager", "admin"],
    "manage_own_consent": ["employee", "manager", "admin"],
    "pause_own_monitoring": ["employee", "manager", "admin"],
    "delete_own_data": ["employee", "manager", "admin"],
    "use_ask_sentinel": ["employee", "manager", "admin"],
    "create_personal_workflow": ["employee", "manager", "admin"],
    "invoke_personal_workflow": ["employee", "manager", "admin"],
    "generate_own_1on1_agenda": ["employee", "manager", "admin"],
    "connect_own_tools": ["employee", "manager", "admin"],
    "disconnect_own_tools": ["employee", "manager", "admin"],
    "view_own_connections": ["employee", "manager", "admin"],
    "view_own_audit_actions": ["employee", "manager", "admin"],
    # ---- Team (manager + admin) -- 12 permissions ----
    "view_team_engines": ["manager", "admin"],
    "view_team_safety_valve": ["manager", "admin"],
    "view_team_talent_scout": ["manager", "admin"],
    "view_team_culture_thermo": ["manager", "admin"],
    "view_team_anonymized": ["manager", "admin"],
    "reveal_team_identity": ["manager", "admin"],
    "view_team_aggregates": ["manager", "admin"],
    "run_simulation": ["manager", "admin"],
    "generate_team_1on1_agenda": ["manager", "admin"],
    "create_team_workflow": ["manager", "admin"],
    "invoke_team_workflow": ["manager", "admin"],
    "dispatch_team_nudge": ["manager", "admin"],
    # ---- Organization (admin only) -- 24 permissions ----
    "view_org_engines": ["admin"],
    "view_all_teams": ["admin"],
    "view_org_health_map": ["admin"],
    "manage_users": ["admin"],
    "invite_users": ["admin"],
    "promote_demote_roles": ["admin"],
    "remove_users": ["admin"],
    "manage_teams": ["admin"],
    "assign_team_members": ["admin"],
    "assign_team_manager": ["admin"],
    "view_audit_logs": ["admin"],
    "configure_thresholds": ["admin"],
    "view_system_health": ["admin"],
    "create_org_workflow": ["admin"],
    "invoke_org_workflow": ["admin"],
    "export_org_data": ["admin"],
    "view_pipeline_health": ["admin"],
    "configure_data_sources": ["admin"],
    "upload_csv_data": ["admin"],
    "manage_data_retention": ["admin"],
    "view_ingestion_errors": ["admin"],
    "trigger_engine_recompute": ["admin"],
    "sync_hris": ["admin"],
    "manage_org_integrations": ["admin"],
}

assert len(PERMISSIONS) == 52, (
    f"Permission map must have exactly 52 entries, got {len(PERMISSIONS)}"
)


class PermissionService:
    """
    Permission checking service that enforces RBAC rules.

    All public permission helpers are classmethods or accept plain role
    strings so they can be used without instantiation where possible.
    Instance methods that require DB queries accept ``db`` explicitly or
    use ``self.db`` for backwards compatibility with existing callers.
    """

    # Expose the map as a class attribute so existing tests that reference
    # ``PermissionService.PERMISSIONS`` continue to work.
    PERMISSIONS = PERMISSIONS

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Pure permission checks (no DB needed)
    # ------------------------------------------------------------------
    @classmethod
    def check_permission(cls, role: str, permission: str) -> bool:
        """Return True if *role* is allowed *permission*.

        ``role`` is a plain string (``"employee"``, ``"manager"``, ``"admin"``).
        For backwards compatibility, also accepts a ``UserIdentity`` or any
        object with a ``.role`` attribute -- the role string is extracted
        automatically.
        """
        # Backwards compat: accept objects with a .role attribute
        actual_role: str = getattr(role, "role", role)  # type: ignore[arg-type]
        if permission not in cls.PERMISSIONS:
            return False
        return actual_role in cls.PERMISSIONS[permission]

    @classmethod
    def assert_permission(cls, role: str, permission: str) -> None:
        """Raise ``PermissionDenied`` unless *role* holds *permission*."""
        if not cls.check_permission(role, permission):
            actual_role = getattr(role, "role", role)
            raise PermissionDenied(
                detail=f"Role '{actual_role}' lacks permission '{permission}'"
            )

    # ------------------------------------------------------------------
    # Team-scoped data access checks
    # ------------------------------------------------------------------
    def can_manager_view_employee(
        self,
        db: Session,
        manager_member: TenantMember,
        employee_hash: str,
    ) -> tuple[bool, str]:
        """Check if a manager can view an employee's individual data.

        Uses the Team table to verify the manager-employee relationship
        instead of the deprecated ``UserIdentity.manager_hash`` column.

        Rules:
        1. Manager and employee must be on the same team.
        2. Employee must have consented, OR
        3. Employee must be at CRITICAL risk for 36+ hours (emergency).
        """
        if manager_member.team_id is None:
            return False, "Manager is not assigned to any team"

        # Check the employee belongs to the same team
        employee_member = (
            db.query(TenantMember)
            .filter_by(team_id=manager_member.team_id, user_hash=employee_hash)
            .first()
        )
        if employee_member is None:
            return False, "Not your direct report"

        # Fetch identity for consent check
        employee_identity = (
            db.query(UserIdentity).filter_by(user_hash=employee_hash).first()
        )
        if employee_identity is None:
            return False, "Employee identity not found"

        # Consent check
        if employee_identity.consent_share_with_manager:
            return True, "Employee has consented to share data"

        # Emergency 36-hour critical override
        if self.is_critical_for_36h(employee_hash):
            return True, "EMERGENCY: Employee at critical risk for 36+ hours"

        return False, "No consent and no emergency condition"

    def can_view_user_data(
        self,
        db: Session,
        member: TenantMember,
        target_hash: str,
        action: str = "view",
    ) -> tuple[bool, str]:
        """Main permission gate for viewing another user's data.

        Uses ``TenantMember.role`` (plain string).
        """
        role = member.role

        # Employees can only view their own data
        if role == "employee":
            if member.user_hash != target_hash:
                return False, "Employees can only view their own data"
            return True, "Viewing own data"

        # Managers can view team members (with consent / emergency rules)
        if role == "manager":
            return self.can_manager_view_employee(db, member, target_hash)

        # Admins can view anyone (audit trail required)
        if role == "admin":
            return True, "Admin access (audit trail required)"

        return False, "Unknown role"

    # ------------------------------------------------------------------
    # Team member queries
    # ------------------------------------------------------------------
    def get_user_team_members(
        self, db: Session, member: TenantMember
    ) -> List[TenantMember]:
        """Return all TenantMembers sharing the same team as *member*."""
        if member.team_id is None:
            return []
        return (
            db.query(TenantMember)
            .filter(
                TenantMember.team_id == member.team_id,
                TenantMember.user_hash != member.user_hash,
            )
            .all()
        )

    # ------------------------------------------------------------------
    # 36-hour critical risk override
    # ------------------------------------------------------------------
    def is_critical_for_36h(self, user_hash: str) -> bool:
        """Check if user has been at CRITICAL risk for 36+ continuous hours.

        This is the emergency override that lets a manager access an
        employee's individual data without explicit consent.
        """
        current_risk = (
            self.db.query(RiskScore).filter_by(user_hash=user_hash).first()
        )
        if not current_risk or current_risk.risk_level != "CRITICAL":
            return False

        cutoff_time = datetime.utcnow() - timedelta(hours=36)

        history = (
            self.db.query(RiskHistory)
            .filter(
                RiskHistory.user_hash == user_hash,
                RiskHistory.timestamp >= cutoff_time,
            )
            .order_by(RiskHistory.timestamp.desc())
            .all()
        )

        if not history:
            if current_risk.updated_at and current_risk.updated_at <= cutoff_time:
                return True
            return False

        # Every entry must be CRITICAL for continuous 36h
        for entry in history:
            if entry.risk_level != "CRITICAL":
                return False

        # Earliest entry must be at least 36 hours old
        earliest_entry = history[-1]
        if earliest_entry.timestamp > cutoff_time:
            return False

        return True

    # ------------------------------------------------------------------
    # Audit logging
    # ------------------------------------------------------------------
    @staticmethod
    def log_data_access(
        db: Session,
        *,
        actor_hash: str,
        actor_role: str,
        target_hash: str,
        action: str,
        tenant_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> AuditLog:
        """Write an immutable audit record using the enhanced AuditLog schema.

        All parameters are keyword-only (after *db*) to prevent positional
        argument mistakes.
        """
        audit_entry = AuditLog(
            tenant_id=tenant_id,
            actor_hash=actor_hash,
            actor_role=actor_role,
            user_hash=target_hash,
            action=action,
            details=details or {},
            ip_address=ip_address,
        )
        db.add(audit_entry)
        db.flush()
        return audit_entry

    # ------------------------------------------------------------------
    # Convenience helpers (backwards compat for existing endpoint code)
    # ------------------------------------------------------------------
    def get_user_identity(self, user_hash: str) -> Optional[UserIdentity]:
        """Fetch user identity from Vault B."""
        return (
            self.db.query(UserIdentity).filter_by(user_hash=user_hash).first()
        )

    def can_view_team_aggregates(self, user: UserIdentity) -> bool:
        """Check if user can view anonymized team data."""
        return self.check_permission(user, "view_team_aggregates")

    def can_run_simulation(self, user: UserIdentity) -> bool:
        """Check if user can run simulations."""
        return self.check_permission(user, "run_simulation")

    def can_configure_system(self, user: UserIdentity) -> bool:
        """Check if user can change system settings."""
        return self.check_permission(user, "configure_thresholds")
