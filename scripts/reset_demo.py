"""
Reset demo data — drops and re-seeds everything.
Usage: cd backend && python -m scripts.reset_demo
"""

import sys
import os

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "backend"))

from app.core.database import engine, SessionLocal
from app.models.analytics import Base as AnalyticsBase
from app.models.identity import Base as IdentityBase
from app.models.notification import Base as NotificationBase

print("Resetting demo database...")

# Drop and recreate all tables
IdentityBase.metadata.drop_all(engine)
AnalyticsBase.metadata.drop_all(engine)
NotificationBase.metadata.drop_all(engine)

IdentityBase.metadata.create_all(engine)
AnalyticsBase.metadata.create_all(engine)
NotificationBase.metadata.create_all(engine)

print("Tables recreated. Running seed...")

from scripts.seed_demo import seed_demo

seed_demo()
