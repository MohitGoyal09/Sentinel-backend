"""
Hybrid Data Integration System
Supports both simulation (demo mode) and real integrations (production)
"""

import logging
from abc import ABC, abstractmethod
from typing import Iterator, Dict, List, Optional, Any
from datetime import datetime
from enum import Enum
import asyncio
from dataclasses import dataclass

logger = logging.getLogger("sentinel.data_sources")


class DataSourceType(Enum):
    """Available data source types"""

    SIMULATION = "simulation"
    SLACK = "slack"
    GITHUB = "github"
    JIRA = "jira"
    CALENDAR = "calendar"


@dataclass
class RawEvent:
    """Standardized raw event from any data source"""

    source: str  # 'simulation', 'slack', 'github', etc.
    user_email: str
    timestamp: datetime
    event_type: str  # 'commit', 'slack_message', 'pr_review', etc.
    metadata: Dict[str, Any]

    def to_analytics_event(self, user_hash: str) -> Dict:
        """Convert to analytics Event format"""
        return {
            "user_hash": user_hash,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "metadata": self.metadata,
            "source": self.source,
        }


class DataSource(ABC):
    """
    Abstract base class for all data sources.
    Implementations: SimulationSource, SlackSource, GitHubSource, etc.
    """

    def __init__(self, source_type: DataSourceType, config: Dict[str, Any]):
        self.source_type = source_type
        self.config = config
        self.is_connected = False

    @abstractmethod
    async def connect(self) -> bool:
        """
        Establish connection to the data source.
        Returns True if successful.
        """
        pass

    @abstractmethod
    async def stream_events(
        self, user_email: str, since: datetime, until: Optional[datetime] = None
    ) -> Iterator[RawEvent]:
        """
        Stream events for a specific user from the data source.
        """
        pass

    @abstractmethod
    async def health_check(self) -> Dict[str, Any]:
        """
        Check if the data source is healthy and accessible.
        """
        pass

    async def disconnect(self):
        """Clean up connections"""
        self.is_connected = False


