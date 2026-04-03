"""Phase 1 schema foundation: teams, soft-delete, audit log overhaul

Revision ID: 004
Revises: 003
Create Date: 2026-04-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Create identity.teams table ────────────────────────────────
    op.create_table(
        "teams",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("manager_hash", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["identity.tenants.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_team_tenant_name"),
        schema="identity",
    )
    op.create_index(
        "ix_teams_tenant_id", "teams", ["tenant_id"], schema="identity"
    )
    op.create_index(
        "ix_teams_manager_hash", "teams", ["manager_hash"], schema="identity"
    )

    # ── 2. Add team_id column to identity.tenant_members ──────────────
    op.add_column(
        "tenant_members",
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        schema="identity",
    )
    op.create_foreign_key(
        "fk_tenant_members_team_id",
        "tenant_members",
        "teams",
        ["team_id"],
        ["id"],
        source_schema="identity",
        referent_schema="identity",
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_tenant_members_team_id",
        "tenant_members",
        ["team_id"],
        schema="identity",
    )

    # ── 3. Change tenant_members.role default from 'member' to 'employee'
    op.alter_column(
        "tenant_members",
        "role",
        server_default="employee",
        schema="identity",
    )

    # ── 4. Overhaul identity.audit_logs ───────────────────────────────
    # 4a. Add new columns
    op.add_column(
        "audit_logs",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="identity",
    )
    op.add_column(
        "audit_logs",
        sa.Column("actor_hash", sa.String(64), nullable=True),
        schema="identity",
    )
    op.add_column(
        "audit_logs",
        sa.Column("actor_role", sa.String(20), nullable=True),
        schema="identity",
    )
    op.add_column(
        "audit_logs",
        sa.Column("ip_address", sa.String(45), nullable=True),
        schema="identity",
    )

    # 4b. Change PK from integer to UUID
    #     - Add a new UUID column
    #     - Drop the old integer PK
    #     - Rename and promote the UUID column to PK
    op.add_column(
        "audit_logs",
        sa.Column(
            "new_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        schema="identity",
    )
    op.drop_constraint(
        "audit_logs_pkey", "audit_logs", schema="identity", type_="primary"
    )
    op.drop_column("audit_logs", "id", schema="identity")
    op.alter_column(
        "audit_logs",
        "new_id",
        new_column_name="id",
        schema="identity",
    )
    op.create_primary_key(
        "audit_logs_pkey", "audit_logs", ["id"], schema="identity"
    )

    # 4c. Add indexes on audit_logs
    op.create_index(
        "ix_audit_logs_tenant_id",
        "audit_logs",
        ["tenant_id"],
        schema="identity",
    )
    op.create_index(
        "ix_audit_logs_timestamp",
        "audit_logs",
        ["timestamp"],
        schema="identity",
    )

    # ── 5. Add soft-delete columns to identity.users ──────────────────
    op.add_column(
        "users",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        schema="identity",
    )
    op.add_column(
        "users",
        sa.Column("deactivated_at", sa.TIMESTAMP(), nullable=True),
        schema="identity",
    )

    # ── 6. Recreate tenant_members.tenant_id FK with ON DELETE CASCADE ─
    op.drop_constraint(
        "tenant_members_tenant_id_fkey",
        "tenant_members",
        schema="identity",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "tenant_members_tenant_id_fkey",
        "tenant_members",
        "tenants",
        ["tenant_id"],
        ["id"],
        source_schema="identity",
        referent_schema="identity",
        ondelete="CASCADE",
    )


def downgrade() -> None:
    # ── 6. Restore tenant_members.tenant_id FK without CASCADE ────────
    op.drop_constraint(
        "tenant_members_tenant_id_fkey",
        "tenant_members",
        schema="identity",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "tenant_members_tenant_id_fkey",
        "tenant_members",
        "tenants",
        ["tenant_id"],
        ["id"],
        source_schema="identity",
        referent_schema="identity",
    )

    # ── 5. Remove soft-delete columns from identity.users ─────────────
    op.drop_column("users", "deactivated_at", schema="identity")
    op.drop_column("users", "is_active", schema="identity")

    # ── 4. Reverse audit_logs overhaul ────────────────────────────────
    # 4c. Drop new indexes
    op.drop_index(
        "ix_audit_logs_timestamp", table_name="audit_logs", schema="identity"
    )
    op.drop_index(
        "ix_audit_logs_tenant_id", table_name="audit_logs", schema="identity"
    )

    # 4b. Restore integer PK
    op.drop_constraint(
        "audit_logs_pkey", "audit_logs", schema="identity", type_="primary"
    )
    op.alter_column(
        "audit_logs",
        "id",
        new_column_name="new_id",
        schema="identity",
    )
    op.add_column(
        "audit_logs",
        sa.Column(
            "id",
            sa.Integer(),
            autoincrement=True,
            nullable=False,
        ),
        schema="identity",
    )
    op.drop_column("audit_logs", "new_id", schema="identity")
    op.create_primary_key(
        "audit_logs_pkey", "audit_logs", ["id"], schema="identity"
    )

    # 4a. Drop new columns
    op.drop_column("audit_logs", "ip_address", schema="identity")
    op.drop_column("audit_logs", "actor_role", schema="identity")
    op.drop_column("audit_logs", "actor_hash", schema="identity")
    op.drop_column("audit_logs", "tenant_id", schema="identity")

    # ── 3. Restore tenant_members.role default to 'member' ────────────
    op.alter_column(
        "tenant_members",
        "role",
        server_default="member",
        schema="identity",
    )

    # ── 2. Remove team_id from identity.tenant_members ────────────────
    op.drop_index(
        "ix_tenant_members_team_id",
        table_name="tenant_members",
        schema="identity",
    )
    op.drop_constraint(
        "fk_tenant_members_team_id",
        "tenant_members",
        schema="identity",
        type_="foreignkey",
    )
    op.drop_column("tenant_members", "team_id", schema="identity")

    # ── 1. Drop identity.teams table ─────────────────────────────────
    op.drop_index(
        "ix_teams_manager_hash", table_name="teams", schema="identity"
    )
    op.drop_index(
        "ix_teams_tenant_id", table_name="teams", schema="identity"
    )
    op.drop_table("teams", schema="identity")
