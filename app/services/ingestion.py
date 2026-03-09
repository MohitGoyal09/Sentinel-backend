"""
Ingestion Pipeline using Hybrid Data Sources
Supports both simulation and real integrations
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
import asyncio

logger = logging.getLogger("sentinel.ingestion")

from app.services.data_sources import (
    DataSource,
    SimulationSource,
    DataSourceType,
    DataSourceFactory,
    create_demo_source,
)
from app.models.analytics import Event
from app.core.security import privacy


class IngestionPipeline:
    """
    Orchestrates data ingestion from multiple sources.

    Usage - Demo Mode:
        pipeline = IngestionPipeline(db)
        pipeline.add_source(create_demo_source('alex_burnout'))
        await pipeline.ingest_user('alex@example.com', days=30)

    Usage - Production Mode:
        pipeline = IngestionPipeline(db)

        # Try real Slack, fall back to simulation
        slack_source = DataSourceFactory.create_hybrid_source(
            primary_source=DataSourceType.SLACK,
            primary_config={'bot_token': 'xoxb-...'},
            fallback_config={'persona_type': 'jordan_steady'}
        )
        pipeline.add_source(slack_source)

        await pipeline.ingest_user('user@example.com', days=30)
    """

    def __init__(self, db: Session, batch_size: int = 100):
        self.db = db
        self.sources: List[DataSource] = []
        self.batch_size = batch_size

    def add_source(self, source: DataSource):
        """Add a data source to the pipeline"""
        self.sources.append(source)

    async def ingest_user(
        self,
        user_email: str,
        days: int = 30,
        source_type: Optional[DataSourceType] = None,
    ) -> Dict[str, Any]:
        """
        Ingest data for a specific user.

        Args:
            user_email: User's email address
            days: How many days of history to ingest
            source_type: If specified, only use sources of this type

        Returns:
            Ingestion summary with counts and status
        """
        user_hash = privacy.hash_identity(user_email)
        since = datetime.utcnow() - timedelta(days=days)

        results = {
            "user_email": user_email,
            "user_hash": user_hash,
            "sources_processed": 0,
            "events_ingested": 0,
            "errors": [],
            "source_details": [],
        }

        # Filter sources if specific type requested
        sources_to_use = self.sources
        if source_type:
            sources_to_use = [s for s in self.sources if s.source_type == source_type]

        for source in sources_to_use:
            try:
                # Connect to source
                if not source.is_connected:
                    connected = await source.connect()
                    if not connected:
                        results["errors"].append(
                            f"Failed to connect to {source.source_type.value}"
                        )
                        continue

                # Stream and ingest events
                source_events = 0
                batch = []

                async for raw_event in source.stream_events(user_email, since):
                    # Convert to analytics Event
                    event = Event(
                        user_hash=user_hash,
                        timestamp=raw_event.timestamp,
                        event_type=raw_event.event_type,
                        metadata_=raw_event.metadata,
                    )
                    batch.append(event)
                    source_events += 1

                    # Persist batch when full
                    if len(batch) >= self.batch_size:
                        self._persist_batch(batch)
                        batch = []

                # Persist remaining events
                if batch:
                    self._persist_batch(batch)

                results["sources_processed"] += 1
                results["events_ingested"] += source_events
                results["source_details"].append(
                    {
                        "type": source.source_type.value,
                        "events": source_events,
                        "status": "success",
                    }
                )

            except Exception as e:
                error_msg = f"Error processing {source.source_type.value}: {str(e)}"
                results["errors"].append(error_msg)
                results["source_details"].append(
                    {
                        "type": source.source_type.value,
                        "events": 0,
                        "status": "error",
                        "error": str(e),
                    }
                )

        return results

    def _persist_batch(self, events: List[Event]):
        """Persist a batch of events to the database"""
        for event in events:
            self.db.add(event)
        self.db.commit()

    async def ingest_team(
        self, user_emails: List[str], days: int = 30
    ) -> Dict[str, Any]:
        """
        Ingest data for multiple users (team).

        Args:
            user_emails: List of user email addresses
            days: How many days of history to ingest

        Returns:
            Team ingestion summary
        """
        results = {
            "team_size": len(user_emails),
            "users_processed": 0,
            "total_events": 0,
            "user_results": [],
        }

        for email in user_emails:
            user_result = await self.ingest_user(email, days)
            results["users_processed"] += 1
            results["total_events"] += user_result["events_ingested"]
            results["user_results"].append(user_result)

        return results

    async def health_check(self) -> Dict[str, Any]:
        """Check health of all data sources"""
        health_status = {"overall": "healthy", "sources": []}

        for source in self.sources:
            try:
                source_health = await source.health_check()
                health_status["sources"].append(source_health)

                if source_health.get("status") != "healthy":
                    health_status["overall"] = "degraded"
            except Exception as e:
                health_status["sources"].append(
                    {
                        "type": source.source_type.value,
                        "status": "error",
                        "message": str(e),
                    }
                )
                health_status["overall"] = "degraded"

        return health_status

    async def close(self):
        """Close all data source connections"""
        for source in self.sources:
            try:
                await source.disconnect()
            except Exception as e:
                logger.error("Error disconnecting %s: %s", source.source_type.value, e)


class QuickIngestor:
    """
    Quick helper for common ingestion scenarios.

    Usage:
        # Quick demo data
        result = await QuickIngestor.demo_user(db, 'alex@example.com', 'alex_burnout')

        # Quick production data (with fallback)
        result = await QuickIngestor.production_user(
            db, 'user@example.com',
            slack_token='xoxb-...',
            days=30
        )
    """

    @staticmethod
    async def demo_user(
        db: Session,
        user_email: str,
        persona_type: str = "jordan_steady",
        days: int = 30,
    ) -> Dict[str, Any]:
        """
        Quickly ingest simulated demo data for a user.

        Args:
            db: Database session
            user_email: User email
            persona_type: One of 'alex_burnout', 'sarah_gem', 'jordan_steady', 'maria_contagion'
            days: Days of history to generate

        Returns:
            Ingestion result
        """
        pipeline = IngestionPipeline(db)
        source = create_demo_source(persona_type)
        pipeline.add_source(source)

        result = await pipeline.ingest_user(user_email, days)
        await pipeline.close()

        return result

    @staticmethod
    async def demo_team(
        db: Session, team_config: List[Dict[str, str]], days: int = 30
    ) -> Dict[str, Any]:
        """
        Ingest simulated data for a whole team.

        Args:
            db: Database session
            team_config: List of {'email': '...', 'persona': '...'}
            days: Days of history

        Returns:
            Team ingestion result
        """
        results = {"team_size": len(team_config), "users": []}

        for config in team_config:
            result = await QuickIngestor.demo_user(
                db, config["email"], config.get("persona", "jordan_steady"), days
            )
            results["users"].append(result)

        return results

    @staticmethod
    async def production_user(
        db: Session,
        user_email: str,
        slack_token: Optional[str] = None,
        github_token: Optional[str] = None,
        days: int = 30,
        fallback_persona: str = "jordan_steady",
    ) -> Dict[str, Any]:
        """
        Ingest production data with simulation fallback.

        Args:
            db: Database session
            user_email: User email
            slack_token: Slack bot token (optional)
            github_token: GitHub token (optional)
            days: Days of history
            fallback_persona: Persona to use if real integrations fail

        Returns:
            Ingestion result
        """
        pipeline = IngestionPipeline(db)

        # Add Slack source if token provided
        if slack_token:
            slack_source = DataSourceFactory.create_hybrid_source(
                primary_source=DataSourceType.SLACK,
                primary_config={"bot_token": slack_token},
                fallback_config={"persona_type": fallback_persona},
            )
            pipeline.add_source(slack_source)

        # Add GitHub source if token provided
        if github_token:
            github_source = DataSourceFactory.create_hybrid_source(
                primary_source=DataSourceType.GITHUB,
                primary_config={"access_token": github_token},
                fallback_config={"persona_type": fallback_persona},
            )
            pipeline.add_source(github_source)

        # If no real integrations, just use simulation
        if not slack_token and not github_token:
            source = create_demo_source(fallback_persona)
            pipeline.add_source(source)

        result = await pipeline.ingest_user(user_email, days)
        await pipeline.close()

        return result


# Pre-configured demo scenarios
DEMO_SCENARIOS = {
    "burnout_crisis": [
        {"email": "alex@example.com", "persona": "alex_burnout"},
        {"email": "sarah@example.com", "persona": "sarah_gem"},
        {"email": "jordan@example.com", "persona": "jordan_steady"},
    ],
    "team_contagion": [
        {"email": "maria@example.com", "persona": "maria_contagion"},
        {"email": "alex@example.com", "persona": "alex_burnout"},
        {"email": "sarah@example.com", "persona": "sarah_gem"},
        {"email": "jordan@example.com", "persona": "jordan_steady"},
    ],
    "healthy_team": [
        {"email": "sarah@example.com", "persona": "sarah_gem"},
        {"email": "jordan@example.com", "persona": "jordan_steady"},
        {"email": "mike@example.com", "persona": "jordan_steady"},
    ],
}


async def seed_demo_data(db: Session, scenario: str = "burnout_crisis"):
    """
    Seed database with demo scenario data.

    Args:
        db: Database session
        scenario: One of 'burnout_crisis', 'team_contagion', 'healthy_team'
    """
    if scenario not in DEMO_SCENARIOS:
        raise ValueError(
            f"Unknown scenario: {scenario}. Use one of {list(DEMO_SCENARIOS.keys())}"
        )

    team_config = DEMO_SCENARIOS[scenario]
    return await QuickIngestor.demo_team(db, team_config, days=30)