class SimulationSource(DataSource):
    """
    Simulation data source for demo and development.
    Generates synthetic behavioral data for personas.
    """

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(DataSourceType.SIMULATION, config or {})
        self.rng = None
        self._setup_rng()

    def _setup_rng(self):
        """Initialize random number generator"""
        import numpy as np

        seed = self.config.get("seed", 42)
        self.rng = np.random.default_rng(seed)

    async def connect(self) -> bool:
        """Simulation is always 'connected'"""
        self.is_connected = True
        return True

    async def stream_events(
        self, user_email: str, since: datetime, until: Optional[datetime] = None
    ) -> Iterator[RawEvent]:
        """
        Generate simulated events for the user.
        Uses persona type from config if available.
        """
        persona_type = self.config.get("persona_type", "jordan_steady")
        days = (datetime.utcnow() - since).days

        if until:
            days = (until - since).days

        events = self._generate_persona_events(user_email, persona_type, days)
        for event in events:
            yield event

    def _generate_persona_events(
        self, user_email: str, persona_type: str, days: int
    ) -> List[RawEvent]:
        """Generate events based on persona type"""

        generators = {
            "alex_burnout": self._alex_burnout_events,
            "sarah_gem": self._sarah_gem_events,
            "jordan_steady": self._jordan_steady_events,
            "maria_contagion": self._maria_contagion_events,
        }

        generator = generators.get(persona_type, self._jordan_steady_events)
        return generator(user_email, days)

    def _alex_burnout_events(self, user_email: str, days: int) -> List[RawEvent]:
        """Generate Alex burnout pattern: escalating late nights"""
        events = []
        base = datetime.utcnow() - timedelta(days=days)

        for day in range(days):
            current = base + timedelta(days=day)

            # Week 1: Normal 9-5
            if day < 7:
                hour = int(self.rng.normal(14, 1))
                late = False
                switches = 2
            # Week 2-3: Drift to late nights
            elif day < 21:
                hour = 18 + int((day - 7) * 0.5)
                late = hour > 20
                switches = 4
            # Week 4+: Crash
            else:
                hour = 22 + int(self.rng.exponential(3))
                late = True
                switches = 8

            # 3-5 events per day
            for _ in range(self.rng.integers(3, 6)):
                events.append(
                    RawEvent(
                        source="simulation",
                        user_email=user_email,
                        timestamp=current.replace(
                            hour=min(hour, 23), minute=int(self.rng.integers(0, 60))
                        ),
                        event_type="commit",
                        metadata={
                            "after_hours": late,
                            "context_switches": switches,
                            "is_reply": self.rng.random() > 0.3,
                            "persona": "alex_burnout",
                        },
                    )
                )

        return events

    def _sarah_gem_events(self, user_email: str, days: int) -> List[RawEvent]:
        """Generate Sarah hidden gem pattern: steady hours, high impact"""
        events = []
        base = datetime.utcnow() - timedelta(days=days)

        for day in range(days):
            current = base + timedelta(days=day)
            hour = int(self.rng.normal(13, 1))

            # Regular commits
            events.append(
                RawEvent(
                    source="simulation",
                    user_email=user_email,
                    timestamp=current.replace(hour=hour),
                    event_type="commit",
                    metadata={
                        "after_hours": False,
                        "context_switches": 1,
                        "persona": "sarah_gem",
                    },
                )
            )

            # Helpful PR reviews (creates network edges)
            if self.rng.random() > 0.3:
                events.append(
                    RawEvent(
                        source="simulation",
                        user_email=user_email,
                        timestamp=current.replace(hour=min(hour + 2, 23)),
                        event_type="pr_review",
                        metadata={
                            "after_hours": False,
                            "comment_length": int(self.rng.normal(300, 50)),
                            "unblocked": True,
                            "persona": "sarah_gem",
                        },
                    )
                )

        return events

    def _jordan_steady_events(self, user_email: str, days: int) -> List[RawEvent]:
        """Generate Jordan steady pattern: consistent 9-6"""
        events = []
        base = datetime.utcnow() - timedelta(days=days)

        for day in range(days):
            current = base + timedelta(days=day)

            # One late night per sprint (every 14 days)
            is_sprint_end = day % 14 == 13
            if is_sprint_end:
                hour = 20
            else:
                hour = int(self.rng.normal(11, 1))

            events.append(
                RawEvent(
                    source="simulation",
                    user_email=user_email,
                    timestamp=current.replace(hour=hour),
                    event_type="commit",
                    metadata={
                        "after_hours": hour > 19,
                        "context_switches": 2,
                        "is_sprint_end": is_sprint_end,
                        "persona": "jordan_steady",
                    },
                )
            )

        return events

    def _maria_contagion_events(self, user_email: str, days: int) -> List[RawEvent]:
        """Generate Maria contagion pattern: declining mood affecting others"""
        events = []
        base = datetime.utcnow() - timedelta(days=days)

        for day in range(days):
            current = base + timedelta(days=day)

            if day < 14:
                # Normal first 2 weeks
                hour = int(self.rng.normal(14, 1))
                sentiment = "neutral"
            else:
                # Declining pattern
                hour = 20 + int((day - 14) * 0.5)
                sentiment = "negative" if day > 21 else "neutral"

            events.append(
                RawEvent(
                    source="simulation",
                    user_email=user_email,
                    timestamp=current.replace(hour=min(hour, 23)),
                    event_type="slack_message",
                    metadata={
                        "after_hours": hour > 19,
                        "sentiment": sentiment,
                        "mentions_resignation": day > 21,
                        "persona": "maria_contagion",
                    },
                )
            )

        return events

    async def health_check(self) -> Dict[str, Any]:
        """Simulation is always healthy"""
        return {
            "status": "healthy",
            "connected": True,
            "type": "simulation",
            "message": "Simulation data source is ready",
        }


