import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict
from app.models.analytics import Event, GraphEdge


class RealTimeSimulator:
    """Generate realistic data for demo + real-time streams"""

    def __init__(self, db_session):
        self.db = db_session
        self.rng = np.random.default_rng(42)

    def create_persona(self, persona_type: str, user_hash: str, team_hash: str = None):
        """Batch generate 30 days of history"""
        generators = {
            "alex_burnout": self._alex_burnout,
            "sarah_gem": self._sarah_gem,
            "jordan_steady": self._jordan_steady,
            "maria_contagion": self._maria_contagion,
        }

        if persona_type not in generators:
            # Fallback if unknown
            print(f"Warning: Unknown persona {persona_type}, using jordan_steady")
            return self._jordan_steady(user_hash, team_hash)

        return generators[persona_type](user_hash, team_hash)

    def generate_realtime_event(self, user_hash: str, current_risk: str) -> Dict:
        """Generate next event based on current trajectory (for live demo)"""
        now = datetime.utcnow()

        # Probability of after-hours increases with risk level
        base_late_prob = {"LOW": 0.1, "ELEVATED": 0.4, "CRITICAL": 0.8}.get(
            current_risk, 0.1
        )

        is_late = self.rng.random() < base_late_prob
        hour = 22 if is_late else int(self.rng.normal(14, 2))

        return {
            "user_hash": user_hash,
            "timestamp": now.replace(hour=min(hour, 23)),
            "event_type": self.rng.choice(["commit", "slack_message", "pr_review"]),
            "metadata_": {
                "after_hours": is_late,
                "context_switches": int(self.rng.poisson(5 if is_late else 2)),
                "comment_length": int(self.rng.exponential(100))
                if self.rng.random() > 0.7
                else 0,
            },
        }

    def _alex_burnout(self, user_hash: str, team_hash: str) -> List[Event]:
        """The escalation curve: 2PM -> 2AM"""
        events = []
        base = datetime.utcnow() - timedelta(days=30)

        for day in range(30):
            current = base + timedelta(days=day)

            # Week 1: Normal
            if day < 7:
                hour = int(self.rng.normal(14, 1))
                late = False
                switches = 2

            # Week 2-3: Drift
            elif day < 21:
                hour = 18 + int((day - 7) * 0.5)
                late = hour > 20
                switches = 4

            # Week 4: Crash
            else:
                hour = 22 + int(self.rng.exponential(3))
                late = True
                switches = 8

            # 3-5 events per day
            for _ in range(self.rng.integers(3, 6)):
                events.append(
                    Event(
                        user_hash=user_hash,
                        timestamp=current.replace(
                            hour=min(hour, 23), minute=self.rng.integers(0, 60)
                        ),
                        event_type="commit",
                        metadata_={
                            "after_hours": late,
                            "context_switches": switches,
                            "is_reply": self.rng.random()
                            > 0.3,  # For belongingness calc
                        },
                    )
                )

        return events

    def _sarah_gem(self, user_hash: str, team_hash: str) -> List[Event]:
        """Steady, high-impact, enables others"""
        events = []
        base = datetime.utcnow() - timedelta(days=30)

        for day in range(30):
            current = base + timedelta(days=day)

            # Regular hours
            hour = int(self.rng.normal(13, 1))

            # Few commits, but lots of "unblocking" events
            events.append(
                Event(
                    user_hash=user_hash,
                    timestamp=current.replace(hour=hour),
                    event_type="commit",
                    metadata_={"after_hours": False, "context_switches": 1},
                )
            )

            # Helping others (creates graph edges)
            if self.rng.random() > 0.3:
                events.append(
                    Event(
                        user_hash=user_hash,
                        target_user_hash=f"teammate_{self.rng.integers(1, 5)}",
                        timestamp=current.replace(hour=min(hour + 2, 23)),
                        event_type="pr_review",
                        metadata_={
                            "after_hours": False,
                            "comment_length": int(self.rng.normal(300, 50)),
                            "unblocked": True,
                        },
                    )
                )

        return events

    def _jordan_steady(self, user_hash: str, team_hash: str) -> List[Event]:
        """Control group - steady behavior"""
        events = []
        base = datetime.utcnow() - timedelta(days=30)
        for day in range(30):
            current = base + timedelta(days=day)
            hour = int(self.rng.normal(11, 1))
            events.append(
                Event(
                    user_hash=user_hash,
                    timestamp=current.replace(hour=hour),
                    event_type="commit",
                    metadata_={"after_hours": False, "context_switches": 2},
                )
            )
        return events

    def _maria_contagion(self, user_hash: str, team_hash: str) -> List[Event]:
        """Maria contagion pattern: declining mood affecting team dynamics"""
        events = []
        base = datetime.utcnow() - timedelta(days=30)

        for day in range(30):
            current = base + timedelta(days=day)

            if day < 14:
                # Normal first 2 weeks
                hour = int(self.rng.normal(14, 1))
                sentiment = "neutral"
                is_negative = False
            elif day < 21:
                # Week 3: Declining pattern starts
                hour = 20 + int((day - 14) * 0.5)
                sentiment = "neutral"
                is_negative = False
            else:
                # Week 4+: Negative sentiment, mentions resignation
                hour = 21 + int(self.rng.exponential(2))
                sentiment = "negative"
                is_negative = True

            # Multiple Slack messages per day (more active when negative)
            message_count = 5 if is_negative else 2
            for _ in range(message_count):
                events.append(
                    Event(
                        user_hash=user_hash,
                        timestamp=current.replace(
                            hour=min(hour, 23), minute=self.rng.integers(0, 60)
                        ),
                        event_type="slack_message",
                        metadata_={
                            "after_hours": hour > 19,
                            "sentiment": sentiment,
                            "is_negative": is_negative,
                            "mentions_resignation": day > 24,
                            "is_reply": self.rng.random() > 0.5,
                            "persona": "maria_contagion",
                        },
                    )
                )

        return events

    def _create_team_network(self, team_hashes: List[str]):
        """Generate graph edges for Culture Thermometer"""
        edges = []
        for i, source in enumerate(team_hashes):
            for target in team_hashes[i + 1 :]:
                if self.rng.random() > 0.3:  # 70% connection probability
                    edges.append(
                        GraphEdge(
                            source_hash=source,
                            target_hash=target,
                            weight=float(self.rng.exponential(5)),
                            last_interaction=datetime.utcnow()
                            - timedelta(days=int(self.rng.exponential(3))),
                            edge_type="collaboration",
                        )
                    )
        return edges
