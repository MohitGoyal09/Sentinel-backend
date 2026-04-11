# Culture Thermometer Agent
# Wrapper for Culture Thermometer engine service

from typing import Dict, Any, List
import structlog

from app.orchestrator.base import BaseAgent
from app.services.culture_temp import CultureThermometer
from app.core.database import SessionLocal

log = structlog.get_logger(__name__)


class CultureThermometerAgent(BaseAgent):
    """
    Culture Thermometer Agent for team health monitoring.

    Capabilities:
    - Team contagion risk analysis
    - Graph fragmentation measurement
    - Communication decay tracking
    - Risk distribution analysis
    """

    agent_id = "culture_thermometer"
    name = "Culture Thermometer Agent"
    agent_type = "culture_thermometer"

    def get_capabilities(self) -> List[str]:
        return [
            "team_contagion_analysis",
            "graph_fragmentation",
            "communication_decay",
            "risk_distribution",
            "team_health_score",
            "intervention_recommendation",
        ]

    async def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute culture thermometer analysis for a team.

        Args:
            payload:
                - team_hashes: List of team member hashes (required)
                - include_forecast: Include risk forecast (optional)
                - forecast_days: Number of days for forecast (optional)

        Returns:
            Culture thermometer analysis results
        """
        self.validate_payload(payload, ["team_hashes"])

        team_hashes = payload["team_hashes"]
        tenant_id = payload.get("tenant_id")
        include_forecast = payload.get("include_forecast", False)
        forecast_days = payload.get("forecast_days", 30)

        log.info(
            "culture_thermometer.execute",
            team_size=len(team_hashes),
            include_forecast=include_forecast,
        )

        with SessionLocal() as db:
            engine = CultureThermometer(db, tenant_id=tenant_id)

            # Run team analysis
            result = engine.analyze_team(team_hashes)

            # Add metadata
            result["analysis_type"] = "team"
            result["team_size"] = len(team_hashes)

            # Calculate confidence
            confidence = self._calculate_confidence(db, team_hashes)
            result["confidence"] = confidence

            # Add forecast if requested
            if include_forecast:
                from app.services.sir_model import predict_contagion_risk
                from app.models.analytics import RiskScore

                # Get risk scores for team
                risks = (
                    db.query(RiskScore)
                    .filter(RiskScore.user_hash.in_(team_hashes))
                    .all()
                )

                risk_map = {"LOW": 0.2, "ELEVATED": 0.6, "CRITICAL": 0.9}
                avg_risk = (
                    sum(risk_map.get(r.risk_level, 0.2) for r in risks) / len(risks)
                    if risks
                    else 0.2
                )

                forecast = predict_contagion_risk(
                    total_members=len(team_hashes),
                    infected_count=sum(
                        1 for r in risks if r.risk_level in ["ELEVATED", "CRITICAL"]
                    ),
                    avg_connections=3.0,
                    avg_risk_score=avg_risk,
                    days=forecast_days,
                )

                result["forecast"] = forecast

            log.info(
                "culture_thermometer.complete",
                team_size=len(team_hashes),
                team_risk=result.get("team_risk"),
                contagion_risk=result.get("metrics", {}).get("contagion_risk"),
                confidence=confidence,
            )

            return result

    def _calculate_confidence(self, db, team_hashes: List[str]) -> float:
        """
        Calculate confidence based on team data completeness.

        Returns:
            Confidence score between 0 and 1
        """
        from app.models.analytics import RiskScore, GraphEdge
        from app.models.identity import UserIdentity

        # Check team member coverage
        user_count = (
            db.query(UserIdentity)
            .filter(UserIdentity.user_hash.in_(team_hashes))
            .count()
        )

        if user_count == 0:
            return 0.1

        # Check risk score coverage
        risk_count = (
            db.query(RiskScore).filter(RiskScore.user_hash.in_(team_hashes)).count()
        )

        coverage = risk_count / user_count if user_count > 0 else 0

        # Check network data
        edge_count = (
            db.query(GraphEdge).filter(GraphEdge.source_hash.in_(team_hashes)).count()
        )

        network_density = (
            edge_count / (user_count * (user_count - 1)) if user_count > 1 else 0
        )

        # Calculate confidence
        confidence = 0.0

        # Coverage contributes 50%
        confidence += coverage * 0.5

        # Network data contributes 30%
        if network_density >= 0.3:
            confidence += 0.3
        elif network_density >= 0.1:
            confidence += 0.2
        elif network_density > 0:
            confidence += 0.1

        # Team size contributes 20%
        if user_count >= 10:
            confidence += 0.2
        elif user_count >= 5:
            confidence += 0.15
        elif user_count >= 3:
            confidence += 0.1
        else:
            confidence += 0.05

        return min(0.99, round(confidence, 2))
