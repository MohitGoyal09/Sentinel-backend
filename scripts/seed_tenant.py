"""
Seed script: Creates test tenants and members for development.
Usage: python -m scripts.seed_tenant
"""

import sys
import os
import logging
from uuid import uuid4

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "backend"))

from app.core.database import SessionLocal
from app.core.security import privacy
from app.models.identity import UserIdentity, AuditLog
from app.models.tenant import Tenant, TenantMember

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("seed_tenant")

TENANTS = [
    {
        "name": "Acme Corp",
        "slug": "acme-corp",
        "plan": "enterprise",
        "owner": {
            "email": "admin@sentinel.local",
            "display_name": "Acme Admin",
            "role": "owner",
        },
        "members": [
            {"email": "alex.rivera@sentinel.local", "role": "member"},
            {"email": "sarah.kim@sentinel.local", "role": "member"},
            {"email": "maria.santos@sentinel.local", "role": "member"},
            {"email": "priya.sharma@sentinel.local", "role": "member"},
        ],
    },
    {
        "name": "Startup Inc",
        "slug": "startup-inc",
        "plan": "free",
        "owner": {
            "email": "manager1@sentinel.local",
            "display_name": "Startup Manager",
            "role": "owner",
        },
        "members": [],
    },
]


def seed_tenants():
    log.info("=" * 60)
    log.info("  SENTINEL — Tenant Seed Script")
    log.info("=" * 60)

    db = SessionLocal()

    try:
        for t in TENANTS:
            # Upsert tenant
            existing = db.query(Tenant).filter_by(slug=t["slug"]).first()
            if existing:
                log.info(f"  [EXISTS] Tenant '{t['name']}' ({t['slug']})")
                tenant = existing
            else:
                tenant = Tenant(
                    name=t["name"],
                    slug=t["slug"],
                    plan=t["plan"],
                    status="active",
                    settings={},
                )
                db.add(tenant)
                db.flush()
                log.info(
                    f"  [CREATE] Tenant '{t['name']}' ({t['slug']}) — plan={t['plan']}"
                )

            # Ensure owner identity exists
            owner = t["owner"]
            owner_hash = privacy.hash_identity(owner["email"])
            existing_user = (
                db.query(UserIdentity).filter_by(user_hash=owner_hash).first()
            )
            if not existing_user:
                db.add(
                    UserIdentity(
                        user_hash=owner_hash,
                        email_encrypted=privacy.encrypt(owner["email"]),
                        role="admin",
                        consent_share_anonymized=True,
                    )
                )
                db.add(
                    AuditLog(
                        user_hash=owner_hash,
                        action="user_created",
                        details={"role": "admin", "seed": "seed_tenant"},
                    )
                )
                log.info(f"    [CREATE] User {owner['email']}")

            # Upsert owner membership
            existing_member = (
                db.query(TenantMember)
                .filter_by(tenant_id=tenant.id, user_hash=owner_hash)
                .first()
            )
            if not existing_member:
                db.add(
                    TenantMember(
                        tenant_id=tenant.id,
                        user_hash=owner_hash,
                        role="owner",
                    )
                )
                log.info(f"    [MEMBER] {owner['email']} → owner")

            # Add employee members
            for m in t["members"]:
                m_hash = privacy.hash_identity(m["email"])
                m_user = db.query(UserIdentity).filter_by(user_hash=m_hash).first()
                if not m_user:
                    db.add(
                        UserIdentity(
                            user_hash=m_hash,
                            email_encrypted=privacy.encrypt(m["email"]),
                            role="employee",
                            consent_share_anonymized=True,
                        )
                    )
                    log.info(f"    [CREATE] User {m['email']}")

                existing_m = (
                    db.query(TenantMember)
                    .filter_by(tenant_id=tenant.id, user_hash=m_hash)
                    .first()
                )
                if not existing_m:
                    db.add(
                        TenantMember(
                            tenant_id=tenant.id,
                            user_hash=m_hash,
                            role=m["role"],
                        )
                    )
                    log.info(f"    [MEMBER] {m['email']} → {m['role']}")

        db.commit()
        log.info("\n" + "=" * 60)
        log.info("  TENANT SEED COMPLETE")
        log.info("=" * 60)

    except Exception as e:
        log.error(f"\n Seed failed: {e}")
        db.rollback()
        import traceback

        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    seed_tenants()
