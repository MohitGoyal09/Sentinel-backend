import networkx as nx
from datetime import datetime
from typing import Dict
from sqlalchemy.orm import Session
from app.models.analytics import Event, GraphEdge, CentralityScore

class TalentScout:
    """Identify hidden gems via network analysis"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def analyze_network(self, team_hash: str = None) -> Dict:
        """Calculate centrality metrics for all users"""
        G = nx.DiGraph()
        
        # Build graph from interactions
        edges = self.db.query(GraphEdge).all()
        for edge in edges:
            G.add_edge(edge.source_hash, edge.target_hash, weight=edge.weight)
        
        if not G.nodes():
            return {"status": "NO_DATA"}
        
        # Calculate metrics
        betweenness = nx.betweenness_centrality(G, weight='weight')
        eigenvector = nx.eigenvector_centrality(G, max_iter=1000, weight='weight')
        unblocking = self._calculate_unblocking_metrics(G)
        
        results = []
        for user_hash in G.nodes():
            score_obj = CentralityScore(
                user_hash=user_hash,
                betweenness=betweenness.get(user_hash, 0),
                eigenvector=eigenvector.get(user_hash, 0),
                unblocking_count=unblocking.get(user_hash, 0),
                knowledge_transfer_score=self._knowledge_transfer_score(user_hash)
            )
            self.db.merge(score_obj)
            results.append({
                "user_hash": user_hash,
                "betweenness": round(betweenness.get(user_hash, 0), 3),
                "eigenvector": round(eigenvector.get(user_hash, 0), 3),
                "unblocking": unblocking.get(user_hash, 0),
                "is_hidden_gem": self._is_hidden_gem(score_obj)
            })
        
        self.db.commit()
        return {"engine": "Talent Scout", "top_performers": results[:5]}
    
    def _calculate_unblocking_metrics(self, G: nx.DiGraph) -> Dict:
        """Count how often someone's work enables others"""
        unblocking = {}
        for node in G.nodes():
            # Out-degree = helping others
            unblocking[node] = G.out_degree(node, weight='weight')
        return unblocking
    
    def _knowledge_transfer_score(self, user_hash: str) -> float:
        """Analyze code review comments for insight quality"""
        reviews = self.db.query(Event).filter(
            Event.user_hash == user_hash,
            Event.event_type == "pr_review"
        ).all()
        
        if not reviews:
            return 0.0
        
        # Metric: Length of review comments (proxy for thoroughness)
        total_length = sum(r.metadata_.get("comment_length", 0) for r in reviews if r.metadata_ and isinstance(r.metadata_, dict))
        return min(total_length / 1000, 10.0)  # Cap at 10
    
    def _is_hidden_gem(self, score: CentralityScore) -> bool:
        """Low activity, high impact"""
        return (score.betweenness > 0.3 and 
                score.unblocking_count > 5 and 
                score.eigenvector < 0.2)  # Not the obvious "popular" person
