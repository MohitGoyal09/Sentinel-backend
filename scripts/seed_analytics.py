"""
Seed analytics database with demo data.
MUST run after seed_demo.py (which creates identity records).

Run:  python scripts/seed_analytics.py
"""

import sys
import os

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "backend"))  # noqa: E402

from scripts.seed_demo import seed_demo

if __name__ == "__main__":
    seed_demo()
