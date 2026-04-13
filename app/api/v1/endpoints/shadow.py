from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import get_db
from app.api.deps.auth import require_role
from app.models.analytics import RiskScore
from app.services.audit_service import AuditService
from app.models.tenant import TenantMember

router = APIRouter()


class DepartureReport(BaseModel):
    user_hash: str
    departure_date: str
    reason: str = "voluntary"


class ShadowStats(BaseModel):
    total_departures: int = 0
    correctly_predicted: int = 0
    false_negatives: int = 0
    accuracy: float = 0.0


@router.post("/actual-departure")
def report_departure(
    report: DepartureReport,
    db: Session = Depends(get_db),
    member: TenantMember = Depends(require_role("admin")),
):
    """Record an actual departure for shadow prediction comparison."""
    # Look up what Sentinel predicted for this person
    score = db.query(RiskScore).filter_by(user_hash=report.user_hash).first()

    predicted_risk = score.risk_level if score else "UNKNOWN"
    predicted_attrition = (
        float(score.attrition_probability)
        if score and score.attrition_probability
        else 0.0
    )
    correctly_predicted = predicted_risk in ("CRITICAL", "ELEVATED")

    # Log to audit trail
    audit = AuditService(db)
    audit.log(
        actor_hash=member.user_hash,
        actor_role=member.role,
        action="shadow_departure_logged",
        target_hash=report.user_hash,
        details={
            "departure_date": report.departure_date,
            "reason": report.reason,
            "predicted_risk": predicted_risk,
            "predicted_attrition_probability": predicted_attrition,
            "correctly_predicted": correctly_predicted,
        },
        tenant_id=member.tenant_id,
    )
    db.commit()

    return {
        "success": True,
        "comparison": {
            "predicted_risk": predicted_risk,
            "predicted_attrition": round(predicted_attrition * 100, 1),
            "correctly_predicted": correctly_predicted,
            "departure_date": report.departure_date,
            "reason": report.reason,
        },
    }


@router.get("/stats")
def get_shadow_stats(
    db: Session = Depends(get_db),
    member: TenantMember = Depends(require_role("admin")),
):
    """Get shadow deployment accuracy statistics."""
    from app.models.identity import AuditLog

    departures = (
        db.query(AuditLog)
        .filter(
            AuditLog.action == "shadow_departure_logged",
            AuditLog.tenant_id == member.tenant_id,
        )
        .all()
    )

    total = len(departures)
    correct = sum(
        1
        for d in departures
        if d.details and d.details.get("correctly_predicted")
    )

    return {
        "total_departures_logged": total,
        "correctly_predicted": correct,
        "false_negatives": total - correct,
        "accuracy": round(correct / max(total, 1) * 100, 1),
        "message": (
            f"Sentinel correctly identified {correct}/{total} departures"
            if total > 0
            else "No departures logged yet. Use POST /shadow/actual-departure to record outcomes."
        ),
    }
