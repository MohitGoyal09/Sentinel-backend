"""
Admin API Endpoints (/admin)

System administration endpoints for:
- System health monitoring
- System-wide audit logs
- User management
- Configuration management
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, Integer
from datetime import datetime, timedelta
from typing import List, Optional, Dict

from app.core.database import get_db
from app.models.identity import UserIdentity, AuditLog
from app.models.analytics import RiskScore, RiskHistory, Event
from app.api.deps.auth import get_current_user_identity, require_role
from app.services.permission_service import PermissionService

router = APIRouter()


@router.get("/health", response_model=dict)
def get_system_health(
    current_user: UserIdentity = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Get comprehensive system health metrics.

    Returns:
    - Database statistics
    - User counts by role
    - Risk distribution across all users
    - Recent activity metrics
    - System performance indicators
    """
    # Database statistics
    total_users = db.query(UserIdentity).count()
    total_events = db.query(Event).count()
    total_audit_logs = db.query(AuditLog).count()
    total_risk_scores = db.query(RiskScore).count()

    # User distribution by role
    role_distribution = (
        db.query(UserIdentity.role, func.count(UserIdentity.user_hash).label("count"))
        .group_by(UserIdentity.role)
        .all()
    )

    # Consent statistics
    consent_stats = db.query(
        func.sum(func.cast(UserIdentity.consent_share_with_manager, Integer)).label(
            "consented"
        ),
        func.count(UserIdentity.user_hash).label("total"),
    ).first()

    # Risk distribution across all users
    risk_distribution = (
        db.query(RiskScore.risk_level, func.count(RiskScore.user_hash).label("count"))
        .group_by(RiskScore.risk_level)
        .all()
    )

    # Recent activity (last 24 hours)
    day_ago = datetime.utcnow() - timedelta(hours=24)
    recent_events_24h = db.query(Event).filter(Event.timestamp >= day_ago).count()
    recent_audit_logs_24h = (
        db.query(AuditLog).filter(AuditLog.timestamp >= day_ago).count()
    )

    # Critical users count
    critical_count = (
        db.query(RiskScore).filter(RiskScore.risk_level == "CRITICAL").count()
    )
    elevated_count = (
        db.query(RiskScore).filter(RiskScore.risk_level == "ELEVATED").count()
    )

    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "database": {
            "total_users": total_users,
            "total_events": total_events,
            "total_audit_logs": total_audit_logs,
            "total_risk_scores": total_risk_scores,
        },
        "users": {
            "by_role": {role: count for role, count in role_distribution},
            "consent_rate": {
                "consented": int(consent_stats.consented or 0),
                "total": consent_stats.total,
                "percentage": round(
                    (consent_stats.consented or 0) / consent_stats.total * 100, 1
                )
                if consent_stats.total > 0
                else 0,
            },
        },
        "risk_summary": {
            "distribution": {level: count for level, count in risk_distribution},
            "critical_count": critical_count,
            "elevated_count": elevated_count,
            "at_risk_total": critical_count + elevated_count,
        },
        "activity_24h": {
            "events": recent_events_24h,
            "audit_logs": recent_audit_logs_24h,
        },
    }


