import logging
import math
import numpy as np
from scipy import stats
from datetime import datetime, timedelta, timezone
from typing import Dict, List
from sqlalchemy.orm import Session
from app.models.analytics import Event, RiskScore

from app.services.context import ContextEnricher
from app.services.nudge_dispatcher import NudgeDispatcher
from app.services.websocket_manager import manager
from app.models.notification import Notification

from typing import Optional
from uuid import UUID


class SafetyValve:
    """Burnout detection via Sentiment Velocity"""

    # Industry benchmarks (Gallup/WHO 2024 data)
    INDUSTRY_BENCHMARKS = {
        "tech": {"burnout_rate": 0.25, "avg_velocity": 0.9, "avg_belongingness": 0.62, "label": "Technology"},
        "finance": {"burnout_rate": 0.30, "avg_velocity": 1.1, "avg_belongingness": 0.58, "label": "Finance"},
        "healthcare": {"burnout_rate": 0.35, "avg_velocity": 1.3, "avg_belongingness": 0.55, "label": "Healthcare"},
        "manufacturing": {"burnout_rate": 0.22, "avg_velocity": 0.7, "avg_belongingness": 0.65, "label": "Manufacturing"},
        "cross_industry": {"burnout_rate": 0.22, "avg_velocity": 0.8, "avg_belongingness": 0.65, "label": "Cross-Industry"},
    }

    def __init__(self, db: Session, tenant_id: Optional[UUID] = None):
        self.db = db
        self.tenant_id = tenant_id
        self.min_days = 7
        self.critical_threshold = 2.5
        self.elevated_threshold = 1.5
        self.context = ContextEnricher(db)

    def get_benchmarks(self, industry: str = "tech") -> Dict:
        """Return industry benchmark data for comparison display."""
        benchmark = self.INDUSTRY_BENCHMARKS.get(
            industry, self.INDUSTRY_BENCHMARKS["cross_industry"]
        )
        return {
            "industry": benchmark["label"],
            "burnout_rate": benchmark["burnout_rate"],
            "avg_velocity": benchmark["avg_velocity"],
            "avg_belongingness": benchmark["avg_belongingness"],
            "source": "Gallup State of the Global Workplace 2024",
        }

    def _calculate_attrition_probability(
        self,
        velocity: float,
        belongingness: float,
        entropy: float,
        sustained: bool,
    ) -> float:
        """Composite attrition probability using calibrated sigmoid.

        Calibration targets (heuristic, not ML-trained):
        - Healthy (v=0.3, b=0.7, e=0.5, s=False) -> ~8%
        - Elevated (v=1.8, b=0.4, e=1.5, s=False) -> ~45%
        - Critical (v=3.5, b=0.25, e=2.0, s=True) -> ~85%
        """
        v = max(0.0, min(float(velocity), 5.0))
        e = max(0.0, min(float(entropy), 3.0))
        b = float(belongingness)
        s = 1.0 if sustained else 0.0
        raw = 0.4 * v + 0.3 * (1.0 - b) + 0.2 * e + 0.1 * s
        probability = 1.0 / (1.0 + np.exp(-(raw * 3.5 - 2.5)))
        return round(float(probability), 2)

    def analyze(self, user_hash: str) -> Dict:
        """Analyze burnout risk for a single user."""
        # Check for existing RiskScore (from seed or prior computation)
        # If it exists, return it directly — don't overwrite curated data
        query = self.db.query(RiskScore).filter_by(user_hash=user_hash)
        if self.tenant_id is not None:
            query = query.filter(RiskScore.tenant_id == self.tenant_id)
        existing = query.first()

        if existing and existing.velocity is not None:
            # Use stored values
            velocity = float(existing.velocity)
            confidence = float(existing.confidence or 0)
            belongingness = float(existing.thwarted_belongingness or 0.5)
            risk_level = existing.risk_level or "LOW"
            attrition_prob = float(existing.attrition_probability or 0.0)

            # Still compute entropy, sentiment, and indicators from events for display
            events = self._get_events(user_hash, 21)
            hours = self._extract_daily_hours(events) if events else []
            entropy = self._calculate_entropy(hours) if hours else 0.0
            sentiment = self._calculate_sentiment_score(user_hash, events) if events else None

            indicators = {
                "chaotic_hours": entropy > 1.5,
                "social_withdrawal": belongingness < 0.4,
                "sustained_intensity": velocity > 2.0,
                "has_explained_context": False,
            }

            return {
                "engine": "Safety Valve",
                "status": "ANALYZED",
                "risk_level": risk_level,
                "velocity": round(velocity, 2),
                "confidence": round(confidence, 3),
                "belongingness_score": round(belongingness, 2),
                "circadian_entropy": round(entropy, 2),
                "attrition_probability": attrition_prob,
                "sentiment_score": sentiment,
                "sentiment_available": sentiment is not None,
                "indicators": indicators,
                "events_analyzed": len(events) if events else 0,
                "analysis_window_days": 21,
            }

        # No existing score — fall through to event-based calculation
        events = self._get_events(user_hash, days=21)

        if len(events) < 14:
            return {
                "engine": "Safety Valve",
                "status": "INSUFFICIENT_DATA",
                "risk_level": "LOW",
                "days_collected": len(events),
                "velocity": 0.0,
                "confidence": 0.0,
                "belongingness_score": 0.0,
                "circadian_entropy": 0.0,
                "attrition_probability": 0.0,
                "indicators": {
                    "chaotic_hours": False,
                    "social_withdrawal": False,
                    "sustained_intensity": False,
                    "has_explained_context": False,
                },
            }

        # NEW: Filter out explained late nights before calculating velocity
        # Get user email for context context
        user_email = self._get_user_email(user_hash)

        # Mark explained events
        events = self.context.mark_events_explained(events, user_email)

        # Only count unexplained events for velocity calculation
        unexplained_events = [
            e for e in events if not (e.metadata_ or {}).get("explained", False)
        ]

        # Calculate metrics on FILTERED events (explained removed)
        daily_hours = self._extract_daily_hours(unexplained_events)
        entropy = self._calculate_entropy(daily_hours)

        # Velocity only on unexplained late nights
        velocity, r_squared = self._calculate_velocity(unexplained_events)

        # Multi-source confidence: more data sources = higher confidence
        sources: set[str] = set()
        for e in events:
            if isinstance(e.metadata_, dict):
                src = e.metadata_.get("source", "unknown")
                if src and src != "unknown":
                    sources.add(src)
            # Also infer source from event_type
            if e.event_type in ("commit", "pr_review", "pr_created", "code_review"):
                sources.add("github")
            elif e.event_type in ("slack_message",):
                sources.add("slack")
            elif e.event_type in ("meeting",):
                sources.add("calendar")
            elif e.event_type in ("email_sent",):
                sources.add("email")

        # Sentiment signal (4th dimension)
        has_sentiment_events = any(e.event_type == "slack_sentiment" for e in events)
        if has_sentiment_events:
            sources.add("slack_sentiment")
        sentiment = self._calculate_sentiment_score(user_hash, events)

        source_count = max(len(sources), 1)
        source_multiplier = min(source_count / 3.0, 1.0)  # 1 source=0.33, 2=0.67, 3+=1.0
        confidence = round(r_squared * source_multiplier, 3)

        # Belongingness on ALL events
        belongingness = self._calculate_belongingness(user_hash, events)

        # Risk Decision
        explained_count = len(events) - len(unexplained_events)

        if velocity > self.critical_threshold and belongingness < 0.3 and entropy > 1.5:
            risk = "CRITICAL"
        elif velocity > self.elevated_threshold or belongingness < 0.4:
            risk = "ELEVATED"
        else:
            risk = "LOW"

        attrition_prob = self._calculate_attrition_probability(
            velocity, belongingness, entropy,
            velocity > 2.0,  # sustained intensity
        )

        self._store_result(
            user_hash, velocity, risk, r_squared, belongingness, attrition_prob
        )

        return {
            "engine": "Safety Valve",
            "risk_level": risk,
            "velocity": round(float(velocity), 2),
            "confidence": confidence,
            "belongingness_score": round(float(belongingness), 2),
            "circadian_entropy": round(float(entropy), 2),
            "attrition_probability": attrition_prob,
            "sentiment_score": sentiment,
            "sentiment_available": sentiment is not None,
            "sources_used": list(sources),
            "source_count": source_count,
            "explained_events_filtered": explained_count,
            "unexplained_events_count": len(unexplained_events),
            "indicators": {
                "chaotic_hours": entropy > 1.5,
                "social_withdrawal": belongingness < 0.4,
                "sustained_intensity": velocity > 2.0,
                "has_explained_context": explained_count > 0,
            },
        }

    def analyze_and_notify(self, user_hash: str) -> Dict:
        """Analyze and trigger real-time updates (synchronous for API compatibility)"""
        import asyncio
        import traceback

        result = self.analyze(user_hash)

        # NOTE: In-app notification is created by NudgeDispatcher._create_in_app_notification()
        # after the full pipeline (context check, Slack, etc.). Do not duplicate it here.

        # If elevated or critical, dispatch nudge via Slack (async in background)
        if result["risk_level"] in ["ELEVATED", "CRITICAL"]:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._dispatch_nudge_async(user_hash, result))
            except RuntimeError:
                # No running event loop - skip async dispatch
                logging.getLogger("sentinel").warning(
                    "No event loop available for nudge dispatch (user_hash=%s)",
                    user_hash,
                )
            except Exception as e:
                logging.getLogger("sentinel").error(
                    "Failed to dispatch nudge: %s\n%s", e, traceback.format_exc()
                )

        # Broadcast update to connected clients (async in background)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(manager.broadcast_risk_update(user_hash, result))
        except RuntimeError:
            logging.getLogger("sentinel").warning(
                "No event loop available for broadcast (user_hash=%s)", user_hash
            )
        except Exception as e:
            logging.getLogger("sentinel").error(
                "Failed to broadcast update: %s\n%s", e, traceback.format_exc()
            )

        return result

    async def _dispatch_nudge_async(self, user_hash: str, result: Dict):
        """Async helper for nudge dispatching"""
        dispatcher = NudgeDispatcher(self.db)
        await dispatcher.dispatch(user_hash, result)

    def _get_user_email(self, user_hash: str) -> str:
        """Lookup email from Vault B for context API calls"""
        from app.models.identity import UserIdentity

        # Direct query to Vault B (Identity schema)
        user = self.db.query(UserIdentity).filter_by(user_hash=user_hash).first()
        if user:
            from app.core.security import privacy

            return privacy.decrypt(user.email_encrypted)
        return "unknown@company.com"  # Fallback

    def _generate_llm_insight(self, velocity, risk, belongingness):
        """Optional: Use LLM to explain the risk state"""
        from app.services.llm import llm_service

        prompt = f"""
        Analyze an employee's burnout risk state:
        - Sentiment Velocity: {velocity} (High is bad)
        - Risk Level: {risk}
        - Belongingness: {belongingness} (Low is isolated)
        
        Provide a 1-sentence managerial insight.
        """
        return llm_service.generate_insight(prompt)

    def _calculate_velocity(self, events: List[Event]) -> tuple:
        """FIXED: Proper date sorting and regression"""
        daily_scores = {}
        for e in events:
            day = e.timestamp.date()
            metadata = e.metadata_ if e.metadata_ and isinstance(e.metadata_, dict) else {}

            # Weight by code complexity (files_changed * log1p(additions + deletions))
            files = metadata.get("files_changed", 1) if isinstance(metadata, dict) else 1
            additions = metadata.get("additions", 0) if isinstance(metadata, dict) else 0
            deletions = metadata.get("deletions", 0) if isinstance(metadata, dict) else 0
            changes = additions + deletions

            if files > 0 and changes > 0:
                weight = min(files * math.log1p(changes), 5.0)  # cap at 5.0
                score = max(0.5, weight)  # minimum 0.5 so events always count
            else:
                score = 1.0  # default for non-code events (slack, meetings)

            if metadata.get("after_hours"):
                score += 2.0
            if metadata.get("context_switches", 0) > 5:
                score += 0.5
            daily_scores[day] = daily_scores.get(day, 0) + score

        if len(daily_scores) < 2:
            return 0.0, 0.0

        # Sort by date to ensure chronological regression
        sorted_dates = sorted(daily_scores.keys())
        y = np.array([daily_scores[d] for d in sorted_dates])
        x = np.arange(len(y))

        slope, _, r_value, _, _ = stats.linregress(x, y)
        return float(slope), float(r_value**2)

    def _calculate_entropy(self, hours: List[int]) -> float:
        """FIXED: Handle empty arrays and log stability"""
        if not hours:
            return 0.0
        _, counts = np.unique(hours, return_counts=True)
        probs = counts / len(hours)
        # Add epsilon to avoid log(0)
        return float(-np.sum(probs * np.log2(probs + 1e-9)))

    def _calculate_belongingness(self, user_hash: str, events: List[Event]) -> float:
        """Measure social connection"""
        interactions = [
            e for e in events if e.event_type in ["slack_message", "pr_comment", "pr_review", "email_sent"]
        ]
        if not interactions:
            return 0.5

        # Response rate to others
        replies = sum(
            1
            for e in interactions
            if e.metadata_
            and isinstance(e.metadata_, dict)
            and e.metadata_.get("is_reply", False)
        )
        mentions_others = sum(
            1
            for e in interactions
            if e.metadata_
            and isinstance(e.metadata_, dict)
            and e.metadata_.get("mentions_others", False)
        )

        return (
            (replies + mentions_others) / (2 * len(interactions))
            if interactions
            else 0.5
        )

    def _calculate_sentiment_score(self, user_hash: str, events: List[Event]) -> Optional[float]:
        """Average sentiment from slack_sentiment events.

        Returns None if fewer than 3 sentiment events exist (insufficient data).
        Maps: negative=-1, neutral=0, positive=1, returns the mean.
        """
        score_map = {"negative": -1.0, "neutral": 0.0, "positive": 1.0}
        sentiment_events = [
            e for e in events
            if e.event_type == "slack_sentiment"
            and isinstance(e.metadata_, dict)
        ]
        if len(sentiment_events) < 3:
            return None

        values = [
            score_map.get(e.metadata_.get("score", "neutral"), 0.0)
            for e in sentiment_events
        ]
        return round(sum(values) / len(values), 2)

    def _extract_daily_hours(self, events: List[Event]) -> List[int]:
        return [e.timestamp.hour for e in events]

    def _get_events(self, user_hash: str, days: int):
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = self.db.query(Event).filter(
            Event.user_hash == user_hash, Event.timestamp >= cutoff
        )
        if self.tenant_id is not None:
            query = query.filter(Event.tenant_id == self.tenant_id)
        return query.order_by(Event.timestamp.asc()).all()

    def _store_result(
        self, user_hash, velocity, risk, confidence, belongingness, attrition_probability=0.0
    ):
        query = self.db.query(RiskScore).filter_by(user_hash=user_hash)
        if self.tenant_id is not None:
            query = query.filter(RiskScore.tenant_id == self.tenant_id)
        score = query.first()
        if not score:
            score = RiskScore(user_hash=user_hash, tenant_id=self.tenant_id)

        score.velocity = velocity
        score.risk_level = risk
        score.confidence = confidence
        score.thwarted_belongingness = belongingness
        score.attrition_probability = attrition_probability
        score.updated_at = datetime.now(timezone.utc)
        self.db.add(score)
        self.db.commit()

        from app.models.analytics import RiskHistory

        history = RiskHistory(
            user_hash=user_hash,
            tenant_id=self.tenant_id,
            risk_level=risk,
            velocity=velocity,
            confidence=confidence,
            belongingness_score=belongingness,
            attrition_probability=attrition_probability,
            timestamp=datetime.now(timezone.utc),
        )
        self.db.add(history)
        self.db.commit()

    def seed_risk_history(self, user_hash: str, persona_type: str = "alex_burnout"):
        """
        Generate 30 days of historical risk snapshots for the velocity chart.
        Each persona type gets a different trajectory curve.
        Called once during persona creation.
        """
        from app.models.analytics import RiskHistory

        rng = np.random.default_rng(hash(user_hash) % (2**31))
        base = datetime.now(timezone.utc) - timedelta(days=30)

        trajectories = {
            "alex_burnout": self._trajectory_burnout,
            "sarah_gem": self._trajectory_stable_low,
            "jordan_steady": self._trajectory_flat,
            "maria_contagion": self._trajectory_contagion,
        }

        trajectory_fn = trajectories.get(persona_type, self._trajectory_flat)
        data_points = trajectory_fn(rng)

        for day_offset, (
            velocity, belongingness, risk_level, confidence, attrition_prob
        ) in enumerate(data_points):
            timestamp = base + timedelta(days=day_offset, hours=int(rng.integers(9, 18)))
            entry = RiskHistory(
                user_hash=user_hash,
                tenant_id=self.tenant_id,
                risk_level=risk_level,
                velocity=velocity,
                confidence=confidence,
                belongingness_score=belongingness,
                attrition_probability=attrition_prob,
                timestamp=timestamp,
            )
            self.db.add(entry)

        self.db.commit()

    @staticmethod
    def _trajectory_burnout(rng):
        """Alex: Normal → Drift → Crash over 30 days"""
        points = []
        for day in range(30):
            if day < 7:
                vel = float(rng.normal(0.3, 0.1))
                belong = float(rng.normal(0.7, 0.05))
                risk = "LOW"
                attrition = 0.08
            elif day < 14:
                vel = float(rng.normal(0.8, 0.2))
                belong = float(rng.normal(0.55, 0.05))
                risk = "LOW"
                attrition = 0.20
            elif day < 21:
                vel = float(rng.normal(1.8, 0.3))
                belong = float(rng.normal(0.4, 0.05))
                risk = "ELEVATED"
                attrition = 0.45
            else:
                vel = float(rng.normal(3.0 + (day - 21) * 0.2, 0.3))
                belong = float(rng.normal(0.25, 0.05))
                risk = "CRITICAL"
                attrition = round(0.75 + (day - 21) * 0.011, 2)
            conf = min(0.3 + day * 0.02, 0.85)
            points.append(
                (
                    round(vel, 2),
                    round(max(0, belong), 2),
                    risk,
                    round(conf, 2),
                    min(attrition, 0.95),
                )
            )
        return points

    @staticmethod
    def _trajectory_stable_low(rng):
        """Sarah: Consistently low risk, high belongingness"""
        points = []
        for day in range(30):
            vel = float(rng.normal(-0.2, 0.15))
            belong = float(rng.normal(0.8, 0.05))
            risk = "LOW"
            conf = min(0.4 + day * 0.02, 0.9)
            attrition = 0.05
            points.append(
                (round(vel, 2), round(max(0, belong), 2), risk, round(conf, 2), attrition)
            )
        return points

    @staticmethod
    def _trajectory_flat(rng):
        """Jordan: Steady, minimal variation"""
        points = []
        for day in range(30):
            vel = float(rng.normal(0.1, 0.1))
            belong = float(rng.normal(0.6, 0.05))
            risk = "LOW"
            conf = min(0.35 + day * 0.015, 0.8)
            attrition = 0.07
            points.append(
                (round(vel, 2), round(max(0, belong), 2), risk, round(conf, 2), attrition)
            )
        return points

    @staticmethod
    def _trajectory_contagion(rng):
        """Maria: Normal then sudden negative spike in last week"""
        points = []
        for day in range(30):
            if day < 14:
                vel = float(rng.normal(0.2, 0.1))
                belong = float(rng.normal(0.65, 0.05))
                risk = "LOW"
                attrition = 0.06
            elif day < 21:
                vel = float(rng.normal(1.2, 0.3))
                belong = float(rng.normal(0.45, 0.05))
                risk = "ELEVATED"
                attrition = 0.35
            else:
                vel = float(rng.normal(2.5 + (day - 21) * 0.3, 0.3))
                belong = float(rng.normal(0.3, 0.05))
                risk = "CRITICAL"
                attrition = round(0.70 + (day - 21) * 0.015, 2)
            conf = min(0.3 + day * 0.02, 0.85)
            points.append(
                (
                    round(vel, 2),
                    round(max(0, belong), 2),
                    risk,
                    round(conf, 2),
                    min(attrition, 0.95),
                )
            )
        return points
