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
from app.models.analytics import RiskScore, RiskHistory, GraphEdge, Event
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


def get_team_members(db: Session, manager_hash: str) -> List[UserIdentity]:
    """Get all employees who report to this manager."""
    return (
        db.query(UserIdentity).filter(UserIdentity.manager_hash == manager_hash).all()
    )


@router.get("/team", response_model=dict)
def get_my_team_dashboard(
    current_user: UserIdentity = Depends(require_role("manager", "admin")),
    db: Session = Depends(get_db),
):
    """
    Get manager's team dashboard with anonymized aggregates.

    Returns:
    - Team overview (anonymized metrics)
    - Risk distribution
    - Recent events (anonymized)
    - Consent summary
    """
    # Get team members
    team_members = get_team_members(db, current_user.user_hash)

    if not team_members:
        return {
            "team": {
                "manager_hash": current_user.user_hash,
                "member_count": 0,
                "message": "No team members assigned to you",
            },
            "metrics": None,
            "risk_distribution": {},
            "consent_summary": {},
        }

    # Get risk scores for all team members
    team_hashes = [m.user_hash for m in team_members]
    risk_scores = db.query(RiskScore).filter(RiskScore.user_hash.in_(team_hashes)).all()

    # Build anonymized member list
    anonymized_members = []
    hash_to_pseudonym = {}

    for idx, member in enumerate(team_members):
        pseudonym = anonymize_user_hash(member.user_hash, idx)
        hash_to_pseudonym[member.user_hash] = pseudonym

        # Get member's risk score
        risk = next((r for r in risk_scores if r.user_hash == member.user_hash), None)

        # Determine if manager can see real identity
        can_identify = member.consent_share_with_manager

        anonymized_members.append(
            {
                "pseudonym": pseudonym,
                "is_identified": can_identify,
                "real_hash": member.user_hash if can_identify else None,
                "risk_level": risk.risk_level if risk else "CALIBRATING",
                "has_consent": member.consent_share_with_manager,
            }
        )

    # Calculate team metrics
    risk_levels = [m["risk_level"] for m in anonymized_members]
    risk_distribution = {
        "LOW": risk_levels.count("LOW"),
        "ELEVATED": risk_levels.count("ELEVATED"),
        "CRITICAL": risk_levels.count("CRITICAL"),
        "CALIBRATING": risk_levels.count("CALIBRATING"),
    }

    # Consent summary
    consent_count = sum(1 for m in team_members if m.consent_share_with_manager)

    # Get recent team events (last 7 days)
    week_ago = datetime.utcnow() - timedelta(days=7)
    recent_events = (
        db.query(Event)
        .filter(Event.user_hash.in_(team_hashes), Event.timestamp >= week_ago)
        .order_by(Event.timestamp.desc())
        .limit(50)
        .all()
    )

    # Anonymize events
    anonymized_events = []
    for event in recent_events:
        pseudonym = hash_to_pseudonym.get(event.user_hash, "Unknown")
        anonymized_events.append(
            {
                "pseudonym": pseudonym,
                "event_type": event.event_type,
                "timestamp": event.timestamp.isoformat(),
                "metadata": event.metadata,
            }
        )

    return {
        "team": {
            "manager_hash": current_user.user_hash,
            "member_count": len(team_members),
            "members": anonymized_members,
        },
        "metrics": {
            "total_members": len(team_members),
            "at_risk_count": risk_distribution["ELEVATED"]
            + risk_distribution["CRITICAL"],
            "critical_count": risk_distribution["CRITICAL"],
            "consent_rate": f"{consent_count}/{len(team_members)}",
        },
        "risk_distribution": risk_distribution,
        "consent_summary": {
            "total": len(team_members),
            "consented": consent_count,
            "not_consented": len(team_members) - consent_count,
            "percentage": round((consent_count / len(team_members)) * 100, 1)
            if team_members
            else 0,
        },
        "recent_events": anonymized_events,
    }


