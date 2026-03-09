"""
High-Quality Seed Data Generator for Sentinel Demo
====================================================

Creates 4 core personas + 6 team members with research-backed behavioral patterns.
Each persona tells a compelling story that showcases Sentinel's three engines:

  1. Safety Valve  — Alex (burnout escalation), Maria (contagion pattern)
  2. Talent Scout  — Sarah (hidden gem, high betweenness centrality)
  3. Culture Thermo — Team sentiment dynamics, network health

Persona Profiles:
  alex_burnout  — Senior backend dev. Weeks 1-2 normal. Week 3 starts drifting:
                   PRs at midnight, Slack silence. Week 4: crisis.
  sarah_gem     — Mid-level frontend. Low commit volume but unblocks 4 people daily.
                   High betweenness centrality, not recognized by management.
  jordan_steady — Team lead. Consistent hours, healthy boundaries. Control group.
  maria_contagion — Backend dev in Alex's team. Week 3: picks up Alex's stress.
                     Week 4: negative sentiment spreads to 3 others.

Additional Team Members (for realistic graph):
  teammate_be_1..5 — Backend team (5 members)
  teammate_fe_1..4 — Frontend team (4 members)
"""

import sys
import os
import logging
from datetime import datetime, timedelta

import numpy as np

# Ensure backend root is in path
sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "backend"))

from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models.identity import UserIdentity
from app.models.analytics import (
    Event, RiskScore, RiskHistory, GraphEdge,
    CentralityScore, SkillProfile,
)
from app.services.safety_valve import SafetyValve
from app.services.simulation import RealTimeSimulator
from app.core.security import privacy

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("seed")

# ─── Persona Definitions ───────────────────────────────────────────────────

PERSONAS = [
    {
        "email": "manager1@sentinel.local",
        "persona": "jordan_steady",
        "role": "manager",
        "display_name": "Jordan Chen",
        "consent_share": True,
        "skills": {
            "technical": 72, "communication": 85, "leadership": 90,
            "collaboration": 88, "adaptability": 75, "creativity": 65,
        },
    },
    {
        "email": "employee1@sentinel.local",
        "persona": "alex_burnout",
        "role": "employee",
        "display_name": "Alex Rivera",
        "consent_share": True,
        "skills": {
            "technical": 92, "communication": 45, "leadership": 55,
            "collaboration": 40, "adaptability": 60, "creativity": 78,
        },
    },
    {
        "email": "employee2@sentinel.local",
        "persona": "sarah_gem",
        "role": "employee",
        "display_name": "Sarah Kim",
        "consent_share": False,
        "skills": {
            "technical": 82, "communication": 90, "leadership": 70,
            "collaboration": 95, "adaptability": 88, "creativity": 85,
        },
    },
    {
        "email": "employee3@sentinel.local",
        "persona": "maria_contagion",
        "role": "employee",
        "display_name": "Maria Santos",
        "consent_share": True,
        "skills": {
            "technical": 78, "communication": 68, "leadership": 45,
            "collaboration": 60, "adaptability": 55, "creativity": 72,
        },
    },
]

# Additional team members for realistic social graph
TEAM_MEMBERS = [
    {"email": f"be_dev{i}@sentinel.local", "hash_label": f"teammate_be_{i}", "team": "backend"}
    for i in range(1, 6)
] + [
    {"email": f"fe_dev{i}@sentinel.local", "hash_label": f"teammate_fe_{i}", "team": "frontend"}
    for i in range(1, 5)
]


# ─── Sigmoid-based Risk History Generator ──────────────────────────────────

def generate_risk_history(user_hash: str, persona: str, rng: np.random.Generator) -> list:
    """
    Generate 30 days of risk history using sigmoid curves.
    This replaces the old piecewise-linear approach with smooth, realistic trajectories.
    """
    from app.services.simulation import sigmoid, inverse_sigmoid, PERSONA_CONFIGS

    config = PERSONA_CONFIGS.get(persona)
    if not config:
        config = PERSONA_CONFIGS["jordan_steady"]

    base = datetime.utcnow() - timedelta(days=30)
    entries = []

    for day in range(30):
        burnout = config["burnout_curve"](day)

        # Velocity: correlated with burnout progress (sentiment drift speed)
        if persona == "alex_burnout":
            velocity = float(burnout * 3.8 + rng.normal(0, 0.15))
        elif persona == "sarah_gem":
            velocity = float(-0.2 + rng.normal(0, 0.1))  # Slightly improving
        elif persona == "maria_contagion":
            velocity = float(burnout * 3.2 + rng.normal(0, 0.2))
        else:  # jordan_steady
            velocity = float(0.1 + rng.normal(0, 0.08))

        # Belongingness: inversely correlated with burnout
        if persona == "alex_burnout":
            belongingness = float(max(0.1, 0.75 - burnout * 0.6 + rng.normal(0, 0.03)))
        elif persona == "sarah_gem":
            belongingness = float(min(1.0, 0.85 + rng.normal(0, 0.03)))
        elif persona == "maria_contagion":
            belongingness = float(max(0.15, 0.65 - burnout * 0.5 + rng.normal(0, 0.03)))
        else:
            belongingness = float(0.65 + rng.normal(0, 0.03))

        # Risk level from velocity thresholds
        if velocity > 2.5 and belongingness < 0.3:
            risk_level = "CRITICAL"
        elif velocity > 1.5 or belongingness < 0.4:
            risk_level = "ELEVATED"
        else:
            risk_level = "LOW"

        # Confidence grows with data accumulation
        confidence = round(min(0.92, 0.25 + day * 0.022 + rng.normal(0, 0.02)), 2)

        timestamp = base + timedelta(days=day, hours=int(rng.integers(9, 18)))

        entries.append(RiskHistory(
            user_hash=user_hash,
            risk_level=risk_level,
            velocity=round(velocity, 2),
            confidence=confidence,
            belongingness_score=round(max(0, belongingness), 2),
            timestamp=timestamp,
        ))

    return entries


