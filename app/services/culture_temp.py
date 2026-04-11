import networkx as nx
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from uuid import UUID
from sqlalchemy.orm import Session
from app.models.analytics import RiskScore, GraphEdge

class CultureThermometer:
    """Team-level contagion detection"""

    def __init__(self, db: Session, tenant_id: Optional[UUID] = None):
        self.db = db
        self.tenant_id = tenant_id
    
    def analyze_team(self, team_hashes: List[str]) -> Dict:
        """Detect resignation contagion risk"""
        if len(team_hashes) < 3:
            return {
                "engine": "Culture Thermometer",
                "team_risk": "INSUFFICIENT_DATA",
                "metrics": {
                    "avg_velocity": 0.0,
                    "critical_members": 0,
                    "graph_fragmentation": 0.0,
                    "comm_decay_rate": 0.0
                },
                "recommendation": "Add at least 3 members to analyze team culture."
            }
        
        # 1. Aggregate sentiment velocity
        risk_query = self.db.query(RiskScore).filter(
            RiskScore.user_hash.in_(team_hashes)
        )
        if self.tenant_id is not None:
            risk_query = risk_query.filter(RiskScore.tenant_id == self.tenant_id)
        risks = risk_query.all()
        
        avg_velocity = np.mean([r.velocity for r in risks]) if risks else 0
        critical_count = sum(1 for r in risks if r.risk_level == "CRITICAL")

        # Team attrition forecast from individual probabilities
        attrition_probs = [r.attrition_probability or 0.0 for r in risks]
        avg_attrition = float(np.mean(attrition_probs)) if attrition_probs else 0.0
        high_risk_30d = sum(1 for p in attrition_probs if p > 0.6)
        high_risk_60d = sum(1 for p in attrition_probs if p > 0.4)
        
        # 2. Social graph fragmentation
        fragmentation = self._calculate_fragmentation(team_hashes)
        
        # 3. Communication decay
        comm_decay = self._communication_decay(team_hashes)
        
        # Contagion risk
        if critical_count >= 2 and fragmentation > 0.5:
            risk = "HIGH_CONTAGION_RISK"
        elif avg_velocity > 1.5:
            risk = "ELEVATED"
        else:
            risk = "STABLE"
        
        return {
            "engine": "Culture Thermometer",
            "team_risk": risk,
            "metrics": {
                "avg_velocity": round(avg_velocity, 2),
                "critical_members": critical_count,
                "graph_fragmentation": round(fragmentation, 2),
                "comm_decay_rate": round(comm_decay, 2)
            },
            "attrition_forecast": {
                "avg_probability": round(avg_attrition, 2),
                "high_risk_30d": high_risk_30d,
                "high_risk_60d": high_risk_60d,
                "total_members": len(team_hashes),
            },
            "recommendation": self._recommendation(risk)
        }
    
    def _calculate_fragmentation(self, team_hashes: List[str]) -> float:
        """How disconnected is the team graph becoming?"""
        edge_query = self.db.query(GraphEdge).filter(
            GraphEdge.source_hash.in_(team_hashes),
            GraphEdge.target_hash.in_(team_hashes)
        )
        if self.tenant_id is not None:
            edge_query = edge_query.filter(GraphEdge.tenant_id == self.tenant_id)
        edges = edge_query.all()
        
        if len(edges) < 2:
            return 1.0  # Fully fragmented
        
        G = nx.Graph()
        for edge in edges:
            G.add_edge(edge.source_hash, edge.target_hash)
        
        # Clustering coefficient: Lower = more fragmented
        try:
            clustering = nx.average_clustering(G)
            return 1.0 - clustering  # Invert so higher = worse
        except Exception:
            return 1.0
    
    def _communication_decay(self, team_hashes: List[str]) -> float:
        """Are cross-team interactions decreasing?"""
        # Compare last 7 days vs previous 7 days
        recent = datetime.now(timezone.utc) - timedelta(days=7)
        previous = datetime.now(timezone.utc) - timedelta(days=14)

        recent_query = self.db.query(GraphEdge).filter(
            GraphEdge.source_hash.in_(team_hashes),
            GraphEdge.last_interaction >= recent
        )
        if self.tenant_id is not None:
            recent_query = recent_query.filter(GraphEdge.tenant_id == self.tenant_id)
        recent_count = recent_query.count()

        old_query = self.db.query(GraphEdge).filter(
            GraphEdge.source_hash.in_(team_hashes),
            GraphEdge.last_interaction < recent,
            GraphEdge.last_interaction >= previous
        )
        if self.tenant_id is not None:
            old_query = old_query.filter(GraphEdge.tenant_id == self.tenant_id)
        old_count = old_query.count()
        
        if old_count == 0:
            return 0.0
        return (old_count - recent_count) / old_count
    
    def _recommendation(self, risk: str) -> str:
        if risk == "HIGH_CONTAGION_RISK":
            return "Immediate team retrospective recommended. 2+ members showing critical burnout with social fragmentation."
        elif risk == "ELEVATED":
            return "Monitor closely. Schedule 1:1s within 48 hours."
        return "Team dynamics healthy."
