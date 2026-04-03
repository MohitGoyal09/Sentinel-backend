"""Migration 0001: Migrate UserIdentity.manager_hash to Team table.

For each unique (tenant_id, manager_hash) in UserIdentity:
  1. Get or create a Team row
  2. Set TenantMember.team_id for matching employees

Idempotent -- safe to run multiple times.
"""
import uuid

from sqlalchemy.orm import Session

from app.models.identity import UserIdentity
from app.models.team import Team
from app.models.tenant import TenantMember


def _get_or_create_team(
    session: Session, tenant_id: uuid.UUID, manager_hash: str
) -> Team:
    """Return existing Team for (tenant_id, manager_hash), or create one."""
    existing = (
        session.query(Team)
        .filter_by(tenant_id=tenant_id, manager_hash=manager_hash)
        .first()
    )
    if existing is not None:
        return existing

    team = Team(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=f"Team ({manager_hash[:8]})",
        manager_hash=manager_hash,
    )
    session.add(team)
    session.flush()
    return team


def run_migration(session: Session) -> None:
    """Migrate manager_hash data into Team rows and link TenantMembers."""
    query = (
        session.query(UserIdentity)
        .filter(
            UserIdentity.manager_hash.isnot(None),
            UserIdentity.tenant_id.isnot(None),
        )
    )

    for emp in query.yield_per(500):
        team = _get_or_create_team(session, emp.tenant_id, emp.manager_hash)

        member = (
            session.query(TenantMember)
            .filter_by(tenant_id=emp.tenant_id, user_hash=emp.user_hash)
            .first()
        )
        if member is not None and member.team_id is None:
            member.team_id = team.id

    session.flush()