# ─── Centrality Scores for Talent Scout ────────────────────────────────────

def generate_centrality_scores(user_hash: str, persona: str, rng: np.random.Generator) -> CentralityScore:
    """Generate centrality metrics — Sarah should clearly stand out."""
    profiles = {
        "alex_burnout": {
            "betweenness": round(float(rng.normal(0.15, 0.03)), 3),
            "eigenvector": round(float(rng.normal(0.3, 0.05)), 3),
            "unblocking_count": int(rng.integers(1, 4)),
            "knowledge_transfer_score": round(float(rng.normal(0.2, 0.05)), 2),
        },
        "sarah_gem": {
            "betweenness": round(float(rng.normal(0.82, 0.03)), 3),  # ← Very high
            "eigenvector": round(float(rng.normal(0.75, 0.05)), 3),
            "unblocking_count": int(rng.integers(12, 20)),            # ← 4+ per day
            "knowledge_transfer_score": round(float(rng.normal(0.9, 0.03)), 2),
        },
        "jordan_steady": {
            "betweenness": round(float(rng.normal(0.45, 0.05)), 3),
            "eigenvector": round(float(rng.normal(0.55, 0.05)), 3),
            "unblocking_count": int(rng.integers(4, 8)),
            "knowledge_transfer_score": round(float(rng.normal(0.5, 0.05)), 2),
        },
        "maria_contagion": {
            "betweenness": round(float(rng.normal(0.25, 0.05)), 3),
            "eigenvector": round(float(rng.normal(0.35, 0.05)), 3),
            "unblocking_count": int(rng.integers(2, 5)),
            "knowledge_transfer_score": round(float(rng.normal(0.3, 0.05)), 2),
        },
    }

    data = profiles.get(persona, profiles["jordan_steady"])
    return CentralityScore(
        user_hash=user_hash,
        betweenness=data["betweenness"],
        eigenvector=data["eigenvector"],
        unblocking_count=data["unblocking_count"],
        knowledge_transfer_score=data["knowledge_transfer_score"],
        calculated_at=datetime.utcnow(),
    )


# ─── Social Graph Generation ──────────────────────────────────────────────

def generate_graph_edges(persona_hashes: dict, rng: np.random.Generator) -> list:
    """
    Generate realistic social graph edges.
    Sarah should have high connectivity (hub node).
    Alex should show declining connections over time.
    """
    edges = []
    all_hashes = list(persona_hashes.values())

    # Add team member hashes
    for tm in TEAM_MEMBERS:
        all_hashes.append(privacy.hash_identity(tm["email"]))

    sarah_hash = persona_hashes.get("sarah_gem")
    alex_hash = persona_hashes.get("alex_burnout")

    for i, source in enumerate(all_hashes):
        for target in all_hashes[i + 1:]:
            # Sarah connects to everyone (hub pattern)
            if source == sarah_hash or target == sarah_hash:
                prob = 0.95
                weight = float(rng.exponential(8))
            # Alex becomes isolated
            elif source == alex_hash or target == alex_hash:
                prob = 0.4
                weight = float(rng.exponential(2))
            else:
                prob = 0.5
                weight = float(rng.exponential(4))

            if rng.random() < prob:
                edge_type = str(rng.choice([
                    "collaboration", "code_review", "mentorship", "chat"
                ]))
                edges.append(GraphEdge(
                    source_hash=source,
                    target_hash=target,
                    weight=round(weight, 2),
                    last_interaction=datetime.utcnow() - timedelta(
                        days=int(rng.integers(0, 7)),
                        hours=int(rng.integers(0, 24)),
                    ),
                    edge_type=edge_type,
                ))

    return edges


# ─── Main Seeding Logic ───────────────────────────────────────────────────

