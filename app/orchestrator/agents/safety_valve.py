# Safety Valve Agent
# Wrapper for Safety Valve engine service

from typing import Dict, Any, List
import structlog

from app.orchestrator.base import BaseAgent
from app.services.safety_valve import SafetyValve
from app.core.database import SessionLocal

log = structlog.get_logger(__name__)


class SafetyValveAgent(BaseAgent):
    """
    Safety Valve Agent for burnout risk analysis.
    
    Capabilities:
    - Burnout risk analysis
    - Velocity calculation
    - Belongingness scoring
    - Circadian entropy measurement
    """
    
    agent_id = "safety_valve"
    name = "Safety Valve Agent"
    agent_type = "safety_valve"
    
    def get_capabilities(self) -> List[str]:
        return [
            "burnout_risk_analysis",
            "velocity_calculation",
            "belongingness_scoring",
            "circadian_entropy",
            "indicator_analysis",
            "nudge_recommendation"
        ]
    
    async def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute safety valve analysis for a user.
        
        Args:
            payload:
                - user_hash: User identifier (required)
                - analysis_type: "full", "quick", or "indicators" (optional)
                - include_nudge: Include nudge recommendation (optional)
        
        Returns:
            Safety valve analysis results
        """
        # Validate required fields
        self.validate_payload(payload, ["user_hash"])
        
        user_hash = payload["user_hash"]
        analysis_type = payload.get("analysis_type", "full")
        include_nudge = payload.get("include_nudge", True)
        
        log.info(
            "safety_valve.execute",
            user_hash=user_hash[:8],
            analysis_type=analysis_type
        )
        
        with SessionLocal() as db:
            engine = SafetyValve(db)
            
            # Run the analysis
            result = engine.analyze(user_hash)
            
            # Add analysis metadata
            result["analysis_type"] = analysis_type
            result["include_nudge"] = include_nudge
            
            # Calculate confidence score based on data completeness
            confidence = self._calculate_confidence(db, user_hash)
            result["confidence"] = confidence
            
            log.info(
                "safety_valve.complete",
                user_hash=user_hash[:8],
                risk_level=result.get("risk_level"),
                confidence=confidence
            )
            
            return result
    
    def _calculate_confidence(self, db, user_hash: str) -> float:
        """
        Calculate confidence score based on available data.
        
        Returns:
            Confidence score between 0 and 1
        """
        from app.models.analytics import Event, RiskScore
        
        # Count recent events (last 30 days)
        from datetime import datetime, timedelta
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        
        event_count = db.query(Event).filter(
            Event.user_hash == user_hash,
            Event.timestamp >= thirty_days_ago
        ).count()
        
        # Check for recent risk score
        risk_score = db.query(RiskScore).filter_by(user_hash=user_hash).first()
        
        # Calculate confidence
        confidence = 0.0
        
        if event_count >= 30:
            confidence = 0.95
        elif event_count >= 15:
            confidence = 0.85
        elif event_count >= 5:
            confidence = 0.7
        elif event_count >= 1:
            confidence = 0.5
        else:
            confidence = 0.3
        
        # Adjust for risk score freshness
        if risk_score:
            days_old = (datetime.utcnow() - risk_score.updated_at).days
            if days_old > 7:
                confidence *= 0.8
            elif days_old > 3:
                confidence *= 0.9
        
        return min(0.99, round(confidence, 2))
