#!/usr/bin/env python3
"""
Script to seed Supabase with simulated data for testing.

Usage:
    cd backend
    python scripts/seed_supabase.py

This creates:
    - Sample user identities (encrypted)
    - Simulated events (commits, PR reviews, slack messages)
    - Risk scores
    - Graph edges for network analysis
    - Centrality scores
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Dict
import json

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from app.config import get_settings
from app.core.security import privacy
import numpy as np

settings = get_settings()

# Persona definitions
PERSONAS = {
    "alex_burnout": {
        "email": "alex.chen@company.com",
        "slack_id": "U123ALEX",
        "type": "burnout",
        "description": "Escalating after-hours work, heading toward burnout"
    },
    "sarah_gem": {
        "email": "sarah.jones@company.com",
        "slack_id": "U456SARAH",
        "type": "high_performer",
        "description": "Steady, high-impact, enables others"
    },
    "jordan_steady": {
        "email": "jordan.smith@company.com",
        "slack_id": "U789JORDAN",
        "type": "steady",
        "description": "Control group - steady behavior"
    },
    "maria_contagion": {
        "email": "maria.garcia@company.com",
        "slack_id": "U321MARIA",
        "type": "contagion",
        "description": "Negative influence on team culture"
    },
    "david_new": {
        "email": "david.lee@company.com",
        "slack_id": "U654DAVID",
        "type": "new_hire",
        "description": "Recent joiner, learning the ropes"
    }
}


def generate_user_hash(email: str) -> str:
    """Generate consistent hash for user using privacy engine."""
    return privacy.hash_identity(email)


def seed_identities(conn) -> Dict[str, str]:
    """Create user identities in Supabase."""
    print("[INFO] Seeding user identities...")
    
    user_hashes = {}
    
    for persona_id, persona_data in PERSONAS.items():
        email = persona_data["email"]
        slack_id = persona_data["slack_id"]
        user_hash = generate_user_hash(email)
        user_hashes[persona_id] = user_hash
        
        # Encrypt PII
        email_encrypted = privacy.encrypt(email)
        slack_encrypted = privacy.encrypt(slack_id) if slack_id else None
        
        # Insert into identity.users table
        try:
            sql = text("""
                INSERT INTO identity.users (user_hash, email_encrypted, slack_id_encrypted, created_at)
                VALUES (:user_hash, :email_encrypted, :slack_id_encrypted, :created_at)
                ON CONFLICT (user_hash) DO NOTHING
            """)
            conn.execute(sql, {
                "user_hash": user_hash,
                "email_encrypted": email_encrypted,
                "slack_id_encrypted": slack_encrypted,
                "created_at": datetime.now(timezone.utc)
            })
            print(f"  [OK] Created identity for {persona_id} ({user_hash})")
        except Exception as e:
            print(f"  [Error] Error creating identity for {persona_id}: {e}")
    
    return user_hashes


def generate_events_alex_burnout(user_hash: str, rng: np.random.Generator) -> List[Dict]:
    """Generate events for Alex (burnout trajectory)."""
    events = []
    base = datetime.now(timezone.utc) - timedelta(days=30)
    
    for day in range(30):
        current = base + timedelta(days=day)
        
        if day < 7:  # Week 1: Normal
            hour = int(rng.normal(14, 1))
            late = False
            switches = 2
        elif day < 21:  # Week 2-3: Drift
            hour = 18 + int((day - 7) * 0.5)
            late = hour > 20
            switches = 4
        else:  # Week 4: Crash
            hour = 22 + int(rng.exponential(3))
            late = True
            switches = 8
        
        for _ in range(rng.integers(3, 6)):
            events.append({
                "user_hash": user_hash,
                "timestamp": current.replace(hour=min(hour, 23), minute=int(rng.integers(0, 60))),
                "event_type": rng.choice(["commit", "slack_message"]),
                "target_user_hash": None,
                "metadata": json.dumps({
                    "after_hours": late,
                    "context_switches": switches,
                    "is_reply": rng.random() > 0.3
                })
            })
    
    return events


def generate_events_sarah_gem(user_hash: str, rng: np.random.Generator, teammate_hashes: List[str]) -> List[Dict]:
    """Generate events for Sarah (high performer)."""
    events = []
    base = datetime.now(timezone.utc) - timedelta(days=30)
    
    for day in range(30):
        current = base + timedelta(days=day)
        hour = int(rng.normal(13, 1))
        
        # Regular commits
        events.append({
            "user_hash": user_hash,
            "timestamp": current.replace(hour=hour),
            "event_type": "commit",
            "target_user_hash": None,
            "metadata": json.dumps({"after_hours": False, "context_switches": 1})
        })
        
        # Helping others (PR reviews)
        if rng.random() > 0.3 and teammate_hashes:
            events.append({
                "user_hash": user_hash,
                "timestamp": current.replace(hour=min(hour + 2, 23)),
                "event_type": "pr_review",
                "target_user_hash": rng.choice(teammate_hashes),
                "metadata": json.dumps({
                    "after_hours": False,
                    "comment_length": int(rng.normal(300, 50)),
                    "unblocked": True
                })
            })
    
    return events


def generate_events_jordan_steady(user_hash: str, rng: np.random.Generator) -> List[Dict]:
    """Generate events for Jordan (steady)."""
    events = []
    base = datetime.now(timezone.utc) - timedelta(days=30)
    
    for day in range(30):
        current = base + timedelta(days=day)
        hour = int(rng.normal(11, 1))
        events.append({
            "user_hash": user_hash,
            "timestamp": current.replace(hour=hour),
            "event_type": "commit",
            "target_user_hash": None,
            "metadata": json.dumps({"after_hours": False, "context_switches": 2})
        })
    
    return events


def generate_events_maria_contagion(user_hash: str, rng: np.random.Generator, teammate_hashes: List[str]) -> List[Dict]:
    """Generate events for Maria (negative contagion)."""
    events = []
    base = datetime.now(timezone.utc) - timedelta(days=30)
    
    for day in range(30):
        current = base + timedelta(days=day)
        
        # Complaining in Slack
        if rng.random() > 0.4:
            events.append({
                "user_hash": user_hash,
                "timestamp": current.replace(hour=int(rng.normal(15, 2))),
                "event_type": "slack_message",
                "target_user_hash": rng.choice(teammate_hashes) if teammate_hashes and rng.random() > 0.5 else None,
                "metadata": json.dumps({
                    "after_hours": False,
                    "sentiment": "negative",
                    "topic": rng.choice(["deadline", "workload", "management"])
                })
            })
    
    return events


def generate_events_david_new(user_hash: str, rng: np.random.Generator, teammate_hashes: List[str]) -> List[Dict]:
    """Generate events for David (new hire)."""
    events = []
    base = datetime.now(timezone.utc) - timedelta(days=14)  # Only 2 weeks of data
    
    for day in range(14):
        current = base + timedelta(days=day)
        hour = int(rng.normal(10, 1))
        
        # Asking for help
        if rng.random() > 0.5 and teammate_hashes:
            events.append({
                "user_hash": user_hash,
                "timestamp": current.replace(hour=hour),
                "event_type": "slack_message",
                "target_user_hash": rng.choice(teammate_hashes),
                "metadata": json.dumps({
                    "after_hours": False,
                    "is_question": True,
                    "topic": rng.choice(["setup", "code_review", "process"])
                })
            })
        
        # Learning commits
        events.append({
            "user_hash": user_hash,
            "timestamp": current.replace(hour=hour + 2),
            "event_type": "commit",
            "target_user_hash": None,
            "metadata": json.dumps({"after_hours": False, "context_switches": 3, "is_learning": True})
        })
    
    return events


def seed_events(conn, user_hashes: Dict[str, str]) -> int:
    """Generate and insert simulated events."""
    print("\n[INFO] Seeding events...")
    
    rng = np.random.default_rng(42)
    all_events = []
    
    teammate_hashes = list(user_hashes.values())
    
    # Generate events for each persona
    all_events.extend(generate_events_alex_burnout(user_hashes["alex_burnout"], rng))
    all_events.extend(generate_events_sarah_gem(user_hashes["sarah_gem"], rng, teammate_hashes))
    all_events.extend(generate_events_jordan_steady(user_hashes["jordan_steady"], rng))
    all_events.extend(generate_events_maria_contagion(user_hashes["maria_contagion"], rng, teammate_hashes))
    all_events.extend(generate_events_david_new(user_hashes["david_new"], rng, teammate_hashes))
    
    # Insert in batches
    batch_size = 100
    inserted = 0
    
    for i in range(0, len(all_events), batch_size):
        batch = all_events[i:i + batch_size]
        try:
            sql = text("""
                INSERT INTO analytics.events (user_hash, timestamp, event_type, target_user_hash, metadata)
                VALUES (:user_hash, :timestamp, :event_type, :target_user_hash, :metadata)
            """)
            conn.execute(sql, batch)
            inserted += len(batch)
            print(f"  [OK] Inserted batch {i//batch_size + 1}/{(len(all_events) + batch_size - 1)//batch_size}")
        except Exception as e:
            print(f"  [Error] Error inserting batch: {e}")
    
    print(f"  Total events inserted: {inserted}")
    return inserted


def seed_risk_scores(conn, user_hashes: Dict[str, str]):
    """Generate and insert risk scores."""
    print("\n[INFO] Seeding risk scores...")
    
    risk_data = [
        {
            "user_hash": user_hashes["alex_burnout"],
            "velocity": 85.5,
            "risk_level": "CRITICAL",
            "confidence": 0.92,
            "thwarted_belongingness": 0.75,
            "updated_at": datetime.now(timezone.utc)
        },
        {
            "user_hash": user_hashes["sarah_gem"],
            "velocity": 45.2,
            "risk_level": "LOW",
            "confidence": 0.88,
            "thwarted_belongingness": 0.15,
            "updated_at": datetime.now(timezone.utc)
        },
        {
            "user_hash": user_hashes["jordan_steady"],
            "velocity": 52.0,
            "risk_level": "LOW",
            "confidence": 0.85,
            "thwarted_belongingness": 0.25,
            "updated_at": datetime.now(timezone.utc)
        },
        {
            "user_hash": user_hashes["maria_contagion"],
            "velocity": 38.5,
            "risk_level": "ELEVATED",
            "confidence": 0.78,
            "thwarted_belongingness": 0.65,
            "updated_at": datetime.now(timezone.utc)
        },
        {
            "user_hash": user_hashes["david_new"],
            "velocity": 62.0,
            "risk_level": "ELEVATED",
            "confidence": 0.72,
            "thwarted_belongingness": 0.45,
            "updated_at": datetime.now(timezone.utc)
        }
    ]
    
    for data in risk_data:
        try:
            sql = text("""
                INSERT INTO analytics.risk_scores (user_hash, velocity, risk_level, confidence, thwarted_belongingness, updated_at)
                VALUES (:user_hash, :velocity, :risk_level, :confidence, :thwarted_belongingness, :updated_at)
                ON CONFLICT (user_hash) DO UPDATE SET
                    velocity = EXCLUDED.velocity,
                    risk_level = EXCLUDED.risk_level,
                    confidence = EXCLUDED.confidence,
                    thwarted_belongingness = EXCLUDED.thwarted_belongingness,
                    updated_at = EXCLUDED.updated_at
            """)
            conn.execute(sql, data)
            persona = [k for k, v in user_hashes.items() if v == data["user_hash"]][0]
            print(f"  [OK] Risk score for {persona}: {data['risk_level']}")
        except Exception as e:
            print(f"  [Error] Error inserting risk score: {e}")


def seed_graph_edges(conn, user_hashes: Dict[str, str]):
    """Generate and insert graph edges."""
    print("\n[INFO] Seeding graph edges...")
    
    rng = np.random.default_rng(42)
    edges = []
    hash_list = list(user_hashes.values())
    
    # Create connections between team members
    for i, source in enumerate(hash_list):
        for target in hash_list[i+1:]:
            if rng.random() > 0.3:  # 70% connection probability
                edges.append({
                    "source_hash": source,
                    "target_hash": target,
                    "weight": float(rng.exponential(5)),
                    "last_interaction": (datetime.now(timezone.utc) - timedelta(days=int(rng.exponential(3)))),
                    "edge_type": rng.choice(["collaboration", "mentorship", "blocking"])
                })
    
    # Insert edges
    for edge in edges:
        try:
            sql = text("""
                INSERT INTO analytics.graph_edges (source_hash, target_hash, weight, last_interaction, edge_type)
                VALUES (:source_hash, :target_hash, :weight, :last_interaction, :edge_type)
            """)
            conn.execute(sql, edge)
            print(f"  [OK] Edge: {edge['source_hash'][:8]}... -> {edge['target_hash'][:8]}... ({edge['edge_type']})")
        except Exception as e:
            print(f"  [Error] Error inserting edge: {e}")


def seed_centrality_scores(conn, user_hashes: Dict[str, str]):
    """Generate and insert centrality scores."""
    print("\n[INFO] Seeding centrality scores...")
    
    scores = [
        {"user_hash": user_hashes["sarah_gem"], "betweenness": 0.85, "eigenvector": 0.92},
        {"user_hash": user_hashes["jordan_steady"], "betweenness": 0.65, "eigenvector": 0.75},
        {"user_hash": user_hashes["alex_burnout"], "betweenness": 0.45, "eigenvector": 0.60},
        {"user_hash": user_hashes["maria_contagion"], "betweenness": 0.55, "eigenvector": 0.70},
        {"user_hash": user_hashes["david_new"], "betweenness": 0.25, "eigenvector": 0.35}
    ]
    
    for score in scores:
        try:
            sql = text("""
                INSERT INTO analytics.centrality_scores (user_hash, betweenness, eigenvector)
                VALUES (:user_hash, :betweenness, :eigenvector)
                ON CONFLICT (user_hash) DO UPDATE SET
                    betweenness = EXCLUDED.betweenness,
                    eigenvector = EXCLUDED.eigenvector
            """)
            conn.execute(sql, score)
            persona = [k for k, v in user_hashes.items() if v == score["user_hash"]][0]
            print(f"  [OK] Centrality for {persona}: betweenness={score['betweenness']:.2f}")
        except Exception as e:
            print(f"  [Error] Error inserting centrality: {e}")


def main():
    """Main seeding function."""
    print("=" * 60)
    print("[SEED] Supabase Data Seeding Script (Direct SQL)")
    print("=" * 60)
    
    # Check environment
    if not settings.database_url:
        print("\n[Error] Error: DATABASE_URL must be set in .env")
        sys.exit(1)
    
    try:
        # Hide credentials for printing
        db_host = settings.database_url.split("@")[-1]
        print(f"\n[OK] Connecting to Database: ...{db_host}")
        
        engine = create_engine(settings.database_url)
        with engine.begin() as conn:  # Transactional block
            
            # Seed data
            user_hashes = seed_identities(conn)
            seed_events(conn, user_hashes)
            seed_risk_scores(conn, user_hashes)
            seed_graph_edges(conn, user_hashes)
            seed_centrality_scores(conn, user_hashes)
        
        print("\n" + "=" * 60)
        print("[OK] Seeding completed successfully!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n[Error] Error during seeding: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
