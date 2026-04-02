"""
Workflows API Endpoints

Provides CRUD operations for automated notification workflows.
Workflows connect a trigger condition (e.g. risk_level_change) to an action
(e.g. send a Slack message) so managers can configure hands-off alerting.

Storage: in-memory dict keyed by workflow_id for the initial implementation.
Each user/org gets an isolated view via current_user.user_hash as the owner key.
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps.auth import get_current_user_identity
from app.models.identity import UserIdentity

logger = logging.getLogger("sentinel.api.workflows")

router = APIRouter(prefix="/workflows", tags=["Workflows"])


# ============================================================================
# SCHEMAS
# ============================================================================


class WorkflowCreate(BaseModel):
    """Payload for creating a new workflow."""

    name: str = Field(..., description="Human-readable workflow name")
    trigger: str = Field(
        ...,
        description=(
            "Trigger condition: risk_level_change | weekly_schedule | "
            "new_alert | team_threshold"
        ),
    )
    action: str = Field(
        ...,
        description=(
            "Notification action: slack_message | email | jira_ticket | webhook"
        ),
    )
    action_config: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        description="Action-specific configuration (e.g. webhook URL, channel name)",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Alert on critical risk",
                "trigger": "risk_level_change",
                "action": "slack_message",
                "action_config": {"channel": "#eng-alerts", "mention": "@here"},
            }
        }


class WorkflowUpdate(BaseModel):
    """Payload for partially updating an existing workflow."""

    enabled: Optional[bool] = None
    name: Optional[str] = None


# ============================================================================
# IN-MEMORY STORE
# ============================================================================

# Structure: { owner_key: { workflow_id: workflow_dict } }
_store: Dict[str, Dict[str, Dict[str, Any]]] = {}

# Template workflows pre-populated for new users
_TEMPLATE_WORKFLOWS: List[Dict[str, Any]] = [
    {
        "name": "Critical Risk Alert",
        "trigger": "risk_level_change",
        "action": "slack_message",
        "action_config": {"channel": "#sentinel-alerts", "mention": "@channel"},
        "enabled": True,
        "description": "Sends a Slack notification when a team member enters critical risk.",
    },
    {
        "name": "Weekly Team Health Digest",
        "trigger": "weekly_schedule",
        "action": "email",
        "action_config": {"subject": "Weekly Sentinel Team Report"},
        "enabled": True,
        "description": "Delivers a weekly email digest of team wellbeing metrics.",
    },
    {
        "name": "New Alert Jira Ticket",
        "trigger": "new_alert",
        "action": "jira_ticket",
        "action_config": {"project": "OPS", "issue_type": "Task", "priority": "High"},
        "enabled": False,
        "description": "Creates a Jira ticket automatically when a new alert fires.",
    },
]

_VALID_TRIGGERS = {"risk_level_change", "weekly_schedule", "new_alert", "team_threshold"}
_VALID_ACTIONS = {"slack_message", "email", "jira_ticket", "webhook"}


# ============================================================================
# HELPERS
# ============================================================================


def _owner_key(user: UserIdentity) -> str:
    """Derive a stable owner key from the current user."""
    return user.user_hash


def _ensure_store_for(owner: str) -> Dict[str, Dict[str, Any]]:
    """Return (and lazily initialise) the workflow dict for an owner."""
    if owner not in _store:
        _store[owner] = {}
        # Seed with template workflows the first time
        for template in _TEMPLATE_WORKFLOWS:
            wf_id = str(uuid.uuid4())
            _store[owner][wf_id] = {
                "id": wf_id,
                "owner": owner,
                "name": template["name"],
                "trigger": template["trigger"],
                "action": template["action"],
                "action_config": template["action_config"],
                "enabled": template["enabled"],
                "description": template.get("description", ""),
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            }
    return _store[owner]


def _workflow_to_dict(wf: Dict[str, Any]) -> Dict[str, Any]:
    """Return a safe public representation of a workflow (strip owner key)."""
    return {k: v for k, v in wf.items() if k != "owner"}


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("")
@router.get("/")
async def get_workflows(
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """
    Get all workflows for the current user / organisation.

    On first call, seeds three template workflows so the UI is never empty.
    """
    owner = _owner_key(current_user)
    workflows = _ensure_store_for(owner)

    return {
        "workflows": [_workflow_to_dict(wf) for wf in workflows.values()],
        "total": len(workflows),
    }


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_workflow(
    workflow: WorkflowCreate,
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """
    Create a new automated workflow.

    Validates trigger and action values before persisting.
    """
    if workflow.trigger not in _VALID_TRIGGERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid trigger '{workflow.trigger}'. "
                f"Valid values: {sorted(_VALID_TRIGGERS)}"
            ),
        )

    if workflow.action not in _VALID_ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid action '{workflow.action}'. "
                f"Valid values: {sorted(_VALID_ACTIONS)}"
            ),
        )

    owner = _owner_key(current_user)
    workflows = _ensure_store_for(owner)

    wf_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    new_workflow: Dict[str, Any] = {
        "id": wf_id,
        "owner": owner,
        "name": workflow.name,
        "trigger": workflow.trigger,
        "action": workflow.action,
        "action_config": workflow.action_config or {},
        "enabled": True,
        "description": "",
        "created_at": now,
        "updated_at": now,
    }
    workflows[wf_id] = new_workflow

    logger.info(f"Workflow created: id={wf_id} trigger={workflow.trigger} owner={owner}")

    return _workflow_to_dict(new_workflow)


@router.patch("/{workflow_id}")
async def update_workflow(
    workflow_id: str,
    update: WorkflowUpdate,
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """
    Enable/disable or rename an existing workflow.

    Returns 404 when the workflow_id does not belong to the current user.
    """
    owner = _owner_key(current_user)
    workflows = _ensure_store_for(owner)

    if workflow_id not in workflows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found.",
        )

    wf = workflows[workflow_id]

    if update.enabled is not None:
        wf["enabled"] = update.enabled
    if update.name is not None:
        wf["name"] = update.name

    wf["updated_at"] = datetime.utcnow().isoformat()

    logger.info(f"Workflow updated: id={workflow_id} owner={owner}")

    return _workflow_to_dict(wf)


@router.delete("/{workflow_id}", status_code=status.HTTP_200_OK)
async def delete_workflow(
    workflow_id: str,
    current_user: UserIdentity = Depends(get_current_user_identity),
):
    """
    Delete a workflow permanently.

    Returns 404 when the workflow_id does not belong to the current user.
    """
    owner = _owner_key(current_user)
    workflows = _ensure_store_for(owner)

    if workflow_id not in workflows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found.",
        )

    del workflows[workflow_id]

    logger.info(f"Workflow deleted: id={workflow_id} owner={owner}")

    return {"deleted": True, "workflow_id": workflow_id}