@router.get("/team/member/{user_hash}", response_model=dict)
def get_team_member_details(
    user_hash: str,
    current_user: UserIdentity = Depends(require_role("manager", "admin")),
    db: Session = Depends(get_db),
):
    """
    Get detailed view of a specific team member.

    Access Rules:
    1. Must be the employee's direct manager
    2. Employee must have consented, OR
    3. Employee must be at CRITICAL risk for 36+ hours (emergency)

    Returns detailed metrics if access granted, anonymized summary if not.
    """
    # Initialize permission service
    perm_service = PermissionService(db)

    # Check if manager can view this employee
    can_view, reason = perm_service.can_manager_view_employee(current_user, user_hash)

    # Get employee data
    employee = db.query(UserIdentity).filter_by(user_hash=user_hash).first()

    if not employee:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found"
        )

    # Verify this is manager's direct report
    if employee.manager_hash != current_user.user_hash:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not your direct report"
        )

    # Log the access attempt
    perm_service.log_data_access(
        accessor_hash=current_user.user_hash,
        target_hash=user_hash,
        action="view_team_member",
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

    return {
        "access": "granted",
        "reason": reason,
        "employee": {
            "user_hash": user_hash,
            "is_identified": True,
            "consent": employee.consent_share_with_manager,
            "monitoring_paused": employee.monitoring_paused_until is not None,
        },
        "risk": {
            "current_level": risk_score.risk_level if risk_score else "CALIBRATING",
            "velocity": risk_score.velocity if risk_score else None,
            "confidence": risk_score.confidence if risk_score else 0,
            "thwarted_belongingness": risk_score.thwarted_belongingness
            if risk_score
            else None,
            "updated_at": risk_score.updated_at.isoformat() if risk_score else None,
        },
        "history": [
            {
                "timestamp": h.timestamp.isoformat(),
                "risk_level": h.risk_level,
                "velocity": h.velocity,
            }
            for h in history
        ],
        "recent_events": [
            {
                "timestamp": e.timestamp.isoformat(),
                "event_type": e.event_type,
                "metadata": e.metadata,
            }
            for e in events
        ],
    }


@router.get("/team/analytics", response_model=dict)
def get_team_analytics(
    days: int = 30,
    current_user: UserIdentity = Depends(require_role("manager", "admin")),
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
    team_members = get_team_members(db, current_user.user_hash)

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


@router.get("/team/network", response_model=dict)
def get_team_network(
    current_user: UserIdentity = Depends(require_role("manager", "admin")),
    db: Session = Depends(get_db),
):
    """
    Get team communication network (anonymized).

    Returns network graph showing collaboration patterns
    without identifying individuals (unless consented).
    """
    # Get team members
    team_members = get_team_members(db, current_user.user_hash)

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
                "risk_level": risk.risk_level if risk else "CALIBRATING",
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


@router.post("/team/send-nudge/{user_hash}")
def send_wellness_nudge(
    user_hash: str,
    message: Optional[str] = None,
    current_user: UserIdentity = Depends(require_role("manager", "admin")),
    db: Session = Depends(get_db),
):
    """
    Send a wellness nudge/encouragement to a team member.

    Note: This should be used sparingly and thoughtfully.
    Consider whether a direct conversation might be more appropriate.
    """
    # Verify employee is on manager's team
    employee = db.query(UserIdentity).filter_by(user_hash=user_hash).first()

    if not employee or employee.manager_hash != current_user.user_hash:
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
            "manager_hash": current_user.user_hash,
            "message_preview": message[:50] if message else None,
            "timestamp": datetime.utcnow().isoformat(),
        },
    )
    db.add(audit_log)
    db.commit()

    # In real implementation, this would send via Slack/email
    # For now, we just log it

    return {
        "message": "Nudge recorded (sending not implemented in demo)",
        "recipient": user_hash,
        "sender": current_user.user_hash,
        "logged": True,
    }