class SlackSource(DataSource):
    """
    Real Slack integration using Slack Events API.
    Requires: bot_token in config
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(DataSourceType.SLACK, config)
        self.client = None
        self.bot_token = config.get("bot_token")

    async def connect(self) -> bool:
        """Connect to Slack API"""
        if not self.bot_token:
            raise ValueError("Slack bot_token required in config")

        try:
            from slack_sdk import WebClient

            self.client = WebClient(token=self.bot_token)

            # Test connection
            auth_test = self.client.auth_test()
            self.is_connected = auth_test["ok"]
            return self.is_connected
        except Exception as e:
            logger.error("Failed to connect to Slack: %s", e)
            self.is_connected = False
            return False

    async def stream_events(
        self, user_email: str, since: datetime, until: Optional[datetime] = None
    ) -> Iterator[RawEvent]:
        """
        Stream Slack messages, reactions, and presence.
        Note: Requires proper OAuth scopes.
        """
        if not self.is_connected:
            raise RuntimeError("Slack source not connected. Call connect() first.")

        # Get user ID from email
        user_id = await self._get_user_id(user_email)
        if not user_id:
            logger.warning("Slack user not found for email: %s", user_email)
            return

        # Fetch conversation history from channels user is in
        conversations = self._get_user_conversations(user_id)

        for channel_id in conversations:
            try:
                result = self.client.conversations_history(
                    channel=channel_id,
                    oldest=str(since.timestamp()),
                    latest=str(until.timestamp()) if until else None,
                    limit=1000,
                )

                for message in result["messages"]:
                    if message.get("user") == user_id:
                        yield self._transform_slack_message(message, user_email)

            except Exception as e:
                logger.error("Error fetching Slack history for channel %s: %s", channel_id, e)
                continue

    def _transform_slack_message(self, message: Dict, user_email: str) -> RawEvent:
        """Transform Slack message to standardized event"""
        ts = datetime.fromtimestamp(float(message["ts"]))

        return RawEvent(
            source="slack",
            user_email=user_email,
            timestamp=ts,
            event_type="slack_message",
            metadata={
                "after_hours": self._is_after_hours(ts),
                "is_reply": "thread_ts" in message,
                "channel_type": "public",  # Could be determined from channel info
                "has_reactions": bool(message.get("reactions")),
                "word_count": len(message.get("text", "").split()),
            },
        )

    async def _get_user_id(self, user_email: str) -> Optional[str]:
        """Lookup Slack user ID by email"""
        try:
            result = self.client.users_lookupByEmail(email=user_email)
            if result["ok"]:
                return result["user"]["id"]
        except Exception as e:
            logger.error("Error looking up Slack user: %s", e)
        return None

    def _get_user_conversations(self, user_id: str) -> List[str]:
        """Get list of channels user participates in"""
        try:
            result = self.client.users_conversations(
                user=user_id, types="public_channel,private_channel"
            )
            return [c["id"] for c in result["channels"]]
        except Exception as e:
            logger.error("Error fetching user conversations: %s", e)
            return []

    def _is_after_hours(self, timestamp: datetime) -> bool:
        """Check if timestamp is after hours (before 9am or after 6pm)"""
        hour = timestamp.hour
        return hour < 9 or hour > 18

    async def health_check(self) -> Dict[str, Any]:
        """Check Slack API health"""
        try:
            if not self.is_connected:
                return {
                    "status": "disconnected",
                    "connected": False,
                    "type": "slack",
                    "message": "Not connected to Slack",
                }

            auth_test = self.client.auth_test()
            return {
                "status": "healthy" if auth_test["ok"] else "error",
                "connected": auth_test["ok"],
                "type": "slack",
                "workspace": auth_test.get("team", "Unknown"),
                "message": f"Connected to Slack workspace: {auth_test.get('team', 'Unknown')}",
            }
        except Exception as e:
            return {
                "status": "error",
                "connected": False,
                "type": "slack",
                "message": str(e),
            }


class GitHubSource(DataSource):
    """
    Real GitHub integration using GitHub API.
    Requires: access_token in config
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(DataSourceType.GITHUB, config)
        self.access_token = config.get("access_token")
        self.base_url = config.get("base_url", "https://api.github.com")

    async def connect(self) -> bool:
        """Connect to GitHub API"""
        if not self.access_token:
            raise ValueError("GitHub access_token required in config")

        try:
            import httpx

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/user",
                    headers={"Authorization": f"token {self.access_token}"},
                )
                self.is_connected = response.status_code == 200
                return self.is_connected
        except Exception as e:
            logger.error("Failed to connect to GitHub: %s", e)
            self.is_connected = False
            return False

    async def stream_events(
        self, user_email: str, since: datetime, until: Optional[datetime] = None
    ) -> Iterator[RawEvent]:
        """Stream GitHub commits, PRs, and reviews"""
        if not self.is_connected:
            raise RuntimeError("GitHub source not connected. Call connect() first.")

        # Fetch events from GitHub API
        # This is a simplified version - real implementation would paginate and handle rate limits

        # Get user's events
        events = await self._fetch_user_events(user_email, since, until)

        for event in events:
            yield self._transform_github_event(event, user_email)

    async def _fetch_user_events(
        self, user_email: str, since: datetime, until: Optional[datetime]
    ) -> List[Dict]:
        """Fetch events from GitHub API"""
        import httpx

        username = self.config.get("username")  # Should lookup from email
        if not username:
            return []

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/users/{username}/events",
                headers={"Authorization": f"token {self.access_token}"},
                params={"per_page": 100},
            )

            if response.status_code == 200:
                return response.json()
            return []

    def _transform_github_event(self, event: Dict, user_email: str) -> RawEvent:
        """Transform GitHub event to standardized format"""
        event_type_map = {
            "PushEvent": "commit",
            "PullRequestReviewEvent": "pr_review",
            "PullRequestEvent": "pr_created",
            "IssuesEvent": "issue_activity",
        }

        event_type = event_type_map.get(event["type"], "github_activity")
        timestamp = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))

        # Extract metadata based on event type
        metadata = {
            "after_hours": self._is_after_hours(timestamp),
            "github_event_type": event["type"],
            "repo": event.get("repo", {}).get("name", "unknown"),
        }

        if event["type"] == "PullRequestReviewEvent":
            payload = event.get("payload", {})
            review = payload.get("review", {})
            metadata.update(
                {
                    "comment_length": len(review.get("body", "")),
                    "is_unblocking": review.get("state") == "approved",
                }
            )

        return RawEvent(
            source="github",
            user_email=user_email,
            timestamp=timestamp,
            event_type=event_type,
            metadata=metadata,
        )

    def _is_after_hours(self, timestamp: datetime) -> bool:
        """Check if timestamp is after hours"""
        hour = timestamp.hour
        return hour < 9 or hour > 18

    async def health_check(self) -> Dict[str, Any]:
        """Check GitHub API health"""
        try:
            if not self.is_connected:
                return {
                    "status": "disconnected",
                    "connected": False,
                    "type": "github",
                    "message": "Not connected to GitHub",
                }

            import httpx

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rate_limit",
                    headers={"Authorization": f"token {self.access_token}"},
                )

                if response.status_code == 200:
                    rate_data = response.json()
                    remaining = rate_data["resources"]["core"]["remaining"]
                    return {
                        "status": "healthy",
                        "connected": True,
                        "type": "github",
                        "rate_limit_remaining": remaining,
                        "message": f"Connected to GitHub. API calls remaining: {remaining}",
                    }
                else:
                    return {
                        "status": "error",
                        "connected": False,
                        "type": "github",
                        "message": f"GitHub API error: {response.status_code}",
                    }
        except Exception as e:
            return {
                "status": "error",
                "connected": False,
                "type": "github",
                "message": str(e),
            }


