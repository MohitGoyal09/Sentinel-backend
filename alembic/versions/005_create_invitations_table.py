"""Create identity.invitations table for RBAC Phase 3

Revision ID: 005
Revises: 004
Create Date: 2026-04-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invitations",
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
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("token", sa.String(128), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("invited_by", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.TIMESTAMP(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["identity.tenants.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["identity.teams.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("token", name="uq_invitations_token"),
        schema="identity",
    )
    op.create_index(
        "ix_invitations_tenant_id",
        "invitations",
        ["tenant_id"],
        schema="identity",
    )
    op.create_index(
        "ix_invitations_token",
        "invitations",
        ["token"],
        schema="identity",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_invitations_token",
        table_name="invitations",
        schema="identity",
    )
    op.drop_index(
        "ix_invitations_tenant_id",
        table_name="invitations",
        schema="identity",
    )
    op.drop_table("invitations", schema="identity")
