"""
Demo Data Endpoint for Onboarding Wizard

Provides one-click sample data loading for hackathon judges.
Creates 4 realistic employee personas with 30-day behavioral history.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, timedelta
from typing import List, Dict, Any
from app.core.database import get_db
from app.core.security import privacy
from app.api.deps.auth import get_current_user_identity, require_role
from app.models.identity import UserIdentity
from app.models.analytics import Event, RiskScore, RiskHistory, GraphEdge, CentralityScore, SkillProfile
from app.services.simulation import RealTimeSimulator
from app.services.safety_valve import SafetyValve
from app.services.talent_scout import TalentScout
from app.config import get_settings

router = APIRouter()


def require_simulation_mode():
    """Dependency that blocks demo endpoints when SIMULATION_MODE is disabled."""
    settings = get_settings()
    if not settings.simulation_mode:
        raise HTTPException(
            status_code=403,
            detail="Demo endpoints are disabled when SIMULATION_MODE=false",
        )


# Sample persona configurations
DEMO_PERSONAS = [
    {
        "email": "sarah.chen@demo.algoquest.ai",
        "name": "Sarah Chen",
        "persona_type": "sarah_gem",
        "description": "High performer with low risk - steady contributor",
        "role": "employee",
        "team": "frontend",
    },
    {
        "email": "michael.rodriguez@demo.algoquest.ai",
        "name": "Michael Rodriguez",
        "persona_type": "alex_burnout",
        "description": "Burnout risk - high meeting load and declining engagement",
        "role": "employee",
        "team": "backend",
    },
    {
        "email": "emily.zhang@demo.algoquest.ai",
        "name": "Emily Zhang",
        "persona_type": "maria_contagion",
        "description": "Flight risk - declining engagement and negative sentiment",
        "role": "employee",
        "team": "backend",
    },
    {
        "email": "david.kim@demo.algoquest.ai",
        "name": "David Kim",
        "persona_type": "jordan_steady",
        "description": "Hidden gem - high potential with healthy work patterns",
        "role": "employee",
        "team": "frontend",
    },
]


@router.post("/load-sample-data", dependencies=[Depends(require_simulation_mode)])
async def load_sample_data(db: Session = Depends(get_db), user=Depends(require_role("admin"))) -> Dict[str, Any]:
    """
    Load sample demo data for onboarding wizard.
    Creates 4 employee personas with 30 days of behavioral history.

    Returns:
        Dict containing created user hashes and summary statistics
    """
    try:
        # Get tenant_id from the authenticated admin user
        from app.models.tenant import TenantMember
        demo_tenant_id = None
        admin_hash = getattr(user, 'user_hash', None)
        if admin_hash:
            tm = db.query(TenantMember).filter_by(user_hash=admin_hash).first()
            demo_tenant_id = tm.tenant_id if tm else None

        simulator = RealTimeSimulator(db)
        safety_valve = SafetyValve(db, tenant_id=demo_tenant_id)
        talent_scout = TalentScout(db, tenant_id=demo_tenant_id)

        created_users = []
        total_events = 0

        for persona_config in DEMO_PERSONAS:
            email = persona_config["email"]
            user_hash = privacy.hash_identity(email)
            persona_type = persona_config["persona_type"]

            # Check if user already exists
            existing_user = db.query(UserIdentity).filter_by(user_hash=user_hash).first()
            if existing_user:
                # Skip if demo data already loaded
                continue

            # Create user identity
            user = UserIdentity(
                user_hash=user_hash,
                email_encrypted=privacy.encrypt(email),
                role=persona_config["role"],
                consent_share_with_manager=True,
                consent_share_anonymized=True,
            )
            db.add(user)
            db.flush()

            # Generate 30 days of behavioral events using simulation engine
            events = simulator.create_persona(
                persona_type=persona_type,
                user_hash=user_hash,
                team_hash=persona_config["team"]
            )

            # Bulk insert events
            db.bulk_save_objects(events)
            total_events += len(events)

            # Calculate initial risk scores using Safety Valve
            risk_data = safety_valve.analyze(user_hash)

            # Create risk score entry
            risk_score = RiskScore(
                user_hash=user_hash,
                velocity=risk_data.get("velocity", 0.0),
                risk_level=risk_data.get("risk_level", "LOW"),
                confidence=risk_data.get("confidence", 0.5),
                thwarted_belongingness=risk_data.get("belongingness_score", 0.0),
                updated_at=datetime.utcnow(),
            )
            db.add(risk_score)

            # Generate risk history (30 daily snapshots)
            base_date = datetime.utcnow() - timedelta(days=30)
            for day in range(30):
                snapshot_date = base_date + timedelta(days=day)

                # Calculate progressive risk based on persona type
                if persona_type == "alex_burnout":
                    # Escalating burnout pattern
                    progress = day / 30.0
                    velocity = 30.0 + (progress * 50.0)  # 30 -> 80
                    risk_level = "LOW" if progress < 0.3 else ("ELEVATED" if progress < 0.7 else "CRITICAL")
                    confidence = 0.6 + (progress * 0.3)
                    belongingness = 0.3 + (progress * 0.5)
                elif persona_type == "maria_contagion":
                    # Declining engagement pattern
                    progress = day / 30.0
                    velocity = 35.0 + (progress * 40.0)
                    risk_level = "LOW" if progress < 0.4 else "ELEVATED"
                    confidence = 0.5 + (progress * 0.3)
                    belongingness = 0.4 + (progress * 0.4)
                elif persona_type == "sarah_gem":
                    # Steady healthy pattern
                    velocity = 25.0 + (day % 5) * 2.0  # Slight variation
                    risk_level = "LOW"
                    confidence = 0.8
                    belongingness = 0.15
                else:  # jordan_steady
                    # Consistent healthy pattern
                    velocity = 28.0 + (day % 7) * 1.5
                    risk_level = "LOW"
                    confidence = 0.75
                    belongingness = 0.2

                history_entry = RiskHistory(
                    user_hash=user_hash,
                    risk_level=risk_level,
                    velocity=velocity,
                    confidence=confidence,
                    belongingness_score=belongingness,
                    timestamp=snapshot_date,
                )
                db.add(history_entry)

            # Generate network graph edges (collaboration patterns)
            # Create connections between demo users
            for other_persona in DEMO_PERSONAS:
                if other_persona["email"] != email:
                    other_hash = privacy.hash_identity(other_persona["email"])

                    # Higher connectivity for "sarah_gem" (network hub)
                    if persona_type == "sarah_gem":
                        weight = 15.0 + (hash(f"{user_hash}{other_hash}") % 20)
                    else:
                        weight = 5.0 + (hash(f"{user_hash}{other_hash}") % 10)

                    edge = GraphEdge(
                        source_hash=user_hash,
                        target_hash=other_hash,
                        weight=float(weight),
                        last_interaction=datetime.utcnow() - timedelta(days=hash(f"{user_hash}{other_hash}") % 7),
                        edge_type="collaboration",
                    )
                    db.add(edge)

            # Calculate network centrality scores
            # Sarah gets high betweenness (connector), others moderate
            if persona_type == "sarah_gem":
                betweenness = 0.85
                eigenvector = 0.72
                unblocking_count = 18
                knowledge_transfer = 0.88
            elif persona_type == "jordan_steady":
                betweenness = 0.45
                eigenvector = 0.52
                unblocking_count = 8
                knowledge_transfer = 0.65
            else:
                betweenness = 0.25
                eigenvector = 0.35
                unblocking_count = 3
                knowledge_transfer = 0.42

            centrality = CentralityScore(
                user_hash=user_hash,
                betweenness=betweenness,
                eigenvector=eigenvector,
                unblocking_count=unblocking_count,
                knowledge_transfer_score=knowledge_transfer,
                calculated_at=datetime.utcnow(),
            )
            db.add(centrality)

            # Create skill profiles (for radar chart visualization)
            if persona_type == "sarah_gem":
                skills = SkillProfile(
                    user_hash=user_hash,
                    technical=82.0,
                    communication=95.0,
                    leadership=68.0,
                    collaboration=92.0,
                    adaptability=85.0,
                    creativity=78.0,
                )
            elif persona_type == "alex_burnout":
                skills = SkillProfile(
                    user_hash=user_hash,
                    technical=88.0,
                    communication=62.0,
                    leadership=55.0,
                    collaboration=58.0,
                    adaptability=48.0,
                    creativity=65.0,
                )
            elif persona_type == "maria_contagion":
                skills = SkillProfile(
                    user_hash=user_hash,
                    technical=75.0,
                    communication=72.0,
                    leadership=62.0,
                    collaboration=68.0,
                    adaptability=58.0,
                    creativity=70.0,
                )
            else:  # jordan_steady
                skills = SkillProfile(
                    user_hash=user_hash,
                    technical=78.0,
                    communication=80.0,
                    leadership=72.0,
                    collaboration=85.0,
                    adaptability=75.0,
                    creativity=73.0,
                )
            db.add(skills)

            created_users.append({
                "user_hash": user_hash,
                "email": email,
                "name": persona_config["name"],
                "persona_type": persona_type,
                "description": persona_config["description"],
                "risk_level": risk_score.risk_level,
                "events_count": len(events),
            })

        # Commit all changes
        db.commit()

        return {
            "success": True,
            "message": "Sample data loaded successfully",
            "users_created": len(created_users),
            "total_events": total_events,
            "users": created_users,
            "demo_mode": True,
        }

    except Exception as e:
        db.rollback()
        import logging
        logging.error(f"Failed to load sample data: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load sample data: {str(e)}"
        )


@router.delete("/clear-sample-data", dependencies=[Depends(require_simulation_mode)])
async def clear_sample_data(db: Session = Depends(get_db), user=Depends(require_role("admin"))) -> Dict[str, Any]:
    """
    Clear all demo sample data.
    Removes demo users and all associated events, risk scores, etc.
    """
    try:
        deleted_count = 0

        for persona_config in DEMO_PERSONAS:
            email = persona_config["email"]
            user_hash = privacy.hash_identity(email)

            # Find and delete user (cascade will handle related records)
            user = db.query(UserIdentity).filter_by(user_hash=user_hash).first()
            if user:
                # Delete related records explicitly (if cascade isn't configured)
                db.query(Event).filter_by(user_hash=user_hash).delete()
                db.query(RiskScore).filter_by(user_hash=user_hash).delete()
                db.query(RiskHistory).filter_by(user_hash=user_hash).delete()
                db.query(GraphEdge).filter_by(source_hash=user_hash).delete()
                db.query(GraphEdge).filter_by(target_hash=user_hash).delete()
                db.query(CentralityScore).filter_by(user_hash=user_hash).delete()
                db.query(SkillProfile).filter_by(user_hash=user_hash).delete()

                db.delete(user)
                deleted_count += 1

        db.commit()

        return {
            "success": True,
            "message": f"Deleted {deleted_count} demo users and associated data",
            "users_deleted": deleted_count,
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to clear sample data: {str(e)}"
        )


@router.get("/sample-data-status", dependencies=[Depends(require_simulation_mode)])
async def get_sample_data_status(db: Session = Depends(get_db), user=Depends(require_role("admin"))) -> Dict[str, Any]:
    """
    Check if sample data is currently loaded.
    """
    loaded_users = []

    for persona_config in DEMO_PERSONAS:
        email = persona_config["email"]
        user_hash = privacy.hash_identity(email)

        user = db.query(UserIdentity).filter_by(user_hash=user_hash).first()
        if user:
            risk_score = db.query(RiskScore).filter_by(user_hash=user_hash).first()
            event_count = db.query(Event).filter_by(user_hash=user_hash).count()

            loaded_users.append({
                "user_hash": user_hash,
                "email": email,
                "name": persona_config["name"],
                "risk_level": risk_score.risk_level if risk_score else "UNKNOWN",
                "events_count": event_count,
            })

    return {
        "demo_loaded": len(loaded_users) > 0,
        "users_count": len(loaded_users),
        "users": loaded_users,
    }