class DataSourceFactory:
    """
    Factory for creating data sources.
    Supports switching between simulation and real integrations.
    """

    _sources: Dict[DataSourceType, type] = {
        DataSourceType.SIMULATION: SimulationSource,
        DataSourceType.SLACK: SlackSource,
        DataSourceType.GITHUB: GitHubSource,
        # Add more sources here
    }

    @classmethod
    def create_source(
        cls, source_type: DataSourceType, config: Optional[Dict[str, Any]] = None
    ) -> DataSource:
        """
        Create a data source of the specified type.

        Args:
            source_type: Type of data source to create
            config: Configuration for the source (tokens, settings, etc.)

        Returns:
            Configured DataSource instance
        """
        source_class = cls._sources.get(source_type)
        if not source_class:
            raise ValueError(f"Unknown data source type: {source_type}")

        return source_class(config or {})

    @classmethod
    def create_hybrid_source(
        cls,
        primary_source: DataSourceType,
        fallback_source: DataSourceType = DataSourceType.SIMULATION,
        primary_config: Optional[Dict[str, Any]] = None,
        fallback_config: Optional[Dict[str, Any]] = None,
    ) -> "HybridDataSource":
        """
        Create a hybrid source that tries real integration first,
        falls back to simulation if unavailable.
        """
        return HybridDataSource(
            primary=cls.create_source(primary_source, primary_config),
            fallback=cls.create_source(fallback_source, fallback_config),
        )

    @classmethod
    def register_source(cls, source_type: DataSourceType, source_class: type):
        """Register a new data source type"""
        cls._sources[source_type] = source_class


