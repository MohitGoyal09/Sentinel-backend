"""
Permission Service - RBAC Engine for Sentinel

This module implements Role-Based Access Control (RBAC) with privacy-first design.
Think of it as the security guard that checks ID at every door.
"""

from enum import Enum
from datetime import datetime, timedelta
from typing import Optional, List
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.models.identity import UserIdentity, AuditLog
from app.models.analytics import RiskScore, RiskHistory


class UserRole(str, Enum):
    """Three-tier role system"""

    EMPLOYEE = "employee"
    MANAGER = "manager"
    ADMIN = "admin"


class PermissionService:
    """
    Permission checking service that enforces RBAC rules.

    Think of this as the bouncer at an exclusive club. Every API endpoint
    must pass through here before serving data.
    """

    # Permission matrix - who can do what
    PERMISSIONS = {
        "view_own_risk": [UserRole.EMPLOYEE, UserRole.MANAGER, UserRole.ADMIN],
        "view_own_velocity": [UserRole.EMPLOYEE, UserRole.MANAGER, UserRole.ADMIN],
        "view_team_aggregates": [UserRole.MANAGER, UserRole.ADMIN],
        "view_individual_details": [
            UserRole.MANAGER,
            UserRole.ADMIN,
        ],  # With restrictions
        "pause_monitoring": [UserRole.EMPLOYEE, UserRole.MANAGER, UserRole.ADMIN],
        "delete_own_data": [UserRole.EMPLOYEE, UserRole.MANAGER, UserRole.ADMIN],
        "run_simulation": [UserRole.MANAGER, UserRole.ADMIN],
        "configure_thresholds": [UserRole.ADMIN],
        "view_audit_logs": [UserRole.ADMIN],
        "view_system_health": [UserRole.ADMIN],
    }

    def __init__(self, db: Session):
        self.db = db

    def get_user_identity(self, user_hash: str) -> Optional[UserIdentity]:
        """Fetch user identity from Vault B"""
        return self.db.query(UserIdentity).filter_by(user_hash=user_hash).first()

    def check_permission(self, user: UserIdentity, permission: str) -> bool:
        """Check if user has a specific permission"""
        if permission not in self.PERMISSIONS:
            return False
        allowed_roles = self.PERMISSIONS[permission]
        return user.role in [r.value for r in allowed_roles]

    def can_view_own_data(self, user: UserIdentity, target_hash: str) -> bool:
        """
        Employees can only view their own data.
        Think of it like checking your own medical records.
        """
        return user.user_hash == target_hash

    def is_critical_for_36h(self, user_hash: str) -> bool:
        """
        Check if user has been at CRITICAL risk for 36+ hours.
        This is the emergency override for manager access.

        Why 36 hours? It's the sweet spot between:
        - 24 hours: Could just be a busy sprint
        - 48 hours: Might be too late for intervention
        - 36 hours: "This is a real emergency, not just a bad day"

        Returns True if:
        1. Current risk level is CRITICAL, AND
        2. Risk has been CRITICAL continuously for 36+ hours
        """
        # Get current risk score
        current_risk = self.db.query(RiskScore).filter_by(user_hash=user_hash).first()

        if not current_risk or current_risk.risk_level != "CRITICAL":
            return False

        # Calculate 36 hours ago
        cutoff_time = datetime.utcnow() - timedelta(hours=36)

        # Get risk history for last 36 hours
        history = (
            self.db.query(RiskHistory)
            .filter(
                RiskHistory.user_hash == user_hash, RiskHistory.timestamp >= cutoff_time
            )
            .order_by(RiskHistory.timestamp.desc())
            .all()
        )

        if not history:
            # No history, check if current risk has been critical long enough
            # If updated_at is older than 36 hours, assume it's been critical
            if current_risk.updated_at and current_risk.updated_at <= cutoff_time:
                return True
            return False

        # Check if all history entries are CRITICAL
        # This means risk has been critical continuously for 36+ hours
        for entry in history:
            if entry.risk_level != "CRITICAL":
                return False

        # Also verify the earliest entry is at least 36 hours old
        earliest_entry = history[-1]  # Last in the list (oldest)
        if earliest_entry.timestamp > cutoff_time:
            # Haven't been tracking for 36 hours yet
            return False

        return True

    def can_manager_view_employee(
        self, manager: UserIdentity, employee_hash: str
    ) -> tuple[bool, str]:
        """
        Check if a manager can view an employee's data.
        Returns (can_view: bool, reason: str)

        Rules:
        1. Must be the employee's assigned manager
        2. Employee must have consented, OR
        3. Employee must be at CRITICAL risk for 36+ hours (emergency)

        Think of it like a doctor accessing patient records:
        - Normal case: Need patient consent
        - Emergency: Can access without consent to save a life
        """
        employee = self.get_user_identity(employee_hash)

        if not employee:
            return False, "Employee not found"

        # Check if this is actually the employee's manager
        if employee.manager_hash != manager.user_hash:
            return False, "Not your direct report"

        # Check if employee has consented
        if employee.consent_share_with_manager:
            return True, "Employee has consented to share data"

        # Check emergency override (36-hour critical rule)
        if self.is_critical_for_36h(employee_hash):
            return True, "EMERGENCY: Employee at critical risk for 36+ hours"

        return False, "No consent and no emergency condition"

    def can_view_user_data(
        self, current_user: UserIdentity, target_hash: str, action: str = "view"
    ) -> tuple[bool, str]:
        """
        Main permission check for viewing user data.
        This is the gatekeeper function that every endpoint should use.

        Returns (can_view: bool, reason: str)
        """
        # Employees can only view their own data
        if current_user.role == UserRole.EMPLOYEE.value:
            if current_user.user_hash != target_hash:
                return False, "Employees can only view their own data"
            return True, "Viewing own data"

        # Managers can view team members (with restrictions)
        if current_user.role == UserRole.MANAGER.value:
            can_view, reason = self.can_manager_view_employee(current_user, target_hash)
            return can_view, reason

        # Admins can view anyone (for audit purposes)
        if current_user.role == UserRole.ADMIN.value:
            return True, "Admin access (audit trail required)"

        return False, "Unknown role"

    def can_view_team_aggregates(self, user: UserIdentity) -> bool:
        """Check if user can view anonymized team data"""
        return self.check_permission(user, "view_team_aggregates")

    def can_run_simulation(self, user: UserIdentity) -> bool:
        """Check if user can run simulations"""
        return self.check_permission(user, "run_simulation")

    def can_configure_system(self, user: UserIdentity) -> bool:
        """Check if user can change system settings"""
        return self.check_permission(user, "configure_thresholds")

    def log_data_access(
        self, accessor_hash: str, target_hash: str, action: str, details: dict = None
    ):
        """
        Log every data access to audit trail.

        This is the security camera footage—if someone accesses data,
        we know who, when, and why.
        """
        audit_entry = AuditLog(
            user_hash=target_hash,  # The person whose data was accessed
            action=f"data_access:{action}",
            details={
                "accessor_hash": accessor_hash,
                "accessor_role": self.get_user_identity(accessor_hash).role
                if self.get_user_identity(accessor_hash)
                else "unknown",
                "target_hash": target_hash,
                "timestamp": datetime.utcnow().isoformat(),
                **(details or {}),
            },
        )
        self.db.add(audit_entry)
        self.db.commit()

    def get_user_team_members(self, manager_hash: str) -> List[UserIdentity]:
        """Get all employees who report to this manager"""
        return self.db.query(UserIdentity).filter_by(manager_hash=manager_hash).all()


# Exception helpers for cleaner endpoint code
class PermissionDenied(HTTPException):
    """Raised when user doesn't have permission"""

    def __init__(self, detail: str = "Access denied"):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


class NotFound(HTTPException):
    """Raised when resource doesn't exist"""

    def __init__(self, detail: str = "Resource not found"):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
