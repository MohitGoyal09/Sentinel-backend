"""
ROI Calculator API Endpoints (/roi)

Provides financial calculations for burnout prevention ROI.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel

from app.core.database import get_db
from app.models.analytics import RiskScore
from app.models.identity import UserIdentity
from app.api.deps.auth import get_current_user_identity

router = APIRouter()


class ROICalculation(BaseModel):
    """ROI calculation response model"""
    high_risk_count: int
    elevated_risk_count: int
    avg_salary: float
    industry_multiplier: float
    potential_cost: float
    intervention_cost: float
    potential_savings: float
    roi_percentage: float
    breakdown: dict


class IndustryConfig(BaseModel):
    """Industry-specific cost multipliers"""
    name: str
    turnover_multiplier: float
    productivity_multiplier: float


# Industry-specific cost multipliers based on research
INDUSTRY_CONFIGS = {
    "tech": IndustryConfig(
        name="Technology",
        turnover_multiplier=1.5,  # 150% of salary
        productivity_multiplier=0.3  # 30% productivity loss
    ),
    "finance": IndustryConfig(
        name="Financial Services",
        turnover_multiplier=2.0,  # 200% of salary (high replacement cost)
        productivity_multiplier=0.4  # 40% productivity loss
    ),
    "healthcare": IndustryConfig(
        name="Healthcare",
        turnover_multiplier=1.8,
        productivity_multiplier=0.35
    ),
    "retail": IndustryConfig(
        name="Retail",
        turnover_multiplier=1.2,
        productivity_multiplier=0.25
    ),
    "manufacturing": IndustryConfig(
        name="Manufacturing",
        turnover_multiplier=1.3,
        productivity_multiplier=0.28
    ),
    "education": IndustryConfig(
        name="Education",
        turnover_multiplier=1.4,
        productivity_multiplier=0.3
    ),
    "default": IndustryConfig(
        name="General",
        turnover_multiplier=1.5,
        productivity_multiplier=0.3
    )
}


@router.get("/calculate", response_model=ROICalculation)
async def calculate_roi(
    high_risk_count: Optional[int] = Query(None, description="Number of high-risk employees"),
    elevated_risk_count: Optional[int] = Query(None, description="Number of elevated-risk employees"),
    avg_salary: float = Query(80000, description="Average employee salary"),
    industry: str = Query("default", description="Industry type for cost multipliers"),
    current_user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db)
):
    """
    Calculate financial ROI of burnout prevention.

    Formula:
    - Replacement cost = turnover_multiplier × annual salary
    - Productivity loss = productivity_multiplier × salary (during burnout period)
    - Total potential cost = (replacement + productivity) × employee count
    - Intervention success rate = 70% (industry research baseline)
    - Potential savings = total_cost × success_rate - intervention_cost
    - ROI = (savings / intervention_cost) × 100

    If high_risk_count or elevated_risk_count not provided, fetch from current team data.
    """

    # Auto-fetch risk counts from team data if not provided
    if high_risk_count is None or elevated_risk_count is None:
        # Query team members
        if current_user.role == "admin":
            # Admin sees all users
            team_members = db.query(UserIdentity).all()
        else:
            # Manager sees their direct reports
            team_members = db.query(UserIdentity).filter(
                UserIdentity.manager_hash == current_user.user_hash
            ).all()

        # Count risk levels from latest risk scores
        critical_count = 0
        elevated_count = 0

        for member in team_members:
            risk_score = db.query(RiskScore).filter(
                RiskScore.user_hash == member.user_hash
            ).order_by(RiskScore.timestamp.desc()).first()

            if risk_score:
                if risk_score.risk_level == "CRITICAL":
                    critical_count += 1
                elif risk_score.risk_level == "ELEVATED":
                    elevated_count += 1

        high_risk_count = high_risk_count or critical_count
        elevated_risk_count = elevated_risk_count or elevated_count

    # Get industry configuration
    industry_config = INDUSTRY_CONFIGS.get(industry.lower(), INDUSTRY_CONFIGS["default"])

    # Constants
    CRITICAL_TURNOVER_PROBABILITY = 0.65  # 65% of critical employees likely to leave
    ELEVATED_TURNOVER_PROBABILITY = 0.25  # 25% of elevated employees likely to leave
    PRODUCTIVITY_LOSS_MONTHS = 3  # Months of reduced productivity before turnover
    INTERVENTION_SUCCESS_RATE = 0.70  # 70% of interventions prevent turnover
    INTERVENTION_COST_PER_PERSON = 2000  # Coaching, workload adjustment, support

    # Calculate costs WITHOUT intervention

    # Critical employees
    critical_replacement_cost = (
        high_risk_count *
        avg_salary *
        industry_config.turnover_multiplier *
        CRITICAL_TURNOVER_PROBABILITY
    )
    critical_productivity_loss = (
        high_risk_count *
        (avg_salary / 12) *
        PRODUCTIVITY_LOSS_MONTHS *
        industry_config.productivity_multiplier
    )

    # Elevated employees (reduced impact)
    elevated_replacement_cost = (
        elevated_risk_count *
        avg_salary *
        industry_config.turnover_multiplier *
        ELEVATED_TURNOVER_PROBABILITY
    )
    elevated_productivity_loss = (
        elevated_risk_count *
        (avg_salary / 12) *
        (PRODUCTIVITY_LOSS_MONTHS / 2) *  # Half the duration
        industry_config.productivity_multiplier
    )

    total_replacement_cost = critical_replacement_cost + elevated_replacement_cost
    total_productivity_loss = critical_productivity_loss + elevated_productivity_loss
    potential_cost_without_intervention = total_replacement_cost + total_productivity_loss

    # Calculate costs WITH intervention
    intervention_cost = (high_risk_count + elevated_risk_count) * INTERVENTION_COST_PER_PERSON
    prevented_cost = potential_cost_without_intervention * INTERVENTION_SUCCESS_RATE
    potential_savings = prevented_cost - intervention_cost

    # Calculate ROI
    roi_percentage = (potential_savings / intervention_cost * 100) if intervention_cost > 0 else 0

    return ROICalculation(
        high_risk_count=high_risk_count,
        elevated_risk_count=elevated_risk_count,
        avg_salary=avg_salary,
        industry_multiplier=industry_config.turnover_multiplier,
        potential_cost=potential_cost_without_intervention,
        intervention_cost=intervention_cost,
        potential_savings=potential_savings,
        roi_percentage=roi_percentage,
        breakdown={
            "replacement_cost": total_replacement_cost,
            "productivity_loss": total_productivity_loss,
            "critical_impact": critical_replacement_cost + critical_productivity_loss,
            "elevated_impact": elevated_replacement_cost + elevated_productivity_loss,
            "intervention_success_rate": INTERVENTION_SUCCESS_RATE,
            "industry": industry_config.name
        }
    )


@router.get("/industries", response_model=list[dict])
async def get_industries():
    """
    Get available industry configurations.
    """
    return [
        {
            "key": key,
            "name": config.name,
            "turnover_multiplier": config.turnover_multiplier,
            "productivity_multiplier": config.productivity_multiplier
        }
        for key, config in INDUSTRY_CONFIGS.items()
    ]
