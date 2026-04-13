"""
Layer 2 — Research-Backed Realistic Simulation Engine

Instead of random data, uses behavioral models grounded in organizational psychology:
- Burnout follows a sigmoid curve (Maslach Burnout Inventory theory)
- After-hours work correlates with reduced morning activity (circadian disruption)
- Slack response times increase under stress (cognitive load theory)
- Code review quality drops before burnout (ego depletion model)
- Social withdrawal precedes burnout escalation (IPT thwarted belongingness)

References:
  Maslach, C., & Leiter, M. P. (2016). Burnout. Stress: Concepts, Cognition, Emotion, and Behavior.
  Sonnentag, S. (2018). The recovery paradox. Journal of Organizational Behavior.
  Demerouti, E. et al. (2001). Job Demands-Resources model.
"""

import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
from app.models.analytics import Event, GraphEdge

# ─── Sigmoid & Behavioral Curves ───────────────────────────────────────────

def sigmoid(x: float, midpoint: float = 0.0, steepness: float = 1.0) -> float:
    """Logistic sigmoid: gradual onset, then rapid escalation, then plateau."""
    return 1.0 / (1.0 + np.exp(-steepness * (x - midpoint)))


def inverse_sigmoid(x: float, midpoint: float = 0.0, steepness: float = 1.0) -> float:
    """Declining sigmoid: starts high, gradually drops, then collapses."""
    return 1.0 - sigmoid(x, midpoint, steepness)


def circadian_disruption(day: int, burnout_progress: float, rng: np.random.Generator) -> Tuple[int, int]:
    """
    Model circadian rhythm disruption as burnout progresses.
    Returns (work_start_hour, work_end_hour).

    Research: Sonnentag (2018) — poor recovery leads to later mornings,
    later nights, compressing productive hours.
    """
    # Healthy: start ~9, end ~17
    # Burnout: start ~11, end ~23+
    base_start = 9 + int(burnout_progress * 3)   # 9 → 12
    base_end = 17 + int(burnout_progress * 6)     # 17 → 23

    start_hour = max(7, min(14, base_start + int(rng.normal(0, 0.5))))
    end_hour = max(16, min(23, base_end + int(rng.normal(0, 1))))

    return start_hour, end_hour


def stress_response_time(burnout_progress: float, rng: np.random.Generator) -> float:
    """
    Slack response time in minutes.
    Research: Cognitive load theory — under stress, response latency increases.
    Healthy: ~5 min avg, Burnout: ~45 min avg.
    """
    base = 5 + burnout_progress * 40  # 5 → 45 minutes
    return max(1, float(rng.normal(base, base * 0.2)))


def code_review_quality(burnout_progress: float, rng: np.random.Generator) -> Dict:
    """
    Code review depth drops as burnout progresses.
    Research: Ego depletion model — self-regulation resources are finite.
    Returns review metadata.
    """
    # Comment length: 200+ words healthy → 10 words burnt out
    comment_length = int(max(5, rng.normal(200 * (1 - burnout_progress * 0.85), 30)))

    # Review thoroughness: 1.0 = thorough, 0.1 = rubber-stamped
    thoroughness = max(0.1, float(inverse_sigmoid(burnout_progress, 0.5, 6)))

    # Approval-without-reading probability increases
    rubber_stamp = bool(burnout_progress > 0.7 and rng.random() < burnout_progress * 0.6)

    return {
        "comment_length": comment_length,
        "thoroughness": round(thoroughness, 2),
        "rubber_stamp": rubber_stamp,
        "lines_reviewed": int(max(5, rng.normal(150 * (1 - burnout_progress * 0.7), 20))),
    }


# ─── Persona Configurations ────────────────────────────────────────────────

PERSONA_CONFIGS = {
    "alex_burnout": {
        "description": "The Escalation Arc — Normal → Drift → Crash",
        "burnout_curve": lambda day: sigmoid(day, midpoint=18, steepness=0.25),
        "base_commits_per_day": (3, 5),       # Increases under pressure
        "base_messages_per_day": (4, 8),
        "base_reviews_per_day": (1, 2),
        "team": "backend",
        "work_style": "deep_focus",           # Fewer but longer sessions
        "weekend_work_probability_base": 0.05,
        "weekend_work_probability_peak": 0.85,
    },
    "sarah_gem": {
        "description": "The Hidden Gem — Steady contributor who unblocks everyone",
        "burnout_curve": lambda day: max(0.0, 0.05 + 0.01 * np.sin(day / 5)),  # Flat, healthy
        "base_commits_per_day": (2, 3),
        "base_messages_per_day": (6, 12),     # High communication
        "base_reviews_per_day": (3, 5),       # Reviews more than she commits
        "team": "frontend",
        "work_style": "collaborative",
        "weekend_work_probability_base": 0.02,
        "weekend_work_probability_peak": 0.02,
    },
    "jordan_steady": {
        "description": "The Control Group — Consistent, healthy boundaries",
        "burnout_curve": lambda day: 0.08 + 0.02 * np.sin(day / 7),  # Flat baseline
        "base_commits_per_day": (2, 4),
        "base_messages_per_day": (3, 6),
        "base_reviews_per_day": (1, 2),
        "team": "backend",
        "work_style": "balanced",
        "weekend_work_probability_base": 0.03,
        "weekend_work_probability_peak": 0.03,
    },
    "maria_contagion": {
        "description": "The Contagion Pattern — Declining sentiment spreads to team",
        "burnout_curve": lambda day: sigmoid(day, midpoint=20, steepness=0.3),
        "base_commits_per_day": (2, 3),
        "base_messages_per_day": (5, 10),     # High messaging
        "base_reviews_per_day": (1, 2),
        "team": "backend",
        "work_style": "reactive",
        "weekend_work_probability_base": 0.05,
        "weekend_work_probability_peak": 0.65,
    },
}


