"""Add chat_history table for Ask Sentinel conversation persistence.

Revision ID: 007
Revises: 006
"""

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_hash", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("conversation_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        schema="identity",
    )

    # Single-column index for user_hash lookups
    op.create_index(
        "ix_chat_history_user_hash",
        "chat_history",
        ["user_hash"],
        schema="identity",
    )

    # Composite index for retrieving a specific conversation by a specific user
    op.create_index(
        "ix_chat_history_user_conversation",
        "chat_history",
        ["user_hash", "conversation_id"],
        schema="identity",
    )

    # Composite index for listing all conversations for a user within a tenant
    op.create_index(
        "ix_chat_history_user_tenant",
        "chat_history",
        ["user_hash", "tenant_id"],
        schema="identity",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chat_history_user_tenant",
        table_name="chat_history",
        schema="identity",
    )
    op.drop_index(
        "ix_chat_history_user_conversation",
        table_name="chat_history",
        schema="identity",
    )
    op.drop_index(
        "ix_chat_history_user_hash",
        table_name="chat_history",
        schema="identity",
    )
    op.drop_table("chat_history", schema="identity")
