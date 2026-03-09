"""CSV connector - allows manual data upload via CSV files for demo and onboarding."""

import csv
import io
from datetime import datetime
from typing import Optional

from .base import BaseConnector, ConnectorStatus, NormalizedEvent


EXPECTED_COLUMNS = {"timestamp", "user_email", "event_type", "source"}


class CSVConnector(BaseConnector):
    def __init__(self):
        super().__init__("CSV Upload")
        self._status = ConnectorStatus.CONNECTED  # Always available

    async def connect(self) -> bool:
        self._status = ConnectorStatus.CONNECTED
        return True

    async def fetch_events(self, since: Optional[datetime] = None) -> list[NormalizedEvent]:
        return []  # CSV is push-based, not pull-based

    @staticmethod
    def parse_csv(content: str) -> tuple[list[NormalizedEvent], list[str]]:
        """
        Parse CSV text into NormalizedEvents.
        Returns (events, errors).
        Expected columns: timestamp, user_email, event_type, source
        Optional columns: metadata_*, risk_signal
        """
        events: list[NormalizedEvent] = []
        errors: list[str] = []

        try:
            reader = csv.DictReader(io.StringIO(content))
            if not reader.fieldnames:
                return [], ["Empty CSV or missing header row"]

            fields = set(reader.fieldnames)
            missing = EXPECTED_COLUMNS - fields
            if missing:
                return [], [f"Missing required columns: {', '.join(missing)}"]

            for i, row in enumerate(reader, start=2):
                try:
                    ts_raw = row.get("timestamp", "").strip()
                    if not ts_raw:
                        errors.append(f"Row {i}: missing timestamp")
                        continue

                    # Try multiple timestamp formats
                    ts = None
                    for fmt in [
                        "%Y-%m-%dT%H:%M:%S",
                        "%Y-%m-%d %H:%M:%S",
                        "%Y-%m-%dT%H:%M:%S.%f",
                        "%Y-%m-%d %H:%M",
                        "%Y-%m-%d",
                    ]:
                        try:
                            ts = datetime.strptime(ts_raw, fmt)
                            break
                        except ValueError:
                            continue
                    if ts is None:
                        errors.append(f"Row {i}: invalid timestamp format '{ts_raw}'")
                        continue

                    # Collect metadata_* columns
                    metadata = {}
                    for key, val in row.items():
                        if key.startswith("metadata_") and val:
                            metadata[key.replace("metadata_", "")] = val

                    event = NormalizedEvent(
                        source=row.get("source", "csv").strip(),
                        event_type=row.get("event_type", "unknown").strip(),
                        user_identifier=row.get("user_email", "").strip(),
                        timestamp=ts,
                        metadata=metadata,
                        risk_signal=row.get("risk_signal", "neutral").strip() or "neutral",
                    )
                    events.append(event)
                except Exception as e:
                    errors.append(f"Row {i}: {str(e)}")

        except csv.Error as e:
            errors.append(f"CSV parse error: {str(e)}")

        return events, errors
