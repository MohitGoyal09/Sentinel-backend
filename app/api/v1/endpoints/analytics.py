from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api.deps.auth import get_current_user_identity, require_role
from app.core.database import get_db
from app.models.analytics import RiskHistory
from app.models.tenant import TenantMember
from typing import List, Dict, Any

router = APIRouter()


@router.get("/team-energy-heatmap")
def get_team_energy_heatmap(
    days: int = 30,
    db: Session = Depends(get_db),
    user=Depends(require_role("manager", "admin"))
):
    """
    Returns daily aggregated risk scores for heatmap visualization.
    Each day shows: avg risk score, count by level, dominant risk level.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    # Scope to current user's tenant
    tenant_hashes = [
        tm.user_hash
        for tm in db.query(TenantMember.user_hash)
        .filter_by(tenant_id=user.tenant_id)
        .all()
    ]

    # Get risk history entries within timeframe, scoped to tenant
    history = db.query(RiskHistory).filter(
        RiskHistory.timestamp >= cutoff,
        RiskHistory.user_hash.in_(tenant_hashes),
    ).all()

    # Group by date
    daily_data: Dict[str, Dict[str, Any]] = {}
    for entry in history:
        day = entry.timestamp.date().isoformat()
        if day not in daily_data:
            daily_data[day] = {
                'date': day,
                'low': 0,
                'elevated': 0,
                'critical': 0,
                'calibrating': 0,
                'total_count': 0,
                'avg_velocity': 0,
                'velocities': []
            }

        # Normalize risk level to lowercase for comparison
        risk_level = (entry.risk_level or 'calibrating').lower()

        # Map risk levels to standardized keys
        if risk_level in ['low', 'healthy']:
            daily_data[day]['low'] += 1
        elif risk_level in ['elevated', 'medium']:
            daily_data[day]['elevated'] += 1
        elif risk_level in ['critical', 'high']:
            daily_data[day]['critical'] += 1
        else:
            daily_data[day]['calibrating'] += 1

        daily_data[day]['total_count'] += 1
        daily_data[day]['velocities'].append(entry.velocity or 0)

    # Calculate aggregates and dominant risk
    result: List[Dict[str, Any]] = []
    for day, data in daily_data.items():
        avg_vel = sum(data['velocities']) / len(data['velocities']) if data['velocities'] else 0

        # Determine dominant risk level
        if data['critical'] > 0:
            dominant = 'CRITICAL'
        elif data['elevated'] > data['low']:
            dominant = 'ELEVATED'
        else:
            dominant = 'LOW'

        result.append({
            'date': day,
            'risk_level': dominant,
            'avg_velocity': round(avg_vel, 2),
            'breakdown': {
                'low': data['low'],
                'elevated': data['elevated'],
                'critical': data['critical']
            },
            'total_members': data['total_count']
        })

    return {
        'days': sorted(result, key=lambda x: x['date']),
        'date_range': {
            'start': cutoff.date().isoformat(),
            'end': datetime.utcnow().date().isoformat()
        }
    }
