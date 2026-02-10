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
        # Calculate metrics
        betweenness = nx.betweenness_centrality(G, weight="weight")
        eigenvector = self._calculate_eigenvector_centrality(G)
        unblocking = self._calculate_unblocking_metrics(G)

        # Calculate layout positions
        # Center at (300, 210) to match frontend SVG viewBox 600x420
        try:
            pos = nx.spring_layout(G, center=(300, 210), scale=180, seed=42)
        except:
            # Fallback if layout fails (e.g. empty graph or circular dependencies)
            pos = {n: (300, 210) for n in G.nodes()}

        results = []
        for user_hash in G.nodes():
            score_obj = CentralityScore(
                user_hash=user_hash,
                betweenness=betweenness.get(user_hash, 0),
                eigenvector=eigenvector.get(user_hash, 0),
                unblocking_count=unblocking.get(user_hash, 0),
                knowledge_transfer_score=self._knowledge_transfer_score(user_hash),
            )
            self.db.merge(score_obj)
            results.append(
                {
                    "user_hash": user_hash,
                    "betweenness": round(betweenness.get(user_hash, 0), 3),
                    "eigenvector": round(eigenvector.get(user_hash, 0), 3),
                    "unblocking": unblocking.get(user_hash, 0),
                    "is_hidden_gem": self._is_hidden_gem(score_obj),
                }
            )

        # Build response nodes/edges
        graph_nodes = []
        # Import random for basic visualization variation if needed, or just static
        import random

        for node in G.nodes():
            bw = betweenness.get(node, 0)
            ev = eigenvector.get(node, 0)
            unb = unblocking.get(node, 0)
            is_gem = bw > 0.3 and unb > 5 and ev < 0.2

            x_pos, y_pos = pos.get(node, (300, 210))

            graph_nodes.append(
                {
                    "id": node,
                    "label": f"User_{node[:4]}",
                    "risk_level": "LOW",
                    "betweenness": round(bw, 3),
                    "eigenvector": round(ev, 3),
                    "unblocking_count": unb,
                    "is_hidden_gem": is_gem,
                    "x": float(x_pos),
                    "y": float(y_pos),
                }
            )

        graph_edges = []
        for u, v, d in G.edges(data=True):
            graph_edges.append(
                {
                    "source": u,
                    "target": v,
                    "weight": d.get("weight", 0),
                    "edge_type": "collaboration",
                }
            )

        self.db.commit()
        return {
            "engine": "Talent Scout",
            "top_performers": results[:5],
            "nodes": graph_nodes,
            "edges": graph_edges,
        }

    def _calculate_eigenvector_centrality(self, G: nx.DiGraph) -> Dict:
        """Calculate eigenvector centrality with multiple fallback strategies"""
        if len(G.nodes()) == 0:
            return {}

        # Strategy 1: Try with weights and high iteration limit
        try:
            return nx.eigenvector_centrality(G, max_iter=5000, weight="weight")
        except (nx.PowerIterationFailedConvergence, Exception) as e:
            print(f"Eigenvector with weights failed: {e}, trying without weights...")

        # Strategy 2: Try without weights (more stable)
        try:
            return nx.eigenvector_centrality(G, max_iter=10000, weight=None)
        except (nx.PowerIterationFailedConvergence, Exception) as e:
            print(
                f"Eigenvector without weights failed: {e}, using degree centrality..."
            )

        # Strategy 3: Use degree centrality as approximation
        try:
            degree_cent = nx.degree_centrality(G)
            # Normalize to similar range as eigenvector
            max_val = max(degree_cent.values()) if degree_cent else 1
            if max_val > 0:
                return {k: v / max_val for k, v in degree_cent.items()}
        except Exception as e:
            print(f"Degree centrality failed: {e}")

        # Strategy 4: Last resort - uniform distribution
        return {node: 1.0 / len(G.nodes()) for node in G.nodes()}

    def _calculate_unblocking_metrics(self, G: nx.DiGraph) -> Dict:
        """Count how often someone's work enables others"""
        unblocking = {}
        for node in G.nodes():
            # Out-degree = helping others
            # Cast to int as it's a "count" and schema expects int
            unblocking[node] = int(round(G.out_degree(node, weight="weight")))
        return unblocking

    def _knowledge_transfer_score(self, user_hash: str) -> float:
        """Analyze code review comments for insight quality"""
        reviews = (
            self.db.query(Event)
            .filter(Event.user_hash == user_hash, Event.event_type == "pr_review")
            .all()
        )

        if not reviews:
            return 0.0

        # Metric: Length of review comments (proxy for thoroughness)
        total_length = sum(
            r.metadata_.get("comment_length", 0)
            for r in reviews
            if r.metadata_ and isinstance(r.metadata_, dict)
        )
        return min(total_length / 1000, 10.0)  # Cap at 10

    def _is_hidden_gem(self, score: CentralityScore) -> bool:
        """Low activity, high impact"""
        return (
            score.betweenness > 0.3
            and score.unblocking_count > 5
            and score.eigenvector < 0.2
        )  # Not the obvious "popular" person
