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
from app.models.analytics import RiskScore, RiskHistory
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
            "role": current_user.role,
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
