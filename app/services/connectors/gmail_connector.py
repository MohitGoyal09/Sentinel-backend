"""Gmail connector - ingests email activity patterns (metadata only, never content)."""

from datetime import datetime
from typing import Optional

from .base import BaseConnector, ConnectorStatus, NormalizedEvent


class GmailConnector(BaseConnector):
    def __init__(self):
        super().__init__("Gmail")

    async def connect(self) -> bool:
        self._status = ConnectorStatus.CONNECTED
        return True

    async def fetch_events(self, since: Optional[datetime] = None) -> list[NormalizedEvent]:
        return []

    @staticmethod
    def parse_email(email_data: dict) -> NormalizedEvent:
        """Parse Gmail metadata into a NormalizedEvent.

        We capture:
        - timestamp (when was the email sent?)
        - recipient_count (how many people?)
        - is_reply (reply vs new thread?)
        - after_hours (sent outside 8am-6pm?)

        We NEVER capture:
        - Subject line
        - Body text
        - Attachment contents
        """
        timestamp = email_data.get("timestamp", datetime.utcnow().isoformat())
        hour = datetime.fromisoformat(str(timestamp)).hour
        after_hours = hour >= 18 or hour < 8

        recipient_count = email_data.get("recipient_count", 1)
        is_reply = email_data.get("is_reply", False)

        risk_signal = "neutral"
        if after_hours:
            risk_signal = "negative"

        return NormalizedEvent(
            source="gmail",
            event_type="email_sent",
            user_identifier=email_data.get("user_email", "unknown"),
            timestamp=datetime.fromisoformat(str(timestamp)),
            metadata={
                "recipient_count": recipient_count,
                "is_reply": is_reply,
                "mentions_others": recipient_count > 1,
                "after_hours": after_hours,
                "hour_of_day": hour,
                "source": "gmail",
                "source_id": email_data.get("message_id", ""),
            },
            risk_signal=risk_signal,
        )
