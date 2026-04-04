"""
Agents package -- defines the Agent Protocol and re-exports concrete agents.

All agents satisfy the ``Agent`` structural protocol: they expose a single
``respond_stream`` coroutine that yields SSE-formatted strings.
"""

from typing import AsyncGenerator, Protocol, runtime_checkable

from sqlalchemy.orm import Session

from app.models.identity import UserIdentity
from app.models.tenant import TenantMember


@runtime_checkable
class Agent(Protocol):
    """Structural protocol satisfied by every concrete agent.

    Implementors must expose ``respond_stream`` with this exact signature.
    The method is an async generator that yields SSE-formatted strings
    (``"data: {...}\\n\\n"``).  It must *always* emit a terminal ``done``
    event as its last yield and must *never* raise -- errors are surfaced as
    ``error`` SSE events instead.
    """

    async def respond_stream(
        self,
        message: str,
        user: UserIdentity,
        member: TenantMember,
        tenant_id: str,
        session_id: str,
        conversation_history: list[dict],
        db: Session,
    ) -> AsyncGenerator[str, None]:
        ...


from app.services.agents.general_agent import GeneralAgent  # noqa: E402
from app.services.agents.org_agent import OrgAgent  # noqa: E402
from app.services.agents.task_agent import TaskAgent  # noqa: E402

__all__ = ["Agent", "GeneralAgent", "OrgAgent", "TaskAgent"]
