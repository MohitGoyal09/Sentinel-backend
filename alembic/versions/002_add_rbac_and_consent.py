"""Add RBAC and consent columns to UserIdentity

Revision ID: 002
Revises: 001
Create Date: 2026-02-10 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add RBAC columns to identity.users table
    op.add_column(
        "users",
        sa.Column("role", sa.String(20), nullable=False, server_default="employee"),
        schema="identity",
    )

    op.add_column(
        "users",
        sa.Column(
            "consent_share_with_manager",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        schema="identity",
    )

    op.add_column(
        "users",
        sa.Column(
            "consent_share_anonymized",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        schema="identity",
    )

    op.add_column(
        "users",
        sa.Column("monitoring_paused_until", sa.DateTime(), nullable=True),
        schema="identity",
    )

    op.add_column(
        "users",
        sa.Column("manager_hash", sa.String(64), nullable=True),
        schema="identity",
    )

    # Create index on manager_hash for efficient lookups
    op.create_index(
        "idx_users_manager_hash", "users", ["manager_hash"], schema="identity"
    )

    # Add new action types to audit_logs
    # Note: In production, you might want to use an ENUM, but for flexibility we'll keep it as String


def downgrade() -> None:
    # Remove index first
    op.drop_index("idx_users_manager_hash", table_name="users", schema="identity")

    # Drop columns in reverse order
    op.drop_column("users", "manager_hash", schema="identity")
    op.drop_column("users", "monitoring_paused_until", schema="identity")
    op.drop_column("users", "consent_share_anonymized", schema="identity")
    op.drop_column("users", "consent_share_with_manager", schema="identity")
    op.drop_column("users", "role", schema="identity")
