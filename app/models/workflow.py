"""
Workflow Automation Models

Models for workflow templates, executions, and integrations.
"""
from sqlalchemy import Column, String, Integer, Boolean, TIMESTAMP, Text, Index, JSON, text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID, ARRAY
from datetime import datetime

from app.models.identity import Base


class UserIntegration(Base):
    """User's connected OAuth integrations (Gmail, Calendar, Slack, etc.)"""

    __tablename__ = 'user_integrations'

    id = Column(Integer, primary_key=True)
    user_hash = Column(String(64), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)

    integration_id = Column(String(100), nullable=False)
    integration_name = Column(String(255))
    account_id = Column(String(100), nullable=False)
    account_identifier = Column(String(255))

    scopes = Column(JSON)
    provider = Column(String(50))

    status = Column(String(20), default='active')
    error_message = Column(Text)

    connected_at = Column(TIMESTAMP, default=datetime.utcnow)
    last_used_at = Column(TIMESTAMP)
    token_expires_at = Column(TIMESTAMP)

    created_by = Column(String(64))
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('idx_user_integrations_user', 'user_hash'),
        Index('idx_user_integrations_status', 'status'),
        Index('idx_user_integrations_integration', 'integration_id'),
        UniqueConstraint('user_hash', 'tenant_id', 'integration_id', 'account_id', name='uq_user_integration_account'),
    )

    def to_dict(self) -> dict:
        """Return a JSON-serialisable representation for API responses."""
        return {
            'id': self.id,
            'integration_id': self.integration_id,
            'integration_name': self.integration_name,
            'account_id': self.account_id,
            'account_identifier': self.account_identifier,
            'status': self.status,
            'connected_at': self.connected_at.isoformat() if self.connected_at else None,
            'last_used_at': self.last_used_at.isoformat() if self.last_used_at else None,
            'token_expires_at': self.token_expires_at.isoformat() if self.token_expires_at else None,
            'scopes': self.scopes,
            'provider': self.provider,
        }


class WorkflowTemplate(Base):
    """Pre-built and custom workflow templates"""

    __tablename__ = 'workflow_templates'

    id = Column(Integer, primary_key=True)
    template_id = Column(String(50), nullable=False)

    name = Column(String(255), nullable=False)
    description = Column(Text)
    category = Column(String(50))
    icon = Column(String(100))

    prompt_template = Column(Text, nullable=False)
    required_integrations = Column(JSON, nullable=False)
    optional_integrations = Column(JSON)
    parameters = Column(JSON)

    is_public = Column(Boolean, default=False)
    is_system = Column(Boolean, default=False)
    created_by = Column(String(64))
    tenant_id = Column(UUID(as_uuid=True))

    usage_count = Column(Integer, default=0)
    last_used_at = Column(TIMESTAMP)

    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('idx_workflow_templates_category', 'category'),
        Index('idx_workflow_templates_public', 'is_public'),
        Index('idx_workflow_templates_tenant', 'tenant_id'),
        UniqueConstraint('template_id', name='uq_template_id'),
    )

    def to_dict(self) -> dict:
        """Return a JSON-serialisable representation for API responses."""
        return {
            'id': self.template_id,
            'name': self.name,
            'description': self.description,
            'category': self.category,
            'icon': self.icon,
            'required_integrations': self.required_integrations,
            'optional_integrations': self.optional_integrations,
            'parameters': self.parameters,
            'usage_count': self.usage_count,
            'is_public': self.is_public,
            'is_system': self.is_system,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class WorkflowExecution(Base):
    """Execution history for all workflows.

    Every workflow run (template-based, ad-hoc, or scheduled) produces one
    row.  Timing columns are populated at completion so that performance
    can be analysed per phase.

    execution_type values:
        ``template``  — run from a WorkflowTemplate
        ``custom``    — ad-hoc natural-language instruction
        ``scheduled`` — triggered by a recurring schedule

    status values:
        ``running``, ``completed``, ``failed``
    """

    __tablename__ = 'workflow_executions'

    id = Column(Integer, primary_key=True)
    execution_id = Column(String(50), nullable=False)
    workflow_id = Column(String(50), nullable=False)

    execution_type = Column(String(20), nullable=False)
    template_id = Column(String(50))
    scheduled_id = Column(String(50))

    user_hash = Column(String(64), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)

    user_message = Column(Text, nullable=False)
    conversation_context = Column(JSON)

    status = Column(String(20), nullable=False)
    started_at = Column(TIMESTAMP, default=datetime.utcnow)
    completed_at = Column(TIMESTAMP)
    execution_time_ms = Column(Integer)

    tools_used = Column(JSON)
    integrations_used = Column(JSON)  # Store as JSON list (ARRAY not supported in SQLite)

    result_summary = Column(Text)
    result_artifacts = Column(JSON)
    full_conversation = Column(JSON)
    error_message = Column(Text)

    intent_classification_ms = Column(Integer)
    session_creation_ms = Column(Integer)
    llm_execution_ms = Column(Integer)

    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_workflow_executions_user', 'user_hash'),
        Index('idx_workflow_executions_type', 'execution_type'),
        Index('idx_workflow_executions_status', 'status'),
        Index('idx_workflow_executions_template', 'template_id'),
        UniqueConstraint('execution_id', name='uq_execution_id'),
    )

    def to_dict(self) -> dict:
        """Return a JSON-serialisable summary for API responses.

        Omits ``full_conversation`` (potentially large) and timing internals.
        """
        return {
            'execution_id': self.execution_id,
            'workflow_id': self.workflow_id,
            'type': self.execution_type,
            'user_message': self.user_message,
            'status': self.status,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'execution_time_ms': self.execution_time_ms,
            'tools_used': self.tools_used,
            'integrations_used': self.integrations_used,
            'result_summary': self.result_summary,
            'result_artifacts': self.result_artifacts,
        }
