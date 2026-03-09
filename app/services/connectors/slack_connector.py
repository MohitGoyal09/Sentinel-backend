"""Slack connector - ingests messaging activity patterns."""

from datetime import datetime
from typing import Optional

from .base import BaseConnector, ConnectorStatus, NormalizedEvent


class SlackConnector(BaseConnector):
    def __init__(self, bot_token: str = ""):
        super().__init__("Slack")
        self.bot_token = bot_token

    async def connect(self) -> bool:
        if not self.bot_token:
            self._status = ConnectorStatus.DISCONNECTED
            return False
        # In production: validate token via Slack API auth.test
        self._status = ConnectorStatus.CONNECTED
        return True

    async def fetch_events(self, since: Optional[datetime] = None) -> list[NormalizedEvent]:
        # In production: use Slack conversations.history API
        return []

    @staticmethod
    def parse_message(msg_data: dict) -> NormalizedEvent:
        """Parse a Slack message into a NormalizedEvent."""
        timestamp = msg_data.get("timestamp", datetime.utcnow().isoformat())
        hour = datetime.fromisoformat(str(timestamp)).hour

        risk_signal = "neutral"
        if hour >= 22 or hour <= 5:
            risk_signal = "negative"

        return NormalizedEvent(
            source="slack",
            event_type="message",
            user_identifier=msg_data.get("user_email", "unknown"),
            timestamp=datetime.fromisoformat(str(timestamp)),
            metadata={
                "channel": msg_data.get("channel", "general"),
                "is_reply": msg_data.get("is_reply", False),
                "reaction_count": msg_data.get("reaction_count", 0),
                "hour_of_day": hour,
            },
            risk_signal=risk_signal,
        )
