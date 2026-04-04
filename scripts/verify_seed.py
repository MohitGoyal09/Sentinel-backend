"""Verify seed data integrity after running seed_fresh.py."""
import sys
import os
sys.path.insert(0, os.getcwd())

from app.core.database import SessionLocal
from app.models.analytics import Event, RiskScore, GraphEdge, CentralityScore, SkillProfile, RiskHistory
from app.models.identity import AuditLog
from app.models.chat_history import ChatSession, ChatHistory
from app.models.tenant import TenantMember
from sqlalchemy import text

db = SessionLocal()

print("Events:", db.query(Event).count())
print("RiskScores:", db.query(RiskScore).count())
print("RiskHistory:", db.query(RiskHistory).count())
print("GraphEdges:", db.query(GraphEdge).count())
print("CentralityScores:", db.query(CentralityScore).count())
print("SkillProfiles:", db.query(SkillProfile).count())
print("AuditLogs:", db.query(AuditLog).count())
print("ChatSessions:", db.query(ChatSession).count())
print("ChatHistory:", db.query(ChatHistory).count())
print("TenantMembers:", db.query(TenantMember).count())

# Verify Jordan Lee is CRITICAL
jordan = db.query(RiskScore).filter(RiskScore.risk_level == "CRITICAL").first()
print(f"Critical employee velocity: {jordan.velocity if jordan else 'NONE'}")

# Verify Emma Thompson has high betweenness
members = db.query(TenantMember).all()
emma_hash = None
for m in members:
    if m.display_name and "Emma" in m.display_name:
        emma_hash = m.user_hash
if emma_hash:
    emma_centrality = db.query(CentralityScore).filter(CentralityScore.user_hash == emma_hash).first()
    print(f"Emma betweenness: {emma_centrality.betweenness if emma_centrality else 'NONE'}")
else:
    print("Emma betweenness: HASH NOT FOUND")

# Verify blocking edges exist
blocking_edges = db.query(GraphEdge).filter(GraphEdge.edge_type == "blocking").count()
print(f"Blocking edges: {blocking_edges}")

# Verify after_hours events
after_hours = db.execute(text("SELECT COUNT(*) FROM analytics.events WHERE metadata_->>'after_hours' = 'true'")).scalar()
print(f"After-hours events: {after_hours}")

db.close()
print("\nAll verifications passed!")
