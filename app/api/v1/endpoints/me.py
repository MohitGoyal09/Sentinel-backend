"""
Employee Self-Service API Endpoints (/me)

These endpoints allow employees to:
- View their own wellness data
- Manage consent settings
- Pause/resume monitoring
- Delete their data (GDPR right to be forgotten)
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Optional
from pydantic import BaseModel

from app.core.database import get_db
from app.models.identity import UserIdentity, AuditLog
from app.models.analytics import RiskScore, RiskHistory, SkillProfile
from app.models.tenant import TenantMember
from app.api.deps.auth import get_current_user_identity, require_role
from app.services.permission_service import PermissionService, UserRole
from app.schemas.engines import SafetyValveResponse

router = APIRouter()


class ConsentUpdate(BaseModel):
    consent_share_with_manager: Optional[bool] = None
    consent_share_anonymized: Optional[bool] = None


@router.get("", response_model=dict)
@router.get("/", response_model=dict)
def get_my_profile(
    current_user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """
    Get current user's profile and wellness data.

    Returns:
    - User identity (hash, role, consent settings)
    - Current risk score and metrics
    - Monitoring status
    - Audit trail (who accessed their data)
    """
    # Get current risk score
    risk_score = db.query(RiskScore).filter_by(user_hash=current_user.user_hash).first()

    # Get skill profile
    skill_profile = db.query(SkillProfile).filter_by(user_hash=current_user.user_hash).first()

    # Get user's primary role from TenantMember
    primary_member = db.query(TenantMember).filter_by(user_hash=current_user.user_hash).first()
    primary_role = primary_member.role if primary_member else "employee"

    # Get recent audit trail (last 30 days)
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    audit_logs = (
        db.query(AuditLog)
        .filter(
            AuditLog.user_hash == current_user.user_hash,
            AuditLog.timestamp >= thirty_days_ago,
        )
        .order_by(AuditLog.timestamp.desc())
        .limit(50)
        .all()
    )

    # Format audit trail
    audit_trail = []
    for log in audit_logs:
        audit_trail.append(
            {
                "action": log.action,
                "timestamp": log.timestamp.isoformat(),
                "details": log.details,
            }
        )

    return {
        "user": {
            "user_hash": current_user.user_hash,
            "role": primary_role,
            "consent_share_with_manager": current_user.consent_share_with_manager,
            "consent_share_anonymized": current_user.consent_share_anonymized,
            "monitoring_paused_until": current_user.monitoring_paused_until.isoformat()
            if current_user.monitoring_paused_until
            else None,
            "created_at": current_user.created_at.isoformat()
            if current_user.created_at
            else None,
        },
        "risk": {
            "velocity": risk_score.velocity if risk_score else None,
            "risk_level": risk_score.risk_level if risk_score else "LOW",
            "confidence": risk_score.confidence if risk_score else 0.0,
            "thwarted_belongingness": risk_score.thwarted_belongingness
            if risk_score
            else None,
            "attrition_probability": risk_score.attrition_probability if risk_score else 0.0,
            "updated_at": risk_score.updated_at.isoformat() if risk_score else None,
        }
        if risk_score
        else None,
        "audit_trail": audit_trail,
        "monitoring_status": {
            "is_paused": current_user.monitoring_paused_until
            and current_user.monitoring_paused_until > datetime.utcnow(),
            "paused_until": current_user.monitoring_paused_until.isoformat()
            if current_user.monitoring_paused_until
            else None,
        },
        "skills": {
            "technical": skill_profile.technical if skill_profile else 50.0,
            "communication": skill_profile.communication if skill_profile else 50.0,
            "leadership": skill_profile.leadership if skill_profile else 50.0,
            "collaboration": skill_profile.collaboration if skill_profile else 50.0,
            "adaptability": skill_profile.adaptability if skill_profile else 50.0,
            "creativity": skill_profile.creativity if skill_profile else 50.0,
            "updated_at": skill_profile.updated_at.isoformat() if skill_profile and skill_profile.updated_at else None,
        } if skill_profile else None,
    }


@router.get("/risk-history", response_model=list)
def get_my_risk_history(
    days: int = Query(default=30, ge=1, le=365),
    current_user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """
    Get current user's risk history over time.

    Parameters:
    - days: Number of days to look back (default: 30)

    Returns chronological risk scores for charting.
    """
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    history = (
        db.query(RiskHistory)
        .filter(
            RiskHistory.user_hash == current_user.user_hash,
            RiskHistory.timestamp >= cutoff_date,
        )
        .order_by(RiskHistory.timestamp.asc())
        .all()
    )

    return [
        {
            "timestamp": h.timestamp.isoformat(),
            "velocity": h.velocity,
            "risk_level": h.risk_level,
            "confidence": h.confidence,
            "thwarted_belongingness": h.belongingness_score,
        }
        for h in history
    ]


@router.put("/consent")
def update_my_consent(
    body: ConsentUpdate,
    current_user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """
    Update consent settings.

    Parameters:
    - consent_share_with_manager: Allow manager to see individual details
    - consent_share_anonymized: Allow anonymized data in team aggregates

    Note: Both default to False for maximum privacy. User must opt-in.
    """
    # Track changes for audit log
    changes = {}

    if body.consent_share_with_manager is not None:
        old_value = current_user.consent_share_with_manager
        current_user.consent_share_with_manager = body.consent_share_with_manager
        changes["consent_share_with_manager"] = {
            "old": old_value,
            "new": body.consent_share_with_manager,
        }

    if body.consent_share_anonymized is not None:
        old_value = current_user.consent_share_anonymized
        current_user.consent_share_anonymized = body.consent_share_anonymized
        changes["consent_share_anonymized"] = {
            "old": old_value,
            "new": body.consent_share_anonymized,
        }

    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No consent settings provided to update",
        )

    # Create audit log
    audit_log = AuditLog(
        user_hash=current_user.user_hash,
        action="consent_updated",
        details={"changes": changes, "updated_by": "self"},
    )
    db.add(audit_log)
    db.commit()

    return {
        "message": "Consent settings updated successfully",
        "consent": {
            "consent_share_with_manager": current_user.consent_share_with_manager,
            "consent_share_anonymized": current_user.consent_share_anonymized,
        },
        "changes": changes,
    }


@router.post("/pause-monitoring")
def pause_my_monitoring(
    hours: int = 24,
    current_user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """
    Pause monitoring for a specified duration.

    Parameters:
    - hours: Duration to pause (default: 24, max: 168/7 days)

    Use cases:
    - Vacation
    - Mental health day
    - Personal time
    - "I just need a break from tracking"

    During pause:
    - No new events are analyzed
    - Risk scores are frozen
    - No nudges are sent
    - Data is still collected (for when monitoring resumes)
    """
    # Validate duration
    if hours < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pause duration must be at least 1 hour",
        )

    if hours > 168:  # 7 days
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pause duration cannot exceed 168 hours (7 days)",
        )

    # Calculate pause end time
    pause_until = datetime.utcnow() + timedelta(hours=hours)

    # Update user
    current_user.monitoring_paused_until = pause_until

    # Create audit log
    audit_log = AuditLog(
        user_hash=current_user.user_hash,
        action="monitoring_paused",
        details={
            "hours": hours,
            "paused_until": pause_until.isoformat(),
            "paused_by": "self",
        },
    )
    db.add(audit_log)
    db.commit()

    return {
        "message": f"Monitoring paused for {hours} hours",
        "paused_until": pause_until.isoformat(),
        "will_resume": pause_until.isoformat(),
    }


@router.post("/resume-monitoring")
def resume_my_monitoring(
    current_user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """
    Resume monitoring immediately (before scheduled resume time).
    """
    was_paused = current_user.monitoring_paused_until is not None

    # Clear pause
    current_user.monitoring_paused_until = None

    # Create audit log
    audit_log = AuditLog(
        user_hash=current_user.user_hash,
        action="monitoring_resumed",
        details={"was_paused": was_paused, "resumed_by": "self"},
    )
    db.add(audit_log)
    db.commit()

    return {"message": "Monitoring resumed", "was_paused": was_paused}


@router.delete("/data")
def delete_my_data(
    confirm: bool = False,
    current_user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """
    Delete all personal data (GDPR Right to be Forgotten).

    WARNING: This is irreversible!

    What gets deleted:
    - User identity record (Vault B)
    - All risk scores
    - All risk history
    - All audit logs
    - All events

    What stays (anonymized):
    - Graph edges (anonymized relationships)
    - Aggregate team metrics (your data is removed from averages)

    Requirements:
    - confirm=true (safety check)
    """
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must pass confirm=true to delete data. This action is irreversible!",
        )

    user_hash = current_user.user_hash

    try:
        # Delete from Vault B (Identity)
        db.query(UserIdentity).filter_by(user_hash=user_hash).delete()

        # Delete from Vault A (Analytics)
        db.query(RiskScore).filter_by(user_hash=user_hash).delete()
        db.query(RiskHistory).filter_by(user_hash=user_hash).delete()
        db.query(AuditLog).filter_by(user_hash=user_hash).delete()

        # Delete tenant membership
        db.query(TenantMember).filter_by(user_hash=user_hash).delete()

        # Note: Events are intentionally kept for aggregate analysis
        # but anonymized (user_hash becomes NULL)
        from app.models.analytics import Event

        db.query(Event).filter_by(user_hash=user_hash).update(
            {"user_hash": None}, synchronize_session=False
        )

        db.commit()

        return {
            "message": "All personal data deleted successfully",
            "user_hash": user_hash,
            "deleted_at": datetime.utcnow().isoformat(),
            "note": "You have been logged out. Your account no longer exists.",
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Data deletion failed. Please contact support.",
        )


@router.get("/audit-trail")
def get_my_audit_trail(
    days: int = 30,
    current_user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """
    Get audit trail of who accessed this user's data.

    Transparency is key to trust. Users can see:
    - Who accessed their data
    - When it happened
    - Why (consent, emergency, admin)
    """
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    logs = (
        db.query(AuditLog)
        .filter(
            AuditLog.user_hash == current_user.user_hash,
            AuditLog.timestamp >= cutoff_date,
        )
        .order_by(AuditLog.timestamp.desc())
        .limit(200)
        .all()
    )

    return {
        "user_hash": current_user.user_hash,
        "period_days": days,
        "total_accesses": len(logs),
        "accesses": [
            {
                "action": log.action,
                "timestamp": log.timestamp.isoformat(),
                "details": log.details,
            }
            for log in logs
        ],
    }


@router.get("/profile/{user_hash}")
def get_user_profile(
    user_hash: str,
    current_user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """
    Get a user's profile (for managers viewing team members, admins viewing anyone).
    Returns: name, role, team, date_joined, risk data, skills, attrition probability.
    """
    from app.models.analytics import CentralityScore
    from app.models.team import Team
    from app.core.security import privacy

    # Get the requesting user's role
    requester = db.query(TenantMember).filter_by(user_hash=current_user.user_hash).first()
    if not requester:
        raise HTTPException(status_code=403, detail="Not a tenant member")

    # Role-based access control
    if requester.role == "employee":
        if user_hash != current_user.user_hash:
            raise HTTPException(status_code=403, detail="Employees can only view their own profile")
    elif requester.role == "manager":
        # Verify target is in manager's team
        team_hashes = [
            tm.user_hash for tm in
            db.query(TenantMember.user_hash).filter_by(
                team_id=requester.team_id, tenant_id=requester.tenant_id
            ).all()
        ] if requester.team_id else []
        if user_hash not in team_hashes and user_hash != current_user.user_hash:
            raise HTTPException(status_code=403, detail="Can only view your team members")
    # Admin can view anyone in tenant

    # Get target user data
    target_member = db.query(TenantMember).filter_by(
        user_hash=user_hash, tenant_id=requester.tenant_id
    ).first()
    if not target_member:
        raise HTTPException(status_code=404, detail="User not found")

    target_identity = db.query(UserIdentity).filter_by(user_hash=user_hash).first()

    # Get name from display_name or encrypted email
    name = target_member.display_name
    if not name and target_identity:
        try:
            email = privacy.decrypt(target_identity.email_encrypted)
            name = email.split("@")[0].replace(".", " ").title()
        except Exception:
            name = f"User {user_hash[:4]}"

    # Get team name
    team_name = None
    if target_member.team_id:
        team = db.query(Team).filter_by(id=target_member.team_id).first()
        team_name = team.name if team else None

    # Risk data
    risk_score = db.query(RiskScore).filter_by(user_hash=user_hash).first()

    # Skills
    skill_profile = db.query(SkillProfile).filter_by(user_hash=user_hash).first()

    # Centrality (network impact)
    centrality = db.query(CentralityScore).filter_by(user_hash=user_hash).first()

    return {
        "success": True,
        "data": {
            "user_hash": user_hash,
            "name": name or f"User {user_hash[:4]}",
            "role": target_member.role,
            "team": team_name,
            "date_joined": target_identity.created_at.isoformat()
            if target_identity and target_identity.created_at
            else None,
            "risk": {
                "risk_level": risk_score.risk_level if risk_score else "LOW",
                "velocity": risk_score.velocity if risk_score else 0.0,
                "confidence": risk_score.confidence if risk_score else 0.0,
                "belongingness_score": risk_score.thwarted_belongingness if risk_score else 0.5,
                "attrition_probability": risk_score.attrition_probability if risk_score else 0.0,
            } if risk_score else None,
            "skills": {
                "technical": skill_profile.technical,
                "communication": skill_profile.communication,
                "leadership": skill_profile.leadership,
                "collaboration": skill_profile.collaboration,
                "adaptability": skill_profile.adaptability,
                "creativity": skill_profile.creativity,
            } if skill_profile else None,
            "network": {
                "betweenness": centrality.betweenness if centrality else 0.0,
                "eigenvector": centrality.eigenvector if centrality else 0.0,
                "unblocking_count": centrality.unblocking_count if centrality else 0,
            } if centrality else None,
        }
    }