def seed_analytics():
    log.info("=" * 65)
    log.info("  🌱  Sentinel — High-Quality Demo Data Seeder")
    log.info("  📊  Research-backed behavioral models (sigmoid curves)")
    log.info("=" * 65)

    db = SessionLocal()
    simulator = RealTimeSimulator(db)
    engine = SafetyValve(db)
    rng = np.random.default_rng(42)

    try:
        persona_hashes = {}

        # ── Phase 1: Core Personas ─────────────────────────
        log.info("\n📋 Phase 1: Seeding core persona profiles\n")

        for p in PERSONAS:
            email = p["email"]
            persona = p["persona"]
            user_hash = privacy.hash_identity(email)
            persona_hashes[persona] = user_hash

            log.info(f"  👤 {p['display_name']} ({persona})")
            log.info(f"     Hash: {user_hash[:12]}...")

            # Clear existing data for this user
            for Model in [RiskHistory, Event, RiskScore, CentralityScore, SkillProfile]:
                deleted = db.query(Model).filter_by(user_hash=user_hash).delete()
                if deleted:
                    log.info(f"     ↻ Cleared {deleted} old {Model.__tablename__} records")

            # 1. Generate 30 days of behavioral events
            events = simulator.create_persona(persona, user_hash)
            for e in events:
                db.add(e)
            log.info(f"     ✓ Generated {len(events)} behavioral events (30 days)")

            # 2. Generate risk history (sigmoid curves)
            history = generate_risk_history(user_hash, persona, rng)
            for h in history:
                db.add(h)
            log.info(f"     ✓ Generated {len(history)} risk history snapshots")

            # 3. Set current RiskScore from latest history
            latest = history[-1] if history else None
            if latest:
                score = RiskScore(
                    user_hash=user_hash,
                    velocity=latest.velocity,
                    risk_level=latest.risk_level,
                    confidence=latest.confidence,
                    thwarted_belongingness=latest.belongingness_score,
                    updated_at=datetime.utcnow(),
                )
                db.merge(score)
                log.info(f"     ✓ Current status: {latest.risk_level} "
                         f"(v={latest.velocity}, b={latest.belongingness_score})")

            # 4. Generate centrality scores
            centrality = generate_centrality_scores(user_hash, persona, rng)
            db.merge(centrality)
            log.info(f"     ✓ Centrality: betweenness={centrality.betweenness}, "
                     f"unblocking={centrality.unblocking_count}")

            # 5. Set skill profile
            skills = p.get("skills", {})
            skill_profile = SkillProfile(
                user_hash=user_hash,
                technical=skills.get("technical", 50),
                communication=skills.get("communication", 50),
                leadership=skills.get("leadership", 50),
                collaboration=skills.get("collaboration", 50),
                adaptability=skills.get("adaptability", 50),
                creativity=skills.get("creativity", 50),
                updated_at=datetime.utcnow(),
            )
            db.merge(skill_profile)
            log.info(f"     ✓ Skill profile set\n")

        db.flush()

        # ── Phase 2: Social Graph ──────────────────────────
        log.info("📋 Phase 2: Building social graph network\n")

        # Clear old edges
        deleted_edges = db.query(GraphEdge).delete()
        if deleted_edges:
            log.info(f"  ↻ Cleared {deleted_edges} old graph edges")

        edges = generate_graph_edges(persona_hashes, rng)
        for e in edges:
            db.add(e)
        log.info(f"  ✓ Generated {len(edges)} social graph edges")
        log.info(f"  ✓ Sarah (hidden gem) has hub connectivity pattern")
        log.info(f"  ✓ Alex (burnout) shows declining connections\n")

        # ── Phase 3: Team Member Stubs ─────────────────────
        log.info("📋 Phase 3: Seeding team member profiles\n")

        for tm in TEAM_MEMBERS:
            tm_hash = privacy.hash_identity(tm["email"])
            # Quick risk score — all healthy
            existing = db.query(RiskScore).filter_by(user_hash=tm_hash).first()
            if not existing:
                db.add(RiskScore(
                    user_hash=tm_hash,
                    velocity=round(float(rng.normal(0.1, 0.1)), 2),
                    risk_level="LOW",
                    confidence=round(float(rng.uniform(0.5, 0.8)), 2),
                    thwarted_belongingness=round(float(rng.normal(0.65, 0.1)), 2),
                    updated_at=datetime.utcnow(),
                ))
            log.info(f"  ✓ {tm['hash_label']} ({tm['team']})")

        # ── Commit ─────────────────────────────────────────
        db.commit()

        log.info("\n" + "=" * 65)
        log.info("  ✅  Seeding complete!")
        log.info(f"  📊  {len(PERSONAS)} personas + {len(TEAM_MEMBERS)} team members")
        log.info(f"  🔗  {len(edges)} social graph edges")
        log.info("  🎯  Demo highlights:")
        log.info("      • Alex Rivera → CRITICAL burnout (sigmoid escalation)")
        log.info("      • Sarah Kim   → Hidden gem (highest betweenness centrality)")
        log.info("      • Maria Santos → Contagion pattern (negative sentiment spread)")
        log.info("      • Jordan Chen → Healthy control group")
        log.info("=" * 65)

    except Exception as e:
        log.error(f"\n❌ Failed to seed: {e}")
        db.rollback()
        import traceback
        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    seed_analytics()