@router.get("/audit-logs", response_model=dict)
def get_system_audit_logs(
    days: int = 7,
    action_type: Optional[str] = None,
    user_hash: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    current_user: UserIdentity = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Get system-wide audit logs with filtering options.

    Parameters:
    - days: Number of days to look back
    - action_type: Filter by action type (e.g., 'data_access', 'consent_updated')
    - user_hash: Filter by specific user
    - limit: Number of records to return
    - offset: Pagination offset

    Returns comprehensive audit trail for compliance and monitoring.
    """
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    # Build query
    query = db.query(AuditLog).filter(AuditLog.timestamp >= cutoff_date)

    if action_type:
        query = query.filter(AuditLog.action.like(f"%{action_type}%"))

    if user_hash:
        query = query.filter(AuditLog.user_hash == user_hash)

    # Get total count for pagination
    total_count = query.count()

    # Get paginated results
    logs = query.order_by(desc(AuditLog.timestamp)).offset(offset).limit(limit).all()

    # Format results
    formatted_logs = []
    for log in logs:
        formatted_logs.append(
            {
                "id": log.id,
                "user_hash": log.user_hash,
                "action": log.action,
                "details": log.details,
                "timestamp": log.timestamp.isoformat(),
            }
        )

    return {
        "total_count": total_count,
        "returned_count": len(formatted_logs),
        "days": days,
        "filters": {"action_type": action_type, "user_hash": user_hash},
        "pagination": {
            "limit": limit,
            "offset": offset,
            "has_more": (offset + limit) < total_count,
        },
        "logs": formatted_logs,
    }


@router.get("/users", response_model=dict)
def get_all_users(
    role: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user: UserIdentity = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Get all users in the system with their status.
    Admin sees email addresses, managers see hashes only (privacy protected).
    """
    query = db.query(UserIdentity)

    if role:
        query = query.filter(UserIdentity.role == role)

    total_count = query.count()
    users = query.offset(offset).limit(limit).all()

    user_hashes = [u.user_hash for u in users]
    risk_scores = db.query(RiskScore).filter(RiskScore.user_hash.in_(user_hashes)).all()

    risk_map = {r.user_hash: r for r in risk_scores}

    from app.core.security import privacy

    formatted_users = []
    for user in users:
        risk = risk_map.get(user.user_hash)

        decrypted_email = (
            privacy.decrypt(user.email_encrypted) if user.email_encrypted else ""
        )
        email = decrypted_email if decrypted_email else None
        name = email.split("@")[0] if email else user.user_hash[:8]

        formatted_users.append(
            {
                "user_hash": user.user_hash,
                "email": email,
                "name": name,
                "role": user.role,
                "consent_share_with_manager": user.consent_share_with_manager,
                "consent_share_anonymized": user.consent_share_anonymized,
                "monitoring_paused": user.monitoring_paused_until is not None,
                "monitoring_paused_until": user.monitoring_paused_until.isoformat()
                if user.monitoring_paused_until
                else None,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "risk_level": risk.risk_level if risk else "LOW",
                "velocity": risk.velocity if risk else None,
                "last_updated": risk.updated_at.isoformat() if risk else None,
                "has_manager": user.manager_hash is not None,
            }
        )

    return {
        "total_count": total_count,
        "returned_count": len(formatted_users),
        "filters": {"role": role},
        "users": formatted_users,
    }


@router.get("/statistics", response_model=dict)
def get_system_statistics(
    days: int = 30,
    current_user: UserIdentity = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Get detailed system statistics and trends.

    Returns:
    - User growth over time
    - Activity trends
    - Risk trend analysis
    - Consent rate changes
    """
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    # User growth (new users per day)
    new_users = (
        db.query(
            func.date(UserIdentity.created_at).label("date"),
            func.count(UserIdentity.user_hash).label("count"),
        )
        .filter(UserIdentity.created_at >= cutoff_date)
        .group_by(func.date(UserIdentity.created_at))
        .all()
    )

    # Daily activity (events per day)
    daily_events = (
        db.query(
            func.date(Event.timestamp).label("date"),
            func.count(Event.id).label("count"),
        )
        .filter(Event.timestamp >= cutoff_date)
        .group_by(func.date(Event.timestamp))
        .all()
    )

    # Risk level changes over time
    risk_changes = (
        db.query(
            func.date(RiskScore.updated_at).label("date"),
            RiskScore.risk_level,
            func.count(RiskScore.user_hash).label("count"),
        )
        .filter(RiskScore.updated_at >= cutoff_date)
        .group_by(func.date(RiskScore.updated_at), RiskScore.risk_level)
        .all()
    )

    # Audit log action types distribution
    action_types = (
        db.query(AuditLog.action, func.count(AuditLog.id).label("count"))
        .filter(AuditLog.timestamp >= cutoff_date)
        .group_by(AuditLog.action)
        .order_by(desc(func.count(AuditLog.id)))
        .limit(20)
        .all()
    )

    return {
        "period_days": days,
        "user_growth": [
            {"date": str(date), "new_users": count} for date, count in new_users
        ],
        "daily_activity": [
            {"date": str(date), "events": count} for date, count in daily_events
        ],
        "risk_trends": [
            {"date": str(date), "risk_level": level, "count": count}
            for date, level, count in risk_changes
        ],
        "top_audit_actions": [
            {"action": action, "count": count} for action, count in action_types
        ],
    }


@router.post("/user/{user_hash}/role")
def update_user_role(
    user_hash: str,
    new_role: str,
    current_user: UserIdentity = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Update a user's role (admin only).

    Valid roles: employee, manager, admin
    """
    valid_roles = ["employee", "manager", "admin"]

    if new_role not in valid_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role. Must be one of: {', '.join(valid_roles)}",
        )

    user = db.query(UserIdentity).filter_by(user_hash=user_hash).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    old_role = user.role
    user.role = new_role

    # Log the change
    audit_log = AuditLog(
        user_hash=user_hash,
        action="role_updated",
        details={
            "old_role": old_role,
            "new_role": new_role,
            "updated_by": current_user.user_hash,
        },
    )
    db.add(audit_log)
    db.commit()

    return {
        "message": "User role updated successfully",
        "user_hash": user_hash,
        "old_role": old_role,
        "new_role": new_role,
    }


@router.post("/user/{user_hash}/manager")
def assign_manager(
    user_hash: str,
    manager_hash: str,
    current_user: UserIdentity = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Assign a manager to an employee (admin only).
    """
    # Verify user exists
    user = db.query(UserIdentity).filter_by(user_hash=user_hash).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    # Verify manager exists and is actually a manager
    manager = (
        db.query(UserIdentity).filter_by(user_hash=manager_hash, role="manager").first()
    )

    if not manager:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Manager not found or user is not a manager",
        )

    # Prevent assigning user as their own manager
    if user_hash == manager_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot assign user as their own manager",
        )

    old_manager = user.manager_hash
    user.manager_hash = manager_hash

    # Log the change
    audit_log = AuditLog(
        user_hash=user_hash,
        action="manager_assigned",
        details={
            "old_manager": old_manager,
            "new_manager": manager_hash,
            "assigned_by": current_user.user_hash,
        },
    )
    db.add(audit_log)
    db.commit()

    return {
        "message": "Manager assigned successfully",
        "user_hash": user_hash,
        "manager_hash": manager_hash,
        "old_manager": old_manager,
    }


@router.delete("/user/{user_hash}")
def delete_user(
    user_hash: str,
    current_user: UserIdentity = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Delete a user and all associated data (admin only).
    """
    user = db.query(UserIdentity).filter_by(user_hash=user_hash).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    if user_hash == current_user.user_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )

    user_hash_deleted = user.user_hash

    db.query(RiskScore).filter_by(user_hash=user_hash).delete()
    db.query(RiskHistory).filter_by(user_hash=user_hash).delete()
    db.query(AuditLog).filter_by(user_hash=user_hash).delete()

    db.delete(user)
    db.commit()

    return {
        "message": "User deleted successfully",
        "user_hash": user_hash_deleted,
    }


@router.put("/user/{user_hash}")
def update_user(
    user_hash: str,
    email: Optional[str] = None,
    current_user: UserIdentity = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Update user profile (admin only).
    """
    user = db.query(UserIdentity).filter_by(user_hash=user_hash).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    changes = {}

    if email is not None:
        from app.core.security import privacy

        user.email_encrypted = privacy.encrypt(email)
        changes["email"] = "updated"

    audit_log = AuditLog(
        user_hash=user_hash,
        action="profile_updated",
        details={
            "changes": changes,
            "updated_by": current_user.user_hash,
        },
    )
    db.add(audit_log)
    db.commit()

    return {
        "message": "User updated successfully",
        "user_hash": user_hash,
        "changes": changes,
    }


@router.get("/managers")
def get_managers(
    current_user: UserIdentity = Depends(require_role("admin", "manager")),
    db: Session = Depends(get_db),
):
    """
    Get list of all managers (for assigning to employees).
    """
    managers = db.query(UserIdentity).filter(UserIdentity.role == "manager").all()

    return {
        "managers": [
            {
                "user_hash": m.user_hash,
                "role": m.role,
            }
            for m in managers
        ]
    }


@router.get("/config", response_model=dict)
def get_system_config(current_user: UserIdentity = Depends(require_role("admin"))):
    """
    Get current system configuration.

    Note: Does not return sensitive values like encryption keys.
    """
    from app.config import get_settings

    settings = get_settings()

    return {
        "environment": settings.environment
        if hasattr(settings, "environment")
        else "production",
        "features": {
            "monitoring_enabled": True,
            "nudges_enabled": True,
            "analytics_enabled": True,
        },
        "thresholds": {
            "critical_velocity": 2.5,
            "elevated_velocity": 1.5,
            "emergency_hours": 36,
        },
        "privacy": {
            "encryption_enabled": True,
            "anonymization_enabled": True,
            "audit_logging_enabled": True,
        },
    }