class HybridDataSource(DataSource):
    """
    Combines multiple data sources with fallback logic.

    Usage:
        # Try Slack, fall back to simulation
        source = DataSourceFactory.create_hybrid_source(
            primary_source=DataSourceType.SLACK,
            primary_config={'bot_token': 'xoxb-...'},
            fallback_config={'persona_type': 'alex_burnout'}
        )
    """

    def __init__(self, primary: DataSource, fallback: DataSource):
        super().__init__(DataSourceType.SIMULATION, {})
        self.primary = primary
        self.fallback = fallback
        self.using_fallback = False

    async def connect(self) -> bool:
        """Try primary first, fall back if needed"""
        # Try primary source
        try:
            if await self.primary.connect():
                self.is_connected = True
                self.using_fallback = False
                return True
        except Exception as e:
            logger.warning("Primary source failed: %s, trying fallback...", e)

        # Fall back to simulation
        try:
            if await self.fallback.connect():
                self.is_connected = True
                self.using_fallback = True
                logger.warning("Using fallback source: %s", self.fallback.source_type.value)
                return True
        except Exception as e:
            logger.error("Fallback source also failed: %s", e)

        self.is_connected = False
        return False

    async def stream_events(
        self, user_email: str, since: datetime, until: Optional[datetime] = None
    ) -> Iterator[RawEvent]:
        """Stream from active source"""
        source = self.fallback if self.using_fallback else self.primary
        async for event in source.stream_events(user_email, since, until):
            yield event

    async def health_check(self) -> Dict[str, Any]:
        """Check health of both sources"""
        primary_health = await self.primary.health_check()
        fallback_health = await self.fallback.health_check()

        return {
            "status": "healthy" if self.is_connected else "error",
            "connected": self.is_connected,
            "using_fallback": self.using_fallback,
            "primary": primary_health,
            "fallback": fallback_health,
        }

    async def disconnect(self):
        """Disconnect both sources"""
        await self.primary.disconnect()
        await self.fallback.disconnect()
        self.is_connected = False


# Convenience function for quick setup
def create_demo_source(persona_type: str = "jordan_steady") -> SimulationSource:
    """
    Quick factory for demo/simulation mode.

    Usage:
        source = create_demo_source('alex_burnout')
        await source.connect()
        events = source.stream_events('user@example.com', since=datetime.utcnow() - timedelta(days=30))
    """
    return SimulationSource(config={"persona_type": persona_type})


def create_production_source(
    slack_token: Optional[str] = None, github_token: Optional[str] = None
) -> HybridDataSource:
    """
    Quick factory for production mode with fallbacks.

    Usage:
        source = create_production_source(
            slack_token='xoxb-...',
            github_token='ghp-...'
        )
        await source.connect()
        # Will use real integrations if available, fall back to simulation
    """
    return DataSourceFactory.create_hybrid_source(
        primary_source=DataSourceType.SLACK
        if slack_token
        else DataSourceType.SIMULATION,
        primary_config={"bot_token": slack_token} if slack_token else {},
        fallback_config={"persona_type": "jordan_steady"},
    )


# Import at end to avoid circular import
from datetime import timedelta
