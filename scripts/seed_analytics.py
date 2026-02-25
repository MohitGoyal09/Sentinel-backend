
import sys
import os
from datetime import datetime, timedelta

# Ensure backend root is in path
sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "backend"))

from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models.identity import UserIdentity
from app.models.analytics import RiskScore, RiskHistory
from app.services.safety_valve import SafetyValve
from app.core.security import privacy

# Define our RBAC users and their desired personas
USERS_TO_SEED = [
    {
        "email": "manager1@sentinel.local",
        "persona": "jordan_steady",  # Steady manager
        "role": "manager"
    },
    {
        "email": "employee1@sentinel.local",
        "persona": "alex_burnout",   # Critical risk (consented)
        "role": "employee"
    },
    {
        "email": "employee2@sentinel.local",
        "persona": "sarah_gem",      # Healthy (not consented)
        "role": "employee"
    },
    {
        "email": "employee3@sentinel.local",
        "persona": "maria_contagion", # Elevated/Critical (different team)
        "role": "employee"
    }
]

def seed_analytics():
    print("=" * 60)
    print("Seeding Analytics Data for RBAC Users")
    print("=" * 60)
    
    db = SessionLocal()
    engine = SafetyValve(db)
    
    try:
        updated_count = 0
        
        for u in USERS_TO_SEED:
            email = u["email"]
            persona = u["persona"]
            user_hash = privacy.hash_identity(email)
            
            print(f"\nProcessing {email} ({persona})...")
            
            # 1. Clear existing history to avoid duplicates if re-running
            deleted = db.query(RiskHistory).filter_by(user_hash=user_hash).delete()
            if deleted:
                print(f"   - Cleared {deleted} old history records")
                
            # 2. Seed 30 days of history
            print("   - Generating risk history trajectory...")
            # We use the internal method directly since we are a script
            engine.seed_risk_history(user_hash, persona)
            
            # 3. Get the latest history point to populate RiskScore (Current Status)
            latest = (
                db.query(RiskHistory)
                .filter_by(user_hash=user_hash)
                .order_by(RiskHistory.timestamp.desc())
                .first()
            )
            
            if latest:
                # Upsert RiskScore
                score = db.query(RiskScore).filter_by(user_hash=user_hash).first()
                if not score:
                    score = RiskScore(user_hash=user_hash)
                    db.add(score)
                
                score.risk_level = latest.risk_level
                score.velocity = latest.velocity
                score.confidence = latest.confidence
                score.thwarted_belongingness = latest.belongingness_score
                score.updated_at = datetime.utcnow()
                
                print(f"   - Updated current RiskScore: {latest.risk_level} (v={latest.velocity})")
                updated_count += 1
            
        db.commit()
        print("\n" + "=" * 60)
        print(f"Success! Updated analytics for {updated_count} users.")
        print("Please refresh the dashboard.")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n[ERROR] Failed to seed analytics: {e}")
        db.rollback()
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    seed_analytics()
