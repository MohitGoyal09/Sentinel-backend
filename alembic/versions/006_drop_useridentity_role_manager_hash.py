"""Drop deprecated role and manager_hash columns from identity.users.

These columns are now superseded by TenantMember.role and TenantMember.team_id.

Revision ID: 006
Revises: 005
"""

from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_identity_users_manager_hash", table_name="users", schema="identity")
    op.drop_column("users", "role", schema="identity")
    op.drop_column("users", "manager_hash", schema="identity")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(20), server_default="employee"),
        schema="identity",
    )
    op.add_column(
        "users",
        sa.Column("manager_hash", sa.String(64), nullable=True),
        schema="identity",
    )
    op.create_index(
        "ix_identity_users_manager_hash",
        "users",
        ["manager_hash"],
        schema="identity",
    )
