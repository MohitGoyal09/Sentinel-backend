"""Jira connector - ingests ticket and sprint activity data."""

from datetime import datetime
from typing import Optional

from .base import BaseConnector, ConnectorStatus, NormalizedEvent


class JiraConnector(BaseConnector):
    def __init__(self, api_key: str = "", base_url: str = ""):
        super().__init__("Jira")
        self.api_key = api_key
        self.base_url = base_url

    async def connect(self) -> bool:
        if not self.api_key:
            self._status = ConnectorStatus.DISCONNECTED
            return False
        # In production: validate via Jira REST API /rest/api/3/myself
        self._status = ConnectorStatus.CONNECTED
        return True

    async def fetch_events(self, since: Optional[datetime] = None) -> list[NormalizedEvent]:
        # In production: poll Jira changelog via REST API
        return []

    @staticmethod
    def parse_ticket_event(ticket_data: dict) -> NormalizedEvent:
        """Parse a Jira ticket event into a NormalizedEvent."""
        event_type = ticket_data.get("event_type", "ticket_updated")
        risk_signal = "neutral"

        # Overdue tickets or too many in-progress items signal overload
        if ticket_data.get("is_overdue"):
            risk_signal = "negative"
        elif event_type == "ticket_completed":
            risk_signal = "positive"

        return NormalizedEvent(
            source="jira",
            event_type=event_type,
            user_identifier=ticket_data.get("assignee_email", "unknown"),
            timestamp=datetime.fromisoformat(
                str(ticket_data.get("timestamp", datetime.utcnow().isoformat()))
            ),
            metadata={
                "ticket_key": ticket_data.get("key", ""),
                "status": ticket_data.get("status", ""),
                "priority": ticket_data.get("priority", "Medium"),
                "sprint": ticket_data.get("sprint", ""),
                "is_overdue": ticket_data.get("is_overdue", False),
            },
            risk_signal=risk_signal,
        )
