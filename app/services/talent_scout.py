import logging
import networkx as nx
from typing import Dict, Optional
from uuid import UUID
from sqlalchemy.orm import Session
from app.models.analytics import Event, GraphEdge, CentralityScore
from app.models.tenant import TenantMember

logger = logging.getLogger("sentinel.talent_scout")


class TalentScout:
    """Identify hidden gems via network analysis"""

    def __init__(self, db: Session, tenant_id: Optional[UUID] = None):
        self.db = db
        self.tenant_id = tenant_id

    def analyze_network(self, team_hash: str = None, tenant_id: UUID = None) -> Dict:
        """Calculate centrality metrics for all users.

        Args:
            team_hash: Optional team filter (unused, kept for API compat)
            tenant_id: Required tenant scope. If not provided, returns NO_DATA
                       to prevent cross-tenant data leakage.
        """
        G = nx.DiGraph()

        if tenant_id is None:
            logger.warning("analyze_network called without tenant_id — refusing to load all edges")
            return {"status": "NO_DATA"}

        # Build graph from interactions — always scoped to tenant
        tenant_hashes = {
            tm.user_hash
            for tm in self.db.query(TenantMember.user_hash)
            .filter_by(tenant_id=tenant_id)
            .all()
        }
        edges = (
            self.db.query(GraphEdge)
            .filter(
                GraphEdge.source_hash.in_(tenant_hashes),
                GraphEdge.target_hash.in_(tenant_hashes),
            )
            .all()
        )
        for edge in edges:
            G.add_edge(edge.source_hash, edge.target_hash, weight=edge.weight)

        if not G.nodes():
            return {"status": "NO_DATA"}

        # Check for existing CentralityScore records (from seed or prior computation).
        # If they exist, use them directly instead of recalculating — this preserves
        # carefully curated seed data for demos while still supporting live recalculation
        # when no scores exist.
        existing_scores = {
            cs.user_hash: cs
            for cs in self.db.query(CentralityScore)
            .filter(CentralityScore.user_hash.in_(tenant_hashes))
            .all()
        }

        if existing_scores:
            # Use existing scores — don't overwrite seed data
            betweenness = {h: cs.betweenness for h, cs in existing_scores.items()}
            eigenvector = {h: cs.eigenvector for h, cs in existing_scores.items()}
            unblocking = {h: cs.unblocking_count for h, cs in existing_scores.items()}
            # Fill in any graph nodes that don't have scores yet
            for node in G.nodes():
                if node not in betweenness:
                    betweenness[node] = 0.0
                    eigenvector[node] = 0.0
                    unblocking[node] = 0
        else:
            # No existing scores — calculate from graph topology
            k = 100 if len(G) > 100 else None
            betweenness = nx.betweenness_centrality(G, weight="weight", k=k)
            eigenvector = self._calculate_eigenvector_centrality(G)
            unblocking = self._calculate_unblocking_metrics(G)

            # Persist calculated scores
            for user_hash in G.nodes():
                score_obj = CentralityScore(
                    user_hash=user_hash,
                    betweenness=betweenness.get(user_hash, 0),
                    eigenvector=eigenvector.get(user_hash, 0),
                    unblocking_count=unblocking.get(user_hash, 0),
                    knowledge_transfer_score=self._knowledge_transfer_score(user_hash),
                )
                self.db.merge(score_obj)

        # Calculate layout positions
        try:
            pos = nx.spring_layout(G, center=(300, 210), scale=180, seed=42)
        except Exception:
            pos = {n: (300, 210) for n in G.nodes()}

        results = []
        for user_hash in G.nodes():
            bw = betweenness.get(user_hash, 0)
            ev = eigenvector.get(user_hash, 0)
            unb = unblocking.get(user_hash, 0)
            results.append(
                {
                    "user_hash": user_hash,
                    "betweenness": round(bw, 3),
                    "eigenvector": round(ev, 3),
                    "unblocking": unb,
                    "is_hidden_gem": bw > 0.3 and unb > 5 and ev < 0.2,
                }
            )

        # Build response nodes/edges
        # Resolve real names from TenantMember
        name_lookup = {}
        members: list = []
        try:
            members = self.db.query(TenantMember).filter(
                TenantMember.user_hash.in_(list(G.nodes()))
            ).all()
            name_lookup = {
                m.user_hash: m.display_name
                for m in members
                if m.display_name
            }
        except Exception:
            pass

        # Fetch actual risk levels from RiskScore table
        from app.models.analytics import RiskScore
        risk_lookup: dict[str, str] = {}
        try:
            risk_rows = self.db.query(RiskScore.user_hash, RiskScore.risk_level).filter(
                RiskScore.user_hash.in_(list(G.nodes()))
            ).all()
            risk_lookup = {r.user_hash: r.risk_level for r in risk_rows}
        except Exception:
            pass

        graph_nodes = []

        for node in G.nodes():
            bw = betweenness.get(node, 0)
            ev = eigenvector.get(node, 0)
            unb = unblocking.get(node, 0)
            is_gem = bw > 0.3 and unb > 5 and ev < 0.2

            x_pos, y_pos = pos.get(node, (300, 210))

            graph_nodes.append(
                {
                    "id": node,
                    "label": name_lookup.get(node, f"User_{node[:4]}"),
                    "role": next((m.role for m in members if m.user_hash == node), "employee"),
                    "risk_level": risk_lookup.get(node, "LOW"),
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
            logger.debug("Eigenvector with weights failed: %s, trying without weights", e)

        # Strategy 2: Try without weights (more stable)
        try:
            return nx.eigenvector_centrality(G, max_iter=10000, weight=None)
        except (nx.PowerIterationFailedConvergence, Exception) as e:
            logger.debug("Eigenvector without weights failed: %s, using degree centrality", e)

        # Strategy 3: Use degree centrality as approximation
        try:
            degree_cent = nx.degree_centrality(G)
            # Normalize to similar range as eigenvector
            max_val = max(degree_cent.values()) if degree_cent else 1
            if max_val > 0:
                return {k: v / max_val for k, v in degree_cent.items()}
        except Exception as e:
            logger.warning("Degree centrality failed: %s", e)

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
        query = self.db.query(Event).filter(
            Event.user_hash == user_hash, Event.event_type == "pr_review"
        )
        if self.tenant_id is not None:
            query = query.filter(Event.tenant_id == self.tenant_id)
        reviews = query.all()

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
