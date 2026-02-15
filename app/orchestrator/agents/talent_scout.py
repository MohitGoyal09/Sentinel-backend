# Talent Scout Agent
# Wrapper for Talent Scout engine service

from typing import Dict, Any, List
import structlog

from app.orchestrator.base import BaseAgent
from app.services.talent_scout import TalentScout
from app.core.database import SessionLocal

log = structlog.get_logger(__name__)


class TalentScoutAgent(BaseAgent):
    """
    Talent Scout Agent for network centrality analysis.
    
    Capabilities:
    - Betweenness centrality calculation
    - Eigenvector centrality measurement
    - Unblocking count tracking
    - Hidden gem identification
    """
    
    agent_id = "talent_scout"
    name = "Talent Scout Agent"
    agent_type = "talent_scout"
    
    def get_capabilities(self) -> List[str]:
        return [
            "network_analysis",
            "betweenness_centrality",
            "eigenvector_centrality",
            "unblocking_count",
            "hidden_gem_identification",
            "network_metrics"
        ]
    
    async def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute talent scout analysis for a user or team.
        
        Args:
            payload:
                - user_hash: User identifier (optional for team analysis)
                - team_hashes: List of team member hashes (optional)
                - analysis_type: "individual", "team", or "global" (optional)
                - network_depth: Depth of network analysis (optional)
        
        Returns:
            Talent scout analysis results
        """
        with SessionLocal() as db:
            engine = TalentScout(db)
            
            # Determine analysis type
            has_user_hash = "user_hash" in payload and payload["user_hash"]
            has_team_hashes = "team_hashes" in payload and payload["team_hashes"]
            
            if has_user_hash:
                analysis_type = payload.get("analysis_type", "individual")
                user_hash = payload["user_hash"]
                
                log.info(
                    "talent_scout.execute",
                    user_hash=user_hash[:8],
                    analysis_type=analysis_type
                )
                
                result = engine.analyze(user_hash)
                
            elif has_team_hashes:
                analysis_type = "team"
                team_hashes = payload["team_hashes"]
                network_depth = payload.get("network_depth", 2)
                
                log.info(
                    "talent_scout.execute",
                    team_size=len(team_hashes),
                    analysis_type=analysis_type,
                    network_depth=network_depth
                )
                
                result = engine.analyze_network()
                
            else:
                # Global analysis
                analysis_type = "global"
                
                log.info(
                    "talent_scout.execute",
                    analysis_type=analysis_type
                )
                
                result = engine.analyze_network()
            
            # Add analysis metadata
            result["analysis_type"] = analysis_type
            
            # Calculate confidence
            confidence = self._calculate_confidence(db)
            result["confidence"] = confidence
            
            log.info(
                "talent_scout.complete",
                analysis_type=analysis_type,
                hidden_gems_found=len(result.get("top_performers", [])),
                confidence=confidence
            )
            
            return result
    
    def _calculate_confidence(self, db) -> float:
        """
        Calculate confidence score based on network data completeness.
        
        Returns:
            Confidence score between 0 and 1
        """
        from app.models.analytics import GraphEdge
        from app.models.identity import UserIdentity
        
        # Check edge count
        edge_count = db.query(GraphEdge).count()
        user_count = db.query(UserIdentity).count()
        
        if edge_count == 0:
            # No network data
            return 0.2
        
        # Calculate average connections per user
        avg_connections = edge_count / user_count if user_count > 0 else 0
        
        # Adjust confidence based on network completeness
        if avg_connections >= 5:
            confidence = 0.95
        elif avg_connections >= 3:
            confidence = 0.85
        elif avg_connections >= 1:
            confidence = 0.7
        else:
            confidence = 0.5
        
        # Adjust for user count
        if user_count < 5:
            confidence *= 0.8
        elif user_count < 10:
            confidence *= 0.9
        
        return min(0.99, round(confidence, 2))
