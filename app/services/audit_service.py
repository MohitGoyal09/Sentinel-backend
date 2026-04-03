"""
AuditService — centralized audit logging with typed action constants.

All auditable operations in Sentinel must go through this service.
Every call produces an immutable row in identity.audit_logs.
"""
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.identity import AuditLog

logger = logging.getLogger("sentinel.audit")


class AuditAction:
    """
    17 standardized audit action constants (spec Section 7).

    String values are snake_case and written directly into AuditLog.action.
    Keeping them as class attributes (not Enum) avoids .value boilerplate at
    every call site while still providing IDE auto-complete and grep-ability.
    """

    # Identity / access
    IDENTITY_REVEALED: str = "identity_revealed"
    CRITICAL_OVERRIDE_ACCESS: str = "critical_override_access"
    DATA_ACCESSED: str = "data_accessed"
    DATA_EXPORTED: str = "data_exported"
    OUT_OF_SCOPE_QUERY: str = "out_of_scope_query"

    # User lifecycle
    ROLE_CHANGED: str = "role_changed"
    USER_INVITED: str = "user_invited"
    USER_REMOVED: str = "user_removed"
    USER_DEACTIVATED: str = "user_deactivated"

    # Team management
    TEAM_MODIFIED: str = "team_modified"

    # Consent / monitoring
    CONSENT_CHANGED: str = "consent_changed"
    MONITORING_PAUSED: str = "monitoring_paused"

    # Workflows
    WORKFLOW_CREATED: str = "workflow_created"
    WORKFLOW_EXECUTED: str = "workflow_executed"

    # Tools / integrations
    TOOL_CONNECTED: str = "tool_connected"
    TOOL_DISCONNECTED: str = "tool_disconnected"

    # Engine / data ops
    ENGINE_RECOMPUTED: str = "engine_recomputed"
    CSV_UPLOADED: str = "csv_uploaded"


class AuditService:
    """
    Centralized audit logger.

    Usage::

        from app.services.audit_service import AuditService, AuditAction

        audit = AuditService(db)
        audit.log(
            actor_hash=member.user_hash,
            actor_role=member.role,
            action=AuditAction.ROLE_CHANGED,
            target_hash=target_user_hash,
            details={"old_role": old_role, "new_role": new_role},
            tenant_id=member.tenant_id,
            ip_address=request.client.host,
        )

    log() calls session.flush() so the row is visible within the same
    transaction but does NOT commit — the caller controls the transaction.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def log(
        self,
        actor_hash: str,
        actor_role: str,
        action: str,
        target_hash: Optional[str] = None,
        details: Optional[dict] = None,
        tenant_id: Optional[UUID] = None,
        ip_address: Optional[str] = None,
    ) -> AuditLog:
        """
        Write one immutable audit row.

        Args:
            actor_hash:  HMAC hash of the user performing the action.
            actor_role:  Role string at the time of the action.
            action:      One of the AuditAction constants.
            target_hash: HMAC hash of the affected user (nullable for non-user actions).
            details:     Arbitrary JSON payload (e.g. old/new role, tool slug).
            tenant_id:   Tenant UUID for multi-tenant filtering.
            ip_address:  Client IP — sourced from Request.client.host in endpoints.

        Returns:
            The persisted AuditLog ORM object (pre-commit).
        """
        entry = AuditLog(
            tenant_id=tenant_id,
            actor_hash=actor_hash,
            actor_role=actor_role,
            user_hash=target_hash,
            action=action,
            details=details or {},
            ip_address=ip_address,
            timestamp=datetime.utcnow(),
        )
        self._db.add(entry)
        self._db.flush()
        logger.debug(
            "audit action=%s actor=%s target=%s",
            action,
            actor_hash[:8] if actor_hash else "?",
            (target_hash or "")[:8] or "none",
        )
        return entry