# ─── Teammate Hashes (for graph edges) ─────────────────────────────────────

TEAM_MEMBERS = {
    "backend": [f"teammate_be_{i}" for i in range(1, 6)],
    "frontend": [f"teammate_fe_{i}" for i in range(1, 5)],
    "devops": [f"teammate_ops_{i}" for i in range(1, 3)],
}


# ─── Main Simulator ────────────────────────────────────────────────────────

class RealTimeSimulator:
    """Generate behaviorally realistic data using research-backed models."""

    def __init__(self, db_session):
        self.db = db_session
        self.rng = np.random.default_rng(42)

    def create_persona(self, persona_type: str, user_hash: str, team_hash: str = None, tenant_id=None) -> List[Event]:
        """Generate 30 days of research-backed behavioral history."""
        if persona_type not in PERSONA_CONFIGS:
            import logging
            logging.getLogger("sentinel").warning(
                "Unknown persona %s, falling back to jordan_steady", persona_type
            )
            persona_type = "jordan_steady"

        config = PERSONA_CONFIGS[persona_type]
        rng = np.random.default_rng(hash(user_hash) % (2**31))
        events = []
        base = datetime.utcnow() - timedelta(days=30)

        for day in range(30):
            current_date = base + timedelta(days=day)
            burnout = config["burnout_curve"](day)  # 0.0 → 1.0

            is_weekend = current_date.weekday() >= 5
            weekend_prob = config["weekend_work_probability_base"] + \
                          (config["weekend_work_probability_peak"] - config["weekend_work_probability_base"]) * burnout

            if is_weekend and rng.random() > weekend_prob:
                continue  # Healthy people skip weekends

            # Circadian disruption model
            start_hour, end_hour = circadian_disruption(day, burnout, rng)
            is_after_hours = end_hour > 19

            # ── Generate commits ────────────────────────────────
            commit_range = config["base_commits_per_day"]
            # Under burnout, commit count may spike (overcompensation) then drop
            commit_multiplier = 1.0 + burnout * 0.5 if burnout < 0.7 else max(0.3, 1.0 - burnout)
            n_commits = max(1, int(rng.integers(*commit_range) * commit_multiplier))

            for i in range(n_commits):
                commit_hour = rng.integers(start_hour, min(end_hour + 1, 24))
                events.append(Event(
                    user_hash=user_hash,
                    tenant_id=tenant_id,
                    timestamp=current_date.replace(
                        hour=int(commit_hour),
                        minute=int(rng.integers(0, 60)),
                    ),
                    event_type="commit",
                    metadata_={
                        "after_hours": bool(commit_hour > 19),
                        "context_switches": int(rng.poisson(2 + burnout * 8)),
                        "lines_changed": int(rng.exponential(50 * (1 + burnout))),
                        "is_weekend": is_weekend,
                        "burnout_phase": _burnout_phase(burnout),
                    },
                ))

            # ── Generate Slack messages ────────────────────────
            msg_range = config["base_messages_per_day"]
            n_messages = rng.integers(*msg_range)

            # Sentiment model: declining sentiment correlates with burnout
            sentiment_score = max(-1.0, float(1.0 - burnout * 2.5 + rng.normal(0, 0.15)))
            sentiment = "positive" if sentiment_score > 0.3 else ("negative" if sentiment_score < -0.3 else "neutral")

            for i in range(n_messages):
                msg_hour = rng.integers(start_hour, min(end_hour + 1, 24))
                response_time = stress_response_time(burnout, rng)

                # Social withdrawal: reply probability drops with burnout
                reply_prob = max(0.1, 0.7 * (1 - burnout * 0.8))
                is_reply = bool(rng.random() < reply_prob)

                # Mentions resignation in final burnout phase
                mentions_resignation = bool(
                    persona_type == "maria_contagion" and
                    burnout > 0.8 and
                    rng.random() < 0.3
                )

                team = config.get("team", "backend")
                target = rng.choice(TEAM_MEMBERS.get(team, TEAM_MEMBERS["backend"])) if is_reply else None

                events.append(Event(
                    user_hash=user_hash,
                    target_user_hash=target,
                    tenant_id=tenant_id,
                    timestamp=current_date.replace(
                        hour=int(msg_hour),
                        minute=int(rng.integers(0, 60)),
                    ),
                    event_type="slack_message",
                    metadata_={
                        "after_hours": bool(msg_hour > 19),
                        "sentiment": sentiment,
                        "sentiment_score": round(sentiment_score, 2),
                        "is_reply": is_reply,
                        "is_negative": sentiment_score < -0.3,
                        "response_time_min": round(response_time, 1),
                        "mentions_resignation": mentions_resignation,
                        "channel": f"#{team}-team",
                        "message_length": int(rng.exponential(80 * (1 - burnout * 0.5))),
                    },
                ))

            # ── Generate PR reviews ────────────────────────────
            review_range = config["base_reviews_per_day"]
            n_reviews = rng.integers(*review_range)

            for i in range(n_reviews):
                review_hour = rng.integers(start_hour, min(end_hour + 1, 24))
                review_meta = code_review_quality(burnout, rng)

                team = config.get("team", "backend")
                target = rng.choice(TEAM_MEMBERS.get(team, TEAM_MEMBERS["backend"]))

                events.append(Event(
                    user_hash=user_hash,
                    target_user_hash=target,
                    tenant_id=tenant_id,
                    timestamp=current_date.replace(
                        hour=int(review_hour),
                        minute=int(rng.integers(0, 60)),
                    ),
                    event_type="pr_review",
                    metadata_={
                        "after_hours": bool(review_hour > 19),
                        **review_meta,
                        "unblocked": True if persona_type == "sarah_gem" else bool(rng.random() > 0.5),
                    },
                ))

            # ── Sarah-specific: Unblocking events ──────────────
            if persona_type == "sarah_gem" and rng.random() > 0.3:
                team = config.get("team", "frontend")
                unblock_targets = rng.choice(
                    TEAM_MEMBERS.get(team, TEAM_MEMBERS["frontend"]),
                    size=min(3, len(TEAM_MEMBERS.get(team, []))),
                    replace=False,
                )
                for target in unblock_targets:
                    events.append(Event(
                        user_hash=user_hash,
                        target_user_hash=str(target),
                        tenant_id=tenant_id,
                        timestamp=current_date.replace(
                            hour=int(rng.integers(10, 17)),
                            minute=int(rng.integers(0, 60)),
                        ),
                        event_type="unblocked",
                        metadata_={
                            "after_hours": False,
                            "time_to_unblock_min": int(rng.exponential(15)),
                            "knowledge_area": rng.choice([
                                "API design", "database", "auth", "frontend", "testing"
                            ]),
                        },
                    ))

        return events

    def generate_realtime_event(self, user_hash: str, current_risk: str) -> Dict:
        """Generate next event based on current trajectory (for live demo streaming)."""
        now = datetime.utcnow()

        # Map risk level to burnout progress
        burnout = {"LOW": 0.1, "ELEVATED": 0.5, "CRITICAL": 0.85}.get(current_risk, 0.1)

        start_hour, end_hour = circadian_disruption(25, burnout, self.rng)
        hour = int(self.rng.integers(start_hour, min(end_hour + 1, 24)))

        event_type = str(self.rng.choice(["commit", "slack_message", "pr_review"]))

        metadata: Dict = {
            "after_hours": hour > 19,
            "context_switches": int(self.rng.poisson(2 + burnout * 8)),
            "burnout_phase": _burnout_phase(burnout),
        }

        if event_type == "slack_message":
            metadata["response_time_min"] = round(stress_response_time(burnout, self.rng), 1)
            metadata["sentiment_score"] = round(max(-1.0, float(1.0 - burnout * 2.5 + self.rng.normal(0, 0.15))), 2)
        elif event_type == "pr_review":
            metadata.update(code_review_quality(burnout, self.rng))

        return {
            "user_hash": user_hash,
            "timestamp": now.replace(hour=min(hour, 23)),
            "event_type": event_type,
            "metadata_": metadata,
        }

    def create_team_network(self, team_hashes: List[str]) -> List[GraphEdge]:
        """Generate weighted social graph edges for Culture Thermometer."""
        edges = []
        for i, source in enumerate(team_hashes):
            for target in team_hashes[i + 1:]:
                if self.rng.random() > 0.25:  # 75% connection probability
                    weight = float(self.rng.exponential(5))
                    recency = int(self.rng.exponential(3))
                    edge_type = str(self.rng.choice([
                        "collaboration", "mentorship", "code_review", "chat"
                    ]))
                    edges.append(GraphEdge(
                        source_hash=source,
                        target_hash=target,
                        weight=weight,
                        last_interaction=datetime.utcnow() - timedelta(days=recency),
                        edge_type=edge_type,
                    ))
        return edges


def _burnout_phase(burnout: float) -> str:
    """Human-readable burnout phase label."""
    if burnout < 0.2:
        return "HEALTHY"
    elif burnout < 0.4:
        return "EARLY_WARNING"
    elif burnout < 0.6:
        return "DRIFT"
    elif burnout < 0.8:
        return "ESCALATION"
    else:
        return "CRISIS"
