import numpy as np
from scipy import stats
from datetime import datetime, timedelta
from typing import Dict, List
from sqlalchemy.orm import Session
from app.models.analytics import Event, RiskScore

from app.services.context import ContextEnricher
from app.services.nudge_dispatcher import NudgeDispatcher
from app.services.websocket_manager import manager

class SafetyValve:
    """Burnout detection via Sentiment Velocity"""
    
    def __init__(self, db: Session):
        self.db = db
        self.min_days = 7
        self.critical_threshold = 2.5
        self.elevated_threshold = 1.5
        self.context = ContextEnricher(db)

    def analyze(self, user_hash: str) -> Dict:
        events = self._get_events(user_hash, days=21)
        
        if len(events) < 14:
            return {
                "engine": "Safety Valve",
                "status": "CALIBRATING", 
                "risk_level": "INSUFFICIENT_DATA",
                "days_collected": len(events),
                "velocity": 0.0,
                "confidence": 0.0,
                "belongingness_score": 0.0,
                "circadian_entropy": 0.0,
                "indicators": {
                    "chaotic_hours": False,
                    "social_withdrawal": False,
                    "sustained_intensity": False,
                    "has_explained_context": False
                }
            }
        
        # NEW: Filter out explained late nights before calculating velocity
        # Get user email for context context 
        user_email = self._get_user_email(user_hash)
        
        # Mark explained events
        events = self.context.mark_events_explained(events, user_email)
        
        # Only count unexplained events for velocity calculation
        unexplained_events = [e for e in events if not (e.metadata_ or {}).get("explained", False)]
        
        # Calculate metrics on FILTERED events (explained removed)
        daily_hours = self._extract_daily_hours(unexplained_events)
        entropy = self._calculate_entropy(daily_hours)
        
        # Velocity only on unexplained late nights
        velocity, r_squared = self._calculate_velocity(unexplained_events)
        
        # Belongingness on ALL events
        belongingness = self._calculate_belongingness(user_hash, events)
        
        # Risk Decision
        explained_count = len(events) - len(unexplained_events)
        
        if velocity > self.critical_threshold and belongingness < 0.3:
            risk = "CRITICAL"
        elif velocity > self.elevated_threshold or belongingness < 0.4:
            risk = "ELEVATED"
        else:
            risk = "LOW"
        
        self._store_result(user_hash, velocity, risk, r_squared, belongingness)
        
        return {
            "engine": "Safety Valve",
            "risk_level": risk,
            "velocity": round(float(velocity), 2),
            "confidence": round(float(r_squared), 2),
            "belongingness_score": round(float(belongingness), 2),
            "circadian_entropy": round(float(entropy), 2),
            "explained_events_filtered": explained_count,
            "unexplained_events_count": len(unexplained_events),
            "indicators": {
                "chaotic_hours": entropy > 1.5,
                "social_withdrawal": belongingness < 0.4,
                "sustained_intensity": velocity > 2.0,
                "has_explained_context": explained_count > 0
            }
        }

    async def analyze_and_notify(self, user_hash: str) -> Dict:
        """Analyze and trigger real-time updates"""
        result = self.analyze(user_hash)
        
        # If elevated or critical, dispatch nudge via Slack
        if result["risk_level"] in ["ELEVATED", "CRITICAL"]:
            dispatcher = NudgeDispatcher(self.db)
            await dispatcher.dispatch(user_hash, result)
        
        # Broadcast update to connected clients (Frontend Dashboard)
        await manager.broadcast_risk_update(user_hash, result)
        
        return result
    def _get_user_email(self, user_hash: str) -> str:
        """Lookup email from Vault B for context API calls"""
        from app.models.identity import UserIdentity
        # Direct query to Vault B (Identity schema)
        user = self.db.query(UserIdentity).filter_by(user_hash=user_hash).first()
        if user:
            from app.core.security import privacy
            return privacy.decrypt(user.email_encrypted)
        return "unknown@company.com"  # Fallback
    
    def _generate_llm_insight(self, velocity, risk, belongingness):
        """Optional: Use LLM to explain the risk state"""
        from app.services.llm import llm_service
        prompt = f"""
        Analyze an employee's burnout risk state:
        - Sentiment Velocity: {velocity} (High is bad)
        - Risk Level: {risk}
        - Belongingness: {belongingness} (Low is isolated)
        
        Provide a 1-sentence managerial insight.
        """
        return llm_service.generate_insight(prompt)
    
    def _calculate_velocity(self, events: List[Event]) -> tuple:
        """FIXED: Proper date sorting and regression"""
        daily_scores = {}
        for e in events:
            day = e.timestamp.date()
            score = 1.0
            if e.metadata_ and isinstance(e.metadata_, dict):
                if e.metadata_.get("after_hours"):
                    score += 2.0
                if e.metadata_.get("context_switches", 0) > 5:
                    score += 0.5
            daily_scores[day] = daily_scores.get(day, 0) + score
        
        if len(daily_scores) < 2:
            return 0.0, 0.0
        
        # Sort by date to ensure chronological regression
        sorted_dates = sorted(daily_scores.keys())
        y = np.array([daily_scores[d] for d in sorted_dates])
        x = np.arange(len(y))
        
        slope, _, r_value, _, _ = stats.linregress(x, y)
        return float(slope), float(r_value ** 2)
    
    def _calculate_entropy(self, hours: List[int]) -> float:
        """FIXED: Handle empty arrays and log stability"""
        if not hours:
            return 0.0
        _, counts = np.unique(hours, return_counts=True)
        probs = counts / len(hours)
        # Add epsilon to avoid log(0)
        return float(-np.sum(probs * np.log2(probs + 1e-9)))
    
    def _calculate_belongingness(self, user_hash: str, events: List[Event]) -> float:
        """Measure social connection"""
        interactions = [e for e in events if e.event_type in ["slack_message", "pr_comment"]]
        if not interactions:
            return 0.5
        
        # Response rate to others
        replies = sum(1 for e in interactions if e.metadata_ and isinstance(e.metadata_, dict) and e.metadata_.get("is_reply", False))
        mentions_others = sum(1 for e in interactions if e.metadata_ and isinstance(e.metadata_, dict) and e.metadata_.get("mentions_others", False))
        
        return (replies + mentions_others) / (2 * len(interactions)) if interactions else 0.5
    
    def _extract_daily_hours(self, events: List[Event]) -> List[int]:
        return [e.timestamp.hour for e in events]
    
    def _get_events(self, user_hash: str, days: int):
        cutoff = datetime.utcnow() - timedelta(days=days)
        return self.db.query(Event).filter(
            Event.user_hash == user_hash,
            Event.timestamp >= cutoff
        ).order_by(Event.timestamp.asc()).all()
    
    def _store_result(self, user_hash, velocity, risk, confidence, belongingness):
        score = self.db.query(RiskScore).filter_by(user_hash=user_hash).first()
        if not score:
            score = RiskScore(user_hash=user_hash)
        
        score.velocity = velocity
        score.risk_level = risk
        score.confidence = confidence
        score.thwarted_belongingness = belongingness
        score.updated_at = datetime.utcnow()
        self.db.add(score)
        self.db.commit()

        from app.models.analytics import RiskHistory
        history = RiskHistory(
            user_hash=user_hash,
            risk_level=risk,
            velocity=velocity,
            confidence=confidence,
            belongingness_score=belongingness,
            timestamp=datetime.utcnow()
        )
        self.db.add(history)
        self.db.commit()
