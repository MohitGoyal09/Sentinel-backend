"""Add workflow automation tables

Revision ID: 003
Revises: 002
Create Date: 2026-03-31

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # user_integrations table
    op.create_table(
        'user_integrations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_hash', sa.String(length=64), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('integration_id', sa.String(length=100), nullable=False),
        sa.Column('integration_name', sa.String(length=255), nullable=True),
        sa.Column('account_id', sa.String(length=100), nullable=False),
        sa.Column('account_identifier', sa.String(length=255), nullable=True),
        sa.Column('scopes', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('provider', sa.String(length=50), nullable=True),
        sa.Column('status', sa.String(length=20), server_default='active', nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('connected_at', sa.TIMESTAMP(), server_default=sa.text('now()'), nullable=True),
        sa.Column('last_used_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('token_expires_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_hash', 'tenant_id', 'integration_id', 'account_id',
                           name='uq_user_integration_account')
    )
    op.create_index('idx_user_integrations_user', 'user_integrations', ['user_hash'])
    op.create_index('idx_user_integrations_status', 'user_integrations', ['status'])
    op.create_index('idx_user_integrations_integration', 'user_integrations', ['integration_id'])

    # workflow_templates table
    op.create_table(
        'workflow_templates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('template_id', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', sa.String(length=50), nullable=True),
        sa.Column('icon', sa.String(length=100), nullable=True),
        sa.Column('prompt_template', sa.Text(), nullable=False),
        sa.Column('required_integrations', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('optional_integrations', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('parameters', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('is_public', sa.Boolean(), server_default='false', nullable=True),
        sa.Column('is_system', sa.Boolean(), server_default='false', nullable=True),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('usage_count', sa.Integer(), server_default='0', nullable=True),
        sa.Column('last_used_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('template_id', name='uq_template_id')
    )
    op.create_index('idx_workflow_templates_category', 'workflow_templates', ['category'])
    op.create_index('idx_workflow_templates_public', 'workflow_templates', ['is_public'])
    op.create_index('idx_workflow_templates_tenant', 'workflow_templates', ['tenant_id'])

    # workflow_executions table
    op.create_table(
        'workflow_executions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('execution_id', sa.String(length=50), nullable=False),
        sa.Column('workflow_id', sa.String(length=50), nullable=False),
        sa.Column('execution_type', sa.String(length=20), nullable=False),
        sa.Column('template_id', sa.String(length=50), nullable=True),
        sa.Column('scheduled_id', sa.String(length=50), nullable=True),
        sa.Column('user_hash', sa.String(length=64), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_message', sa.Text(), nullable=False),
        sa.Column('conversation_context', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('started_at', sa.TIMESTAMP(), server_default=sa.text('now()'), nullable=True),
        sa.Column('completed_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('execution_time_ms', sa.Integer(), nullable=True),
        sa.Column('tools_used', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('integrations_used', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('result_summary', sa.Text(), nullable=True),
        sa.Column('result_artifacts', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('full_conversation', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('intent_classification_ms', sa.Integer(), nullable=True),
        sa.Column('session_creation_ms', sa.Integer(), nullable=True),
        sa.Column('llm_execution_ms', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('execution_id', name='uq_execution_id')
    )
    op.create_index('idx_workflow_executions_user', 'workflow_executions', ['user_hash'])
    op.create_index('idx_workflow_executions_type', 'workflow_executions', ['execution_type'])
    op.create_index('idx_workflow_executions_status', 'workflow_executions', ['status'])
    op.create_index('idx_workflow_executions_template', 'workflow_executions', ['template_id'])


def downgrade() -> None:
    op.drop_index('idx_workflow_executions_template', table_name='workflow_executions')
    op.drop_index('idx_workflow_executions_status', table_name='workflow_executions')
    op.drop_index('idx_workflow_executions_type', table_name='workflow_executions')
    op.drop_index('idx_workflow_executions_user', table_name='workflow_executions')
    op.drop_table('workflow_executions')

    op.drop_index('idx_workflow_templates_tenant', table_name='workflow_templates')
    op.drop_index('idx_workflow_templates_public', table_name='workflow_templates')
    op.drop_index('idx_workflow_templates_category', table_name='workflow_templates')
    op.drop_table('workflow_templates')

    op.drop_index('idx_user_integrations_integration', table_name='user_integrations')
    op.drop_index('idx_user_integrations_status', table_name='user_integrations')
    op.drop_index('idx_user_integrations_user', table_name='user_integrations')
    op.drop_table('user_integrations')
