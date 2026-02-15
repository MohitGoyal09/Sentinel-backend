# LLM Context Agent
# Wrapper for LLM context enrichment service

from typing import Dict, Any, List
import structlog

from app.orchestrator.base import BaseAgent
from app.services.context import ContextEnricher
from app.services.llm import LLMService
from app.core.database import SessionLocal

log = structlog.get_logger(__name__)


class LLMContextAgent(BaseAgent):
    """
    LLM Context Agent for context enrichment and explanation generation.
    
    Capabilities:
    - Context explanation generation
    - Risk pattern interpretation
    - Intervention recommendations
    - Natural language summaries
    """
    
    agent_id = "llm_context"
    name = "LLM Context Agent"
    agent_type = "llm_context"
    
    def get_capabilities(self) -> List[str]:
        return [
            "context_explanation",
            "pattern_interpretation",
            "intervention_recommendation",
            "natural_language_summary",
            "sentiment_analysis"
        ]
    
    async def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute context enrichment for a user's risk analysis.
        
        Args:
            payload:
                - user_hash: User identifier (required)
                - risk_data: Risk analysis data (optional, fetches if not provided)
                - include_recommendations: Include intervention recommendations (optional)
                - language: Response language (optional, defaults to "en")
        
        Returns:
            Context enrichment results with explanations
        """
        self.validate_payload(payload, ["user_hash"])
        
        user_hash = payload["user_hash"]
        include_recommendations = payload.get("include_recommendations", True)
        language = payload.get("language", "en")
        
        log.info(
            "llm_context.execute",
            user_hash=user_hash[:8],
            include_recommendations=include_recommendations
        )
        
        # Get risk data if not provided
        risk_data = payload.get("risk_data")
        if risk_data is None:
            from app.services.safety_valve import SafetyValve
            
            with SessionLocal() as db:
                safety_engine = SafetyValve(db)
                risk_data = safety_engine.analyze(user_hash)
        
        # Enrich context
        with SessionLocal() as db:
            enricher = ContextEnricher(db)
            llm = LLMService()
            
            # Get context explanation
            context = enricher.get_context(user_hash)
            
            # Generate explanation using LLM
            explanation = llm.generate_explanation(
                user_hash=user_hash,
                risk_data=risk_data,
                context=context,
                language=language
            )
            
            result = {
                "user_hash": user_hash,
                "explanation": explanation,
                "context": context,
                "language": language,
                "generated_at": self._timestamp()
            }
            
            # Add recommendations if requested
            if include_recommendations:
                recommendations = llm.generate_recommendations(
                    user_hash=user_hash,
                    risk_data=risk_data,
                    context=context,
                    language=language
                )
                result["recommendations"] = recommendations
            
            # Calculate confidence based on data quality
            confidence = self._calculate_confidence(risk_data, context)
            result["confidence"] = confidence
            
            log.info(
                "llm_context.complete",
                user_hash=user_hash[:8],
                has_context=context.get("is_explained", False),
                has_recommendations=include_recommendations,
                confidence=confidence
            )
            
            return result
    
    def _calculate_confidence(self, risk_data: Dict, context: Dict) -> float:
        """
        Calculate confidence based on data quality.
        
        Returns:
            Confidence score between 0 and 1
        """
        confidence = 0.5  # Base confidence
        
        # Risk data completeness
        required_fields = ["risk_level", "velocity", "belongingness_score", "circadian_entropy"]
        present_fields = sum(1 for f in required_fields if f in risk_data)
        data_completeness = present_fields / len(required_fields)
        confidence += data_completeness * 0.3
        
        # Context availability
        if context.get("is_explained"):
            confidence += 0.15
        if context.get("source"):
            confidence += 0.05
        
        return min(0.99, round(confidence, 2))
    
    def _timestamp(self) -> str:
        """Get current ISO timestamp."""
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()
