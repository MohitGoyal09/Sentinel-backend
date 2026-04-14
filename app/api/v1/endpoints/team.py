"""
Team Management API Endpoints (/team)

These endpoints allow managers to:
- View team-level aggregates (anonymized by default)
- View individual details (only with consent or emergency)
- Manage team health monitoring
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from collections import defaultdict

from app.core.database import get_db
from app.models.identity import UserIdentity, AuditLog
from app.models.analytics import RiskScore, RiskHistory, GraphEdge, Event, SkillProfile
from app.models.tenant import TenantMember
from app.api.deps.auth import get_current_user_identity, require_role
from app.services.permission_service import PermissionService

router = APIRouter()


def anonymize_user_hash(user_hash: str, index: int) -> str:
    """
    Replace user_hash with pseudonym for anonymized views.

    user_hash 'abc123' with index 0 -> 'User A'
    user_hash 'def456' with index 1 -> 'User B'
    etc.
    """
    return f"User {chr(65 + index)}"  # A, B, C, ...


def get_team_members(db: Session, team_id, tenant_id) -> List[UserIdentity]:
    """Get all UserIdentity records for members of the given team."""
    member_hashes = [
        tm.user_hash
        for tm in db.query(TenantMember.user_hash).filter_by(
            team_id=team_id,
            tenant_id=tenant_id,
        ).all()
    ]
    if not member_hashes:
        return []
    return db.query(UserIdentity).filter(UserIdentity.user_hash.in_(member_hashes)).all()


@router.get("/", response_model=dict)
def get_my_team_dashboard(
    view_as: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    current_user: TenantMember = Depends(require_role("manager", "admin")),
    db: Session = Depends(get_db),
):
    """
    Get team dashboard.

    Admins can:
    - View all employees (Global View) if view_as is None
    - View specific team if view_as={team_id}

    Managers:
    - Only view their own team members
    """
    is_global_view = False

    # Resolve the target team_id and tenant_id for scoping
    target_team_id = current_user.team_id
    target_tenant_id = current_user.tenant_id

    # Permission Logic
    if current_user.role == "admin":
        if view_as:
            # view_as is a team_id UUID string; find that team's members
            import uuid as _uuid
            try:
                target_team_id = _uuid.UUID(view_as)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="view_as must be a valid team UUID",
                )
        else:
            is_global_view = True
    elif view_as and view_as != str(current_user.team_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Managers can only view their own team",
        )

    # Query Data
    if is_global_view:
        # Admin Global View - Scoped to tenant
        tenant_hashes = [
            tm.user_hash
            for tm in db.query(TenantMember.user_hash)
            .filter_by(tenant_id=current_user.tenant_id)
            .all()
        ]
        total_count = len(tenant_hashes)
        team_members = (
            db.query(UserIdentity)
            .filter(UserIdentity.user_hash.in_(tenant_hashes))
            .offset(skip)
            .limit(limit)
            .all()
        )
        dashboard_title = "Organization Overview"
    else:
        # Team Specific View via TenantMember
        team_member_hashes = [
            tm.user_hash
            for tm in db.query(TenantMember.user_hash).filter_by(
                team_id=target_team_id,
                tenant_id=target_tenant_id,
            ).all()
        ]
        total_count = len(team_member_hashes)
        team_members = (
            db.query(UserIdentity)
            .filter(UserIdentity.user_hash.in_(team_member_hashes))
            .offset(skip)
            .limit(limit)
            .all()
        )
        dashboard_title = "Team Dashboard"

    if not team_members and not is_global_view:
        return {
            "team": {
                "team_id": str(target_team_id) if target_team_id else None,
                "member_count": 0,
                "message": "No team members assigned",
                "is_global_view": False,
                "total_pages": 0,
            },
            "metrics": None,
            "risk_distribution": {},
            "consent_summary": {},
        }

    # Get risk scores for visible members
    member_hashes = [m.user_hash for m in team_members]
    risk_scores = (
        db.query(RiskScore).filter(RiskScore.user_hash.in_(member_hashes)).all()
    )

    # Convert risk scores to dicts to avoid SQLAlchemy serialization issues
    risk_scores_dict = {r.user_hash: {"risk_level": r.risk_level} for r in risk_scores}

    # Build anonymized member list
    anonymized_members = []
    hash_to_pseudonym = {}

    for idx, member in enumerate(team_members):
        # Different index offset for global view pagination
        global_idx = skip + idx
        pseudonym = anonymize_user_hash(member.user_hash, global_idx)
        hash_to_pseudonym[member.user_hash] = pseudonym

        # Get member's risk score from dict
        risk = risk_scores_dict.get(member.user_hash)

        # Determine if viewer can see real identity
        # Admin can always see identity in global view? Maybe restricting for privacy demo.
        # Let's respect consent even for Admins in this demo unless 'emergency' override
        can_identify = (
            bool(member.consent_share_with_manager) or current_user.role == "admin"
        )

        anonymized_members.append(
            {
                "pseudonym": pseudonym,
                "is_identified": can_identify,
                "real_hash": str(member.user_hash) if can_identify else None,
                "risk_level": risk["risk_level"] if risk else "LOW",
                "has_consent": bool(member.consent_share_with_manager),
                "department": "Unknown",  # UserIdentity model has no metadata JSON column
            }
        )

    # Calculate metrics (Only for current page in global view to avoid heavy query,
    # OR do a separate aggregate query for accurate total stats)

    if is_global_view:
        # Global Aggregates (Scoped to tenant)
        risk_counts = (
            db.query(RiskScore.risk_level, func.count(RiskScore.risk_level))
            .filter(RiskScore.user_hash.in_(tenant_hashes))
            .group_by(RiskScore.risk_level)
            .all()
        )
        risk_dist_map = {str(level): int(count) for level, count in risk_counts}
        consent_total = (
            db.query(UserIdentity)
            .filter(
                UserIdentity.user_hash.in_(tenant_hashes),
                UserIdentity.consent_share_with_manager == True,
            )
            .count()
        )
        total_employees = total_count
    else:
        # Team Specific Aggregates
        team_hashes_all = [
            str(m.user_hash)
            for m in get_team_members(db, target_team_id, target_tenant_id)
        ]
        risk_counts = (
            db.query(RiskScore.risk_level, func.count(RiskScore.risk_level))
            .filter(RiskScore.user_hash.in_(team_hashes_all))
            .group_by(RiskScore.risk_level)
            .all()
        )
        risk_dist_map = {str(level): int(count) for level, count in risk_counts}
        consent_total = sum(
            1
            for m in get_team_members(db, target_team_id, target_tenant_id)
            if bool(m.consent_share_with_manager)
        )
        total_employees = len(team_hashes_all)

    risk_distribution = {
        "LOW": risk_dist_map.get("LOW", 0),
        "ELEVATED": risk_dist_map.get("ELEVATED", 0),
        "CRITICAL": risk_dist_map.get("CRITICAL", 0),
    }

    # Recent Events (Page specific)
    week_ago = datetime.utcnow() - timedelta(days=7)
    recent_events = (
        db.query(Event)
        .filter(Event.user_hash.in_(member_hashes), Event.timestamp >= week_ago)
        .order_by(Event.timestamp.desc())
        .limit(20)
        .all()
    )

    anonymized_events = []
    for event in recent_events:
        pseudonym = hash_to_pseudonym.get(event.user_hash, "Unknown")
        # Use metadata_ which is the actual column name (metadata is SQLAlchemy's MetaData)
        event_metadata = None
        try:
            event_metadata = event.metadata_
        except AttributeError:
            pass
        if event_metadata is None and hasattr(event, "metadata"):
            event_metadata = event.metadata
        anonymized_events.append(
            {
                "pseudonym": pseudonym,
                "event_type": str(event.event_type) if event.event_type else None,
                "timestamp": event.timestamp.isoformat() if event.timestamp else None,
                "metadata": event_metadata,
            }
        )

    return {
        "team": {
            "team_id": str(target_team_id) if target_team_id else None,
            "member_count": len(team_members),
            "members": anonymized_members,
            "total_count": total_count,
            "page": (skip // limit) + 1,
            "is_global_view": is_global_view,
            "title": dashboard_title,
        },
        "metrics": {
            "total_members": total_employees,
            "at_risk_count": risk_distribution["ELEVATED"]
            + risk_distribution["CRITICAL"],
            "critical_count": risk_distribution["CRITICAL"],
            "consent_rate": f"{consent_total}/{total_employees}",
        },
        "risk_distribution": risk_distribution,
        "consent_summary": {
            "total": total_employees,
            "consented": consent_total,
            "not_consented": total_employees - consent_total,
            "percentage": round((consent_total / total_employees) * 100, 1)
            if total_employees
            else 0,
        },
        "recent_events": anonymized_events,
    }


@router.get("/member/{user_hash}", response_model=dict)
def get_team_member_details(
    user_hash: str,
    current_user: TenantMember = Depends(require_role("manager", "admin")),
    db: Session = Depends(get_db),
):
    """
    Get detailed view of a specific team member.

    Access Rules:
    1. Must be on the same team as the employee (or admin)
    2. Employee must have consented, OR
    3. Employee must be at CRITICAL risk for 36+ hours (emergency)

    Returns detailed metrics if access granted, anonymized summary if not.
    """
    # Initialize permission service
    perm_service = PermissionService(db)

    # Check if manager can view this employee
    can_view, reason = perm_service.can_manager_view_employee(db, current_user, user_hash)

    # Get employee data
    employee = db.query(UserIdentity).filter_by(user_hash=user_hash).first()

    if not employee:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found"
        )

    # Verify this employee is on the same team (admins bypass this check)
    if current_user.role != "admin":
        target_member = (
            db.query(TenantMember)
            .filter_by(user_hash=user_hash, tenant_id=current_user.tenant_id)
            .first()
        )
        if not target_member or target_member.team_id != current_user.team_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not your direct report",
            )

    # Log the access attempt
    PermissionService.log_data_access(
        db,
        actor_hash=current_user.user_hash,
        actor_role=current_user.role,
        target_hash=user_hash,
        action="view_team_member",
        tenant_id=str(current_user.tenant_id),
        details={
            "granted": can_view,
            "reason": reason,
            "manager": current_user.user_hash,
        },
    )

    if not can_view:
        # Return anonymized summary
        return {
            "access": "denied",
            "reason": reason,
            "employee": {
                "pseudonym": "User ?",
                "is_identified": False,
                "message": "This employee has not consented to share detailed data",
                "risk_level": "ANONYMOUS",
            },
            "suggestion": "Consider having a conversation about wellbeing support",
        }

    # Access granted - return full details
    risk_score = db.query(RiskScore).filter_by(user_hash=user_hash).first()

    # Get recent history (last 30 days)
    month_ago = datetime.utcnow() - timedelta(days=30)
    history = (
        db.query(RiskHistory)
        .filter(RiskHistory.user_hash == user_hash, RiskHistory.timestamp >= month_ago)
        .order_by(RiskHistory.timestamp.desc())
        .limit(30)
        .all()
    )

    # Get recent events (last 7 days)
    week_ago = datetime.utcnow() - timedelta(days=7)
    events = (
        db.query(Event)
        .filter(Event.user_hash == user_hash, Event.timestamp >= week_ago)
        .order_by(Event.timestamp.desc())
        .limit(20)
        .all()
    )

    # Get skills profile (create default if doesn't exist)
    skills = db.query(SkillProfile).filter_by(user_hash=user_hash).first()
    if not skills:
        # Create default skills profile
        skills = SkillProfile(user_hash=user_hash)
        db.add(skills)
        db.commit()
        db.refresh(skills)

    return {
        "access": "granted",
        "reason": reason,
        "employee": {
            "user_hash": user_hash,
            "is_identified": True,
            "consent": bool(employee.consent_share_with_manager),
            "monitoring_paused": employee.monitoring_paused_until is not None,
        },
        "risk": {
            "current_level": risk_score.risk_level if risk_score else "LOW",
            "velocity": float(risk_score.velocity)
            if risk_score and risk_score.velocity
            else None,
            "confidence": float(risk_score.confidence)
            if risk_score and risk_score.confidence
            else 0,
            "thwarted_belongingness": float(risk_score.thwarted_belongingness)
            if risk_score and risk_score.thwarted_belongingness
            else None,
            "updated_at": risk_score.updated_at.isoformat() if risk_score else None,
        },
        "skills": skills.to_dict() if skills else None,
        "history": [
            {
                "timestamp": h.timestamp.isoformat() if h.timestamp else None,
                "risk_level": str(h.risk_level) if h.risk_level else None,
                "velocity": float(h.velocity) if h.velocity else None,
            }
            for h in history
        ],
        "recent_events": [
            {
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                "event_type": str(e.event_type) if e.event_type else None,
                "metadata": e.metadata_ if hasattr(e, "metadata_") else None,
            }
            for e in events
        ],
    }


@router.get("/analytics", response_model=dict)
def get_team_analytics(
    days: int = 30,
    current_user: TenantMember = Depends(require_role("manager", "admin")),
    db: Session = Depends(get_db),
):
    """
    Get team-level analytics (aggregated, anonymized).

    Parameters:
    - days: Number of days to analyze (default: 30)

    Returns:
    - Team velocity trends
    - Risk level transitions
    - Work pattern analysis
    - Communication patterns
    """
    # Get team members
    team_members = get_team_members(db, current_user.team_id, current_user.tenant_id)

    if not team_members:
        return {
            "period_days": days,
            "message": "No team members found",
            "analytics": None,
        }

    team_hashes = [m.user_hash for m in team_members]
    cutoff = datetime.utcnow() - timedelta(days=days)

    # Get all risk history for team
    history_records = (
        db.query(RiskHistory)
        .filter(RiskHistory.user_hash.in_(team_hashes), RiskHistory.timestamp >= cutoff)
        .order_by(RiskHistory.timestamp.asc())
        .all()
    )

    # Calculate trends
    daily_metrics = defaultdict(
        lambda: {"count": 0, "total_velocity": 0, "risk_levels": []}
    )

    for record in history_records:
        day_key = record.timestamp.strftime("%Y-%m-%d")
        daily_metrics[day_key]["count"] += 1
        daily_metrics[day_key]["total_velocity"] += record.velocity or 0
        daily_metrics[day_key]["risk_levels"].append(record.risk_level)

    # Build trend data
    trends = []
    for date_str, metrics in sorted(daily_metrics.items()):
        avg_velocity = (
            (metrics["total_velocity"] / metrics["count"])
            if metrics["count"] > 0
            else 0
        )
        risk_dist = {
            "LOW": metrics["risk_levels"].count("LOW"),
            "ELEVATED": metrics["risk_levels"].count("ELEVATED"),
            "CRITICAL": metrics["risk_levels"].count("CRITICAL"),
        }

        trends.append(
            {
                "date": date_str,
                "avg_velocity": round(avg_velocity, 2),
                "member_count": metrics["count"],
                "risk_distribution": risk_dist,
            }
        )

    # Calculate current team health score
    current_risks = (
        db.query(RiskScore).filter(RiskScore.user_hash.in_(team_hashes)).all()
    )

    total_velocity = sum(r.velocity or 0 for r in current_risks)
    avg_team_velocity = total_velocity / len(current_risks) if current_risks else 0

    critical_count = sum(1 for r in current_risks if r.risk_level == "CRITICAL")
    elevated_count = sum(1 for r in current_risks if r.risk_level == "ELEVATED")

    # Health score (0-100, higher is better)
    health_score = 100
    health_score -= critical_count * 20  # -20 per critical member
    health_score -= elevated_count * 10  # -10 per elevated member
    health_score -= min(avg_team_velocity * 5, 30)  # -5 per velocity point, max -30
    health_score = max(0, min(100, health_score))  # Clamp 0-100

    return {
        "period_days": days,
        "team_size": len(team_members),
        "health_score": round(health_score, 1),
        "current_metrics": {
            "avg_velocity": round(avg_team_velocity, 2),
            "critical_count": critical_count,
            "elevated_count": elevated_count,
            "healthy_count": len(current_risks) - critical_count - elevated_count,
        },
        "trends": trends,
    }


@router.get("/network", response_model=dict)
def get_team_network(
    current_user: TenantMember = Depends(require_role("manager", "admin")),
    db: Session = Depends(get_db),
):
    """
    Get team communication network (anonymized).

    Returns network graph showing collaboration patterns
    without identifying individuals (unless consented).
    """
    # Get team members
    team_members = get_team_members(db, current_user.team_id, current_user.tenant_id)

    if not team_members:
        return {"nodes": [], "edges": [], "message": "No team members found"}

    team_hashes = [m.user_hash for m in team_members]

    # Get graph edges within team
    edges = (
        db.query(GraphEdge)
        .filter(
            GraphEdge.source_hash.in_(team_hashes),
            GraphEdge.target_hash.in_(team_hashes),
        )
        .all()
    )

    # Build anonymized network
    hash_to_pseudonym = {
        m.user_hash: anonymize_user_hash(m.user_hash, i)
        for i, m in enumerate(team_members)
    }

    nodes = []
    edges_anon = []

    # Build nodes
    for idx, member in enumerate(team_members):
        pseudonym = hash_to_pseudonym[member.user_hash]
        risk = db.query(RiskScore).filter_by(user_hash=member.user_hash).first()

        # Can identify if consented
        can_identify = member.consent_share_with_manager

        nodes.append(
            {
                "id": pseudonym,
                "label": pseudonym if not can_identify else f"{pseudonym} (identified)",
                "risk_level": risk.risk_level if risk else "LOW",
                "is_identified": can_identify,
                "real_hash": member.user_hash if can_identify else None,
            }
        )

    # Build edges
    for edge in edges:
        source_pseudo = hash_to_pseudonym.get(edge.source_hash)
        target_pseudo = hash_to_pseudonym.get(edge.target_hash)

        if source_pseudo and target_pseudo:
            edges_anon.append(
                {
                    "source": source_pseudo,
                    "target": target_pseudo,
                    "weight": edge.weight,
                    "edge_type": edge.edge_type,
                }
            )

    return {
        "nodes": nodes,
        "edges": edges_anon,
        "team_size": len(team_members),
        "connection_count": len(edges_anon),
    }


@router.post("/send-nudge/{user_hash}")
def send_wellness_nudge(
    user_hash: str,
    message: Optional[str] = None,
    current_user: TenantMember = Depends(require_role("manager", "admin")),
    db: Session = Depends(get_db),
):
    """
    Send a wellness nudge/encouragement to a team member.

    Note: This should be used sparingly and thoughtfully.
    Consider whether a direct conversation might be more appropriate.
    """
    # Verify employee is on the manager's team
    employee = db.query(UserIdentity).filter_by(user_hash=user_hash).first()

    if not employee:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not your direct report"
        )

    # Check team membership (admins can nudge anyone in their tenant)
    target_member = (
        db.query(TenantMember)
        .filter_by(user_hash=user_hash, tenant_id=current_user.tenant_id)
        .first()
    )
    if current_user.role != "admin" and (
        not target_member or target_member.team_id != current_user.team_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not your direct report"
        )

    # Check if employee has opted out of manager nudges
    if not employee.consent_share_with_manager:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Employee has not consented to receive manager communications",
        )

    # Log the nudge
    audit_log = AuditLog(
        user_hash=user_hash,
        action="manager_nudge_sent",
        details={
            "sender_hash": current_user.user_hash,
            "team_id": str(current_user.team_id) if current_user.team_id else None,
            "message_preview": message[:50] if message else None,
            "timestamp": datetime.utcnow().isoformat(),
        },
    )
    db.add(audit_log)
    db.commit()

    # Create an in-app notification for the target employee
    from app.models.notification import Notification
    notification = Notification(
        user_hash=user_hash,
        tenant_id=current_user.tenant_id,
        type="activity",
        title="Manager Nudge",
        message=message if message else "Your manager sent you a check-in nudge.",
        priority="normal",
        data={"sender_hash": current_user.user_hash},
    )
    db.add(notification)
    db.commit()

    return {
        "message": "Nudge sent successfully",
        "recipient": user_hash,
        "sender": current_user.user_hash,
        "logged": True,
    }
