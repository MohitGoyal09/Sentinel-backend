"""Initial schema creation for analytics and identity

Revision ID: 001
Revises:
Create Date: 2026-01-31 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create schemas
    op.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    op.execute("CREATE SCHEMA IF NOT EXISTS identity")
    
    # ### Analytics Schema Tables ###
    
    # Events table
    op.create_table(
        'events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_hash', sa.String(64), index=True, nullable=False),
        sa.Column('timestamp', sa.DateTime(), default=sa.func.now()),
        sa.Column('event_type', sa.String(50), nullable=False),
        sa.Column('target_user_hash', sa.String(64), nullable=True),
        sa.Column('metadata', postgresql.JSONB(), default={}),
        schema='analytics'
    )
    op.create_index('idx_events_user_timestamp', 'events', ['user_hash', 'timestamp'], schema='analytics')
    
    # Risk scores table
    op.create_table(
        'risk_scores',
        sa.Column('user_hash', sa.String(64), primary_key=True),
        sa.Column('velocity', sa.Float(), nullable=True),
        sa.Column('risk_level', sa.String(20), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('thwarted_belongingness', sa.Float(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), default=sa.func.now(), onupdate=sa.func.now()),
        schema='analytics'
    )
    
    # Graph edges table
    op.create_table(
        'graph_edges',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('source_hash', sa.String(64), index=True, nullable=False),
        sa.Column('target_hash', sa.String(64), index=True, nullable=False),
        sa.Column('weight', sa.Float(), nullable=True),
        sa.Column('last_interaction', sa.DateTime(), nullable=True),
        sa.Column('edge_type', sa.String(20), nullable=True),
        schema='analytics'
    )
    op.create_index('idx_graph_edges_source_target', 'graph_edges', ['source_hash', 'target_hash'], schema='analytics')
    
    # Centrality scores table
    op.create_table(
        'centrality_scores',
        sa.Column('user_hash', sa.String(64), primary_key=True),
        sa.Column('betweenness', sa.Float(), nullable=True),
        sa.Column('eigenvector', sa.Float(), nullable=True),
        schema='analytics'
    )
    
    # ### Identity Schema Tables ###
    
    # Users table (Vault B)
    op.create_table(
        'users',
        sa.Column('user_hash', sa.String(64), primary_key=True),
        sa.Column('email_encrypted', sa.LargeBinary(), nullable=False),
        sa.Column('slack_id_encrypted', sa.LargeBinary(), nullable=True),
        sa.Column('created_at', sa.DateTime(), default=sa.func.now()),
        schema='identity'
    )
    
    # Audit logs table
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column('user_hash', sa.String(64), index=True, nullable=False),
        sa.Column('action', sa.String(50), nullable=False),
        sa.Column('details', postgresql.JSONB(), default={}),
        sa.Column('timestamp', sa.DateTime(), default=sa.func.now()),
        schema='identity'
    )


def downgrade() -> None:
    # Drop tables in reverse order
    op.drop_table('audit_logs', schema='identity')
    op.drop_table('users', schema='identity')
    
    op.drop_table('centrality_scores', schema='analytics')
    op.drop_table('graph_edges', schema='analytics')
    op.drop_table('risk_scores', schema='analytics')
    op.drop_table('events', schema='analytics')
    
    # Drop schemas
    op.execute("DROP SCHEMA IF EXISTS analytics CASCADE")
    op.execute("DROP SCHEMA IF EXISTS identity CASCADE")
