"""Git connector - ingests commit data from Git repositories."""

from datetime import datetime
from typing import Optional

from .base import BaseConnector, ConnectorStatus, NormalizedEvent


class GitConnector(BaseConnector):
    def __init__(self, repo_url: str = ""):
        super().__init__("Git")
        self.repo_url = repo_url

    async def connect(self) -> bool:
        # In production: validate repo access via SSH/HTTPS
        self._status = ConnectorStatus.CONNECTED
        return True

    async def fetch_events(self, since: Optional[datetime] = None) -> list[NormalizedEvent]:
        # In production: parse `git log` output or use GitHub/GitLab API
        return []

    @staticmethod
    def parse_commit(commit_data: dict) -> NormalizedEvent:
        """Parse a single commit into a NormalizedEvent."""
        timestamp = commit_data.get("timestamp", datetime.utcnow().isoformat())
        hour = datetime.fromisoformat(str(timestamp)).hour

        # Detect risk signals from commit patterns
        risk_signal = "neutral"
        if hour >= 22 or hour <= 5:
            risk_signal = "negative"  # Late-night commits
        elif 9 <= hour <= 17:
            risk_signal = "positive"

        return NormalizedEvent(
            source="git",
            event_type="commit",
            user_identifier=commit_data.get("author_email", "unknown"),
            timestamp=datetime.fromisoformat(str(timestamp)),
            metadata={
                "message": commit_data.get("message", "")[:200],
                "files_changed": commit_data.get("files_changed", 0),
                "additions": commit_data.get("additions", 0),
                "deletions": commit_data.get("deletions", 0),
                "hour_of_day": hour,
                "after_hours": hour >= 21 or hour <= 6,
                "source": "github",
                "source_id": commit_data.get("sha", ""),
            },
            risk_signal=risk_signal,
        )
