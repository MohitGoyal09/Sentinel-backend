"""
Admin API Endpoints (/admin)

System administration endpoints for:
- System health monitoring
- System-wide audit logs
- User management
- Configuration management
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, Integer
from datetime import datetime, timedelta
from typing import List, Optional, Dict

from app.core.database import get_db
from app.models.identity import UserIdentity, AuditLog
from app.models.analytics import RiskScore, RiskHistory, Event
from app.models.tenant import TenantMember
from app.api.deps.auth import get_tenant_member, require_role
from app.services.permission_service import PermissionService
from app.services.audit_service import AuditService, AuditAction

router = APIRouter()


@router.get("/health", response_model=dict)
def get_system_health(
    member: TenantMember = Depends(require_role("admin")),
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
    # Subquery: user_hashes belonging to this tenant
    tenant_user_hashes = (
        db.query(TenantMember.user_hash)
        .filter(TenantMember.tenant_id == member.tenant_id)
        .subquery()
    )

    # Database statistics — scoped to this tenant
    total_users = (
        db.query(UserIdentity)
        .filter(UserIdentity.tenant_id == member.tenant_id)
        .count()
    )
    total_events = (
        db.query(Event)
        .filter(Event.user_hash.in_(db.query(tenant_user_hashes.c.user_hash)))
        .count()
    )
    total_audit_logs = (
        db.query(AuditLog)
        .filter(AuditLog.user_hash.in_(db.query(tenant_user_hashes.c.user_hash)))
        .count()
    )
    total_risk_scores = (
        db.query(RiskScore)
        .filter(RiskScore.user_hash.in_(db.query(tenant_user_hashes.c.user_hash)))
        .count()
    )

    # User distribution by role — scoped to this tenant via TenantMember
    role_distribution = (
        db.query(TenantMember.role, func.count(TenantMember.id).label("count"))
        .filter_by(tenant_id=member.tenant_id)
        .group_by(TenantMember.role)
        .all()
    )

    # Consent statistics — scoped to this tenant
    consent_stats = (
        db.query(
            func.sum(func.cast(UserIdentity.consent_share_with_manager, Integer)).label(
                "consented"
            ),
            func.count(UserIdentity.user_hash).label("total"),
        )
        .filter(UserIdentity.tenant_id == member.tenant_id)
        .first()
    )

    # Risk distribution — scoped to this tenant
    risk_distribution = (
        db.query(RiskScore.risk_level, func.count(RiskScore.user_hash).label("count"))
        .filter(RiskScore.user_hash.in_(db.query(tenant_user_hashes.c.user_hash)))
        .group_by(RiskScore.risk_level)
        .all()
    )

    # Recent activity (last 24 hours) — scoped to this tenant
    day_ago = datetime.utcnow() - timedelta(hours=24)
    recent_events_24h = (
        db.query(Event)
        .filter(
            Event.timestamp >= day_ago,
            Event.user_hash.in_(db.query(tenant_user_hashes.c.user_hash)),
        )
        .count()
    )
    recent_audit_logs_24h = (
        db.query(AuditLog)
        .filter(
            AuditLog.timestamp >= day_ago,
            AuditLog.user_hash.in_(db.query(tenant_user_hashes.c.user_hash)),
        )
        .count()
    )

    # Critical users count — scoped to this tenant
    critical_count = (
        db.query(RiskScore)
        .filter(
            RiskScore.risk_level == "CRITICAL",
            RiskScore.user_hash.in_(db.query(tenant_user_hashes.c.user_hash)),
        )
        .count()
    )
    elevated_count = (
        db.query(RiskScore)
        .filter(
            RiskScore.risk_level == "ELEVATED",
            RiskScore.user_hash.in_(db.query(tenant_user_hashes.c.user_hash)),
        )
        .count()
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
    days: int = Query(default=7, ge=1, le=90),
    action_type: Optional[str] = None,
    user_hash: Optional[str] = None,
    limit: int = Query(default=100, le=1000),
    offset: int = 0,
    member: TenantMember = Depends(require_role("admin")),
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

    # Build query — tenant-scoped
    query = db.query(AuditLog).filter(
        AuditLog.timestamp >= cutoff_date,
        AuditLog.tenant_id == str(member.tenant_id),
    )

    if action_type:
        query = query.filter(AuditLog.action == action_type)

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
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = 0,
    member: TenantMember = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Get all users in the system with their status.
    Admin sees email addresses, managers see hashes only (privacy protected).
    """
    # Join UserIdentity with TenantMember scoped to this tenant
    query = db.query(UserIdentity, TenantMember).outerjoin(
        TenantMember,
        (TenantMember.user_hash == UserIdentity.user_hash)
        & (TenantMember.tenant_id == member.tenant_id),
    )

    if role:
        query = query.filter(TenantMember.role == role)

    total_count = query.count()
    results = query.offset(offset).limit(limit).all()

    user_hashes = [row.UserIdentity.user_hash for row in results]
    risk_scores = db.query(RiskScore).filter(RiskScore.user_hash.in_(user_hashes)).all()
    risk_map = {r.user_hash: r for r in risk_scores}

    # Build team name lookup
    from app.models.team import Team
    from app.core.security import privacy

    team_ids = {row.TenantMember.team_id for row in results if row.TenantMember and row.TenantMember.team_id}
    teams = db.query(Team).filter(Team.id.in_(team_ids)).all() if team_ids else []
    team_map = {str(t.id): t.name for t in teams}

    formatted_users = []
    for row in results:
        user = row.UserIdentity
        tm = row.TenantMember
        risk = risk_map.get(user.user_hash)

        # Use display_name from TenantMember, fall back to email prefix
        name = tm.display_name if tm and tm.display_name else None
        if not name:
            decrypted_email = privacy.decrypt(user.email_encrypted) if user.email_encrypted else ""
            name = decrypted_email.split("@")[0].replace(".", " ").title() if decrypted_email else user.user_hash[:8]

        decrypted_email = privacy.decrypt(user.email_encrypted) if user.email_encrypted else None

        # Get team name
        team_name = None
        if tm and tm.team_id:
            team_name = team_map.get(str(tm.team_id))

        formatted_users.append(
            {
                "user_hash": user.user_hash,
                "email": decrypted_email,
                "name": name,
                "role": tm.role if tm else "employee",
                "team_name": team_name,
                "team_id": str(tm.team_id) if tm and tm.team_id else None,
                "consent_share_with_manager": user.consent_share_with_manager,
                "consent_share_anonymized": user.consent_share_anonymized,
                "monitoring_paused": user.monitoring_paused_until is not None,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "risk_level": risk.risk_level if risk else "LOW",
                "velocity": risk.velocity if risk else None,
                "last_updated": risk.updated_at.isoformat() if risk else None,
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
    member: TenantMember = Depends(require_role("admin")),
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

    # Subquery: user_hashes belonging to this tenant
    tenant_user_hashes = (
        db.query(TenantMember.user_hash)
        .filter(TenantMember.tenant_id == member.tenant_id)
        .subquery()
    )

    # User growth (new users per day) — tenant-scoped
    new_users = (
        db.query(
            func.date(UserIdentity.created_at).label("date"),
            func.count(UserIdentity.user_hash).label("count"),
        )
        .filter(
            UserIdentity.created_at >= cutoff_date,
            UserIdentity.user_hash.in_(db.query(tenant_user_hashes.c.user_hash)),
        )
        .group_by(func.date(UserIdentity.created_at))
        .all()
    )

    # Daily activity (events per day) — tenant-scoped
    daily_events = (
        db.query(
            func.date(Event.timestamp).label("date"),
            func.count(Event.id).label("count"),
        )
        .filter(
            Event.timestamp >= cutoff_date,
            Event.user_hash.in_(db.query(tenant_user_hashes.c.user_hash)),
        )
        .group_by(func.date(Event.timestamp))
        .all()
    )

    # Risk level changes over time — tenant-scoped
    risk_changes = (
        db.query(
            func.date(RiskScore.updated_at).label("date"),
            RiskScore.risk_level,
            func.count(RiskScore.user_hash).label("count"),
        )
        .filter(
            RiskScore.updated_at >= cutoff_date,
            RiskScore.user_hash.in_(db.query(tenant_user_hashes.c.user_hash)),
        )
        .group_by(func.date(RiskScore.updated_at), RiskScore.risk_level)
        .all()
    )

    # Audit log action types distribution — tenant-scoped
    action_types = (
        db.query(AuditLog.action, func.count(AuditLog.id).label("count"))
        .filter(
            AuditLog.timestamp >= cutoff_date,
            AuditLog.tenant_id == str(member.tenant_id),
        )
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
    member: TenantMember = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Update a user's role (admin only).

    Valid roles: employee, manager, admin
    Updates TenantMember.role for the target user in this tenant.
    """
    valid_roles = ["employee", "manager", "admin"]

    if new_role not in valid_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role. Must be one of: {', '.join(valid_roles)}",
        )

    # Verify target user exists in this tenant
    target_member = (
        db.query(TenantMember)
        .filter_by(user_hash=user_hash, tenant_id=member.tenant_id)
        .first()
    )

    if not target_member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found in this organization",
        )

    old_role = target_member.role
    target_member.role = new_role

    # Log the change
    audit = AuditService(db)
    audit.log(
        actor_hash=member.user_hash,
        actor_role=member.role,
        action=AuditAction.ROLE_CHANGED,
        target_hash=user_hash,
        details={"old_role": old_role, "new_role": new_role},
        tenant_id=member.tenant_id,
    )
    db.commit()

    return {
        "message": "User role updated successfully",
        "user_hash": user_hash,
        "old_role": old_role,
        "new_role": new_role,
    }


@router.post("/user/{user_hash}/team")
def assign_team(
    user_hash: str,
    team_id: str,
    member: TenantMember = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Assign a user to a team (admin only).
    Replaces the deprecated manager-assignment pattern — team membership
    is now the source of truth for who reports to whom.
    """
    from app.models.team import Team
    import uuid as uuid_lib

    # Validate team_id is a valid UUID
    try:
        parsed_team_id = uuid_lib.UUID(team_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid team_id format",
        )

    # Verify the team exists within this tenant
    team = (
        db.query(Team)
        .filter_by(id=parsed_team_id, tenant_id=member.tenant_id)
        .first()
    )
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found in this organization",
        )

    # Verify the target user is a member of this tenant
    target_member = (
        db.query(TenantMember)
        .filter_by(user_hash=user_hash, tenant_id=member.tenant_id)
        .first()
    )
    if not target_member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found in this organization",
        )

    old_team_id = str(target_member.team_id) if target_member.team_id else None
    target_member.team_id = parsed_team_id

    # Log the change
    audit = AuditService(db)
    audit.log(
        actor_hash=member.user_hash,
        actor_role=member.role,
        action=AuditAction.TEAM_MODIFIED,
        target_hash=user_hash,
        details={
            "old_team_id": old_team_id,
            "new_team_id": team_id,
        },
        tenant_id=member.tenant_id,
    )
    db.commit()

    return {
        "message": "User assigned to team successfully",
        "user_hash": user_hash,
        "team_id": team_id,
        "old_team_id": old_team_id,
    }


@router.delete("/user/{user_hash}")
def delete_user(
    user_hash: str,
    member: TenantMember = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Delete a user and all associated data (admin only).
    """
    # Verify target user belongs to admin's tenant
    target_member = (
        db.query(TenantMember)
        .filter_by(user_hash=user_hash, tenant_id=member.tenant_id)
        .first()
    )
    if not target_member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found in this organization",
        )

    user = db.query(UserIdentity).filter_by(user_hash=user_hash).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    if user_hash == member.user_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )

    user_hash_deleted = user.user_hash

    # Delete all related data in correct order (FK constraints)
    from app.models.notification import Notification, NotificationPreference
    from app.models.analytics import Event, GraphEdge, CentralityScore, SkillProfile
    from app.models.chat_history import ChatHistory, ChatSession

    db.query(NotificationPreference).filter_by(user_hash=user_hash).delete()
    db.query(Notification).filter_by(user_hash=user_hash).delete()
    db.query(ChatHistory).filter_by(user_hash=user_hash).delete()
    db.query(ChatSession).filter_by(user_hash=user_hash).delete()
    db.query(Event).filter_by(user_hash=user_hash).delete()
    db.query(GraphEdge).filter_by(source_hash=user_hash).delete()
    db.query(GraphEdge).filter_by(target_hash=user_hash).delete()
    db.query(CentralityScore).filter_by(user_hash=user_hash).delete()
    db.query(SkillProfile).filter_by(user_hash=user_hash).delete()
    db.query(RiskScore).filter_by(user_hash=user_hash).delete()
    db.query(RiskHistory).filter_by(user_hash=user_hash).delete()
    db.query(AuditLog).filter_by(user_hash=user_hash).delete()
    db.query(TenantMember).filter_by(user_hash=user_hash).delete()

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
    member: TenantMember = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """
    Update user profile (admin only).
    """
    # Verify target user belongs to admin's tenant
    target_member = (
        db.query(TenantMember)
        .filter_by(user_hash=user_hash, tenant_id=member.tenant_id)
        .first()
    )
    if not target_member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found in this organization",
        )

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
            "updated_by": member.user_hash,
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
    member: TenantMember = Depends(require_role("admin", "manager")),
    db: Session = Depends(get_db),
):
    """
    Get list of all managers in this tenant (for assigning to teams).
    """
    managers = (
        db.query(TenantMember)
        .filter_by(tenant_id=member.tenant_id, role="manager")
        .all()
    )

    return {
        "managers": [
            {
                "user_hash": m.user_hash,
                "role": m.role,
            }
            for m in managers
        ]
    }


@router.get("/users/search")
def search_users(
    q: str = "",
    role: str = "",
    sort_by: str = "created_at",
    sort_order: str = "desc",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
    member: TenantMember = Depends(require_role("admin")),
):
    """Search and filter users with pagination."""
    from app.core.response import success_response

    # Join UserIdentity with TenantMember scoped to this tenant
    query = db.query(UserIdentity, TenantMember.role).outerjoin(
        TenantMember,
        (TenantMember.user_hash == UserIdentity.user_hash)
        & (TenantMember.tenant_id == member.tenant_id),
    )

    if q:
        query = query.filter(UserIdentity.user_hash.contains(q.lower()))

    if role:
        query = query.filter(TenantMember.role == role)

    total = query.count()

    ALLOWED_SORT_COLS = {"created_at", "role", "user_hash"}
    if sort_by in ALLOWED_SORT_COLS and hasattr(UserIdentity, sort_by):
        col = getattr(UserIdentity, sort_by)
        query = query.order_by(col.desc() if sort_order == "desc" else col.asc())

    results = query.offset(offset).limit(limit).all()

    return success_response(
        {
            "users": [
                {
                    "user_hash": row.UserIdentity.user_hash,
                    "role": row.role,
                    "created_at": row.UserIdentity.created_at.isoformat()
                    if row.UserIdentity.created_at
                    else None,
                    "consent_share_with_manager": row.UserIdentity.consent_share_with_manager,
                    "consent_share_anonymized": row.UserIdentity.consent_share_anonymized,
                    "monitoring_paused": row.UserIdentity.monitoring_paused_until is not None,
                }
                for row in results
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    )


@router.get("/config", response_model=dict)
def get_system_config(member: TenantMember = Depends(require_role("admin"))):
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


@router.get("/pipeline/health")
def get_pipeline_health(
    member: TenantMember = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Pipeline health dashboard for admins (spec Section 6)."""

    from app.api.v1.endpoints.ingestion import _pipeline_metrics

    # DB-backed metrics
    try:
        total_db_events = db.query(func.count(Event.id)).scalar() or 0
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        events_last_hour = (
            db.query(func.count(Event.id))
            .filter(Event.timestamp >= one_hour_ago)
            .scalar() or 0
        )
    except Exception:
        total_db_events = 0
        events_last_hour = 0

    total_ingested = _pipeline_metrics.get("total_ingested", 0)
    total_errors = _pipeline_metrics.get("total_errors", 0)
    pipeline_start_iso = _pipeline_metrics.get("pipeline_start_time", datetime.utcnow().isoformat())

    try:
        uptime_hours = round(
            (datetime.utcnow() - datetime.fromisoformat(pipeline_start_iso)).total_seconds() / 3600, 1
        )
    except Exception:
        uptime_hours = 0.0

    total_processed = total_db_events + total_ingested
    error_rate_pct = round(total_errors / max(total_processed, 1) * 100, 2)

    # Per-stage health
    stage_metrics = _pipeline_metrics.get("stage_metrics", {})

    def _stage_health(stage_name: str, processed: int) -> str:
        sm = stage_metrics.get(stage_name, {})
        if sm.get("error_count", 0) > 0:
            return "degraded"
        if processed == 0 and sm.get("processed", 0) == 0:
            return "unknown"
        return "ok"

    stage_names = ["Collection", "Validation", "Privacy Layer", "Storage", "Engine Processing"]
    stage_descriptions = [
        "Webhooks & API polling from connected sources",
        "Schema validation, deduplication, timestamp normalization",
        "HMAC hashing, AES-256 encryption, PII removal",
        "Dual-vault architecture (Vault A: analytics, Vault B: identity)",
        "Safety Valve, Talent Scout, Culture Thermometer analysis",
    ]

    stages = []
    for name, desc in zip(stage_names, stage_descriptions):
        sm = stage_metrics.get(name, {})
        processed = sm.get("processed", 0) or total_db_events
        stages.append({
            "name": name,
            "status": "active",
            "processed": processed,
            "error_count": sm.get("error_count", 0),
            "last_processed_at": sm.get("last_processed_at"),
            "health": _stage_health(name, processed),
            "description": desc,
        })

    degraded_count = sum(1 for s in stages if s["health"] == "degraded")
    overall_status = "healthy" if degraded_count == 0 else ("degraded" if degraded_count <= 2 else "critical")

    return {
        "pipeline_health": {
            "overall_status": overall_status,
            "checked_at": datetime.utcnow().isoformat(),
            "stages": stages,
            "summary": {
                "total_events_processed": total_processed,
                "total_errors": total_errors,
                "error_rate_pct": error_rate_pct,
                "uptime_hours": uptime_hours,
                "ingested_last_hour": events_last_hour,
            },
        }
    }
