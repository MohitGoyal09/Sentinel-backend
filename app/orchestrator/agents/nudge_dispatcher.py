# Nudge Dispatcher Agent
# Wrapper for nudge dispatching service

from typing import Dict, Any, List, Optional
import structlog

from app.orchestrator.base import BaseAgent
from app.services.nudge_dispatcher import NudgeDispatcher
from app.services.safety_valve import SafetyValve
from app.core.database import SessionLocal

log = structlog.get_logger(__name__)


class NudgeDispatcherAgent(BaseAgent):
    """
    Nudge Dispatcher Agent for intervention delivery.
    
    Capabilities:
    - Risk-based nudge generation
    - Multi-channel delivery (Slack, Email, etc.)
    - Intervention tracking
    - Nudge effectiveness monitoring
    """
    
    agent_id = "nudge_dispatcher"
    name = "Nudge Dispatcher Agent"
    agent_type = "nudge_dispatcher"
    
    def get_capabilities(self) -> List[str]:
        return [
            "nudge_generation",
            "multi_channel_delivery",
            "intervention_tracking",
            "effectiveness_monitoring",
            "personalized_recommendations"
        ]
    
    async def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute nudge dispatch for a user.
        
        Args:
            payload:
                - user_hash: User identifier (required)
                - channel: Delivery channel (optional, defaults to "auto")
                - nudge_type: Type of nudge (optional, auto-detected from risk)
                - priority: Nudge priority (optional, defaults to "normal")
                - skip_generation: Skip nudge generation, use provided message (optional)
                - message: Pre-defined message (optional)
        
        Returns:
            Nudge dispatch results
        """
        self.validate_payload(payload, ["user_hash"])
        
        user_hash = payload["user_hash"]
        channel = payload.get("channel", "auto")
        nudge_type = payload.get("nudge_type")
        priority = payload.get("priority", "normal")
        skip_generation = payload.get("skip_generation", False)
        message = payload.get("message")
        
        log.info(
            "nudge_dispatcher.execute",
            user_hash=user_hash[:8],
            channel=channel,
            priority=priority
        )
        
        with SessionLocal() as db:
            dispatcher = NudgeDispatcher(db)
            safety_engine = SafetyValve(db)
            
            # Get risk data if not using pre-defined message
            if skip_generation and message is None:
                raise ValueError("Either skip_generation=false or provide a message")
            
            if not skip_generation:
                # Analyze risk to determine nudge type
                risk_data = safety_engine.analyze(user_hash)
                
                # Auto-detect nudge type from risk level
                if nudge_type is None:
                    nudge_type = self._detect_nudge_type(risk_data)
                
                # Generate personalized nudge
                nudge = dispatcher.generate_nudge(
                    user_hash=user_hash,
                    nudge_type=nudge_type,
                    risk_data=risk_data
                )
                
                message = nudge.get("message", "")
                channels = nudge.get("channels", ["slack"])
            else:
                channels = [channel] if channel != "auto" else ["slack"]
            
            # Dispatch nudge
            result = dispatcher.dispatch(
                user_hash=user_hash,
                message=message,
                channels=channels,
                priority=priority
            )
            
            # Track delivery
            result["user_hash"] = user_hash
            result["nudge_type"] = nudge_type
            result["dispatched_at"] = self._timestamp()
            
            # Calculate confidence based on risk data quality
            if not skip_generation:
                risk_data = safety_engine.analyze(user_hash)
                confidence = self._calculate_confidence(risk_data)
            else:
                confidence = 0.8
            
            result["confidence"] = confidence
            
            log.info(
                "nudge_dispatcher.complete",
                user_hash=user_hash[:8],
                nudge_type=nudge_type,
                channels=channels,
                success=result.get("success", False),
                confidence=confidence
            )
            
            return result
    
    async def dry_run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Preview nudge without sending.
        
        Args:
            payload:
                - user_hash: User identifier (required)
                - nudge_type: Type of nudge (optional)
        
        Returns:
            Preview of nudge that would be sent
        """
        self.validate_payload(payload, ["user_hash"])
        
        user_hash = payload["user_hash"]
        nudge_type = payload.get("nudge_type")
        
        with SessionLocal() as db:
            dispatcher = NudgeDispatcher(db)
            safety_engine = SafetyValve(db)
            
            # Get risk data
            risk_data = safety_engine.analyze(user_hash)
            
            # Auto-detect nudge type if not provided
            if nudge_type is None:
                nudge_type = self._detect_nudge_type(risk_data)
            
            # Generate preview
            nudge = dispatcher.generate_nudge(
                user_hash=user_hash,
                nudge_type=nudge_type,
                risk_data=risk_data
            )
            
            return {
                "user_hash": user_hash,
                "nudge_type": nudge_type,
                "preview": nudge,
                "risk_data": risk_data,
                "dry_run": True
            }
    
    def _detect_nudge_type(self, risk_data: Dict) -> str:
        """
        Detect appropriate nudge type from risk data.
        
        Returns:
            Nudge type string
        """
        risk_level = risk_data.get("risk_level", "ELEVATED")
        
        if risk_level == "CRITICAL":
            return "immediate_intervention"
        elif risk_level == "ELEVATED":
            return "supportive_checkin"
        else:
            return "wellness_prompt"
    
    def _calculate_confidence(self, risk_data: Dict) -> float:
        """
        Calculate confidence based on risk data quality.
        
        Returns:
            Confidence score between 0 and 1
        """
        confidence = 0.7  # Base confidence
        
        # Risk level determination confidence
        risk_level = risk_data.get("risk_level")
        if risk_level:
            confidence += 0.1
        
        # Velocity metric
        if risk_data.get("velocity") is not None:
            confidence += 0.1
        
        # Belongingness score
        if risk_data.get("belongingness_score") is not None:
            confidence += 0.1
        
        return min(0.99, round(confidence, 2))
    
    def _timestamp(self) -> str:
        """Get current ISO timestamp."""
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()
