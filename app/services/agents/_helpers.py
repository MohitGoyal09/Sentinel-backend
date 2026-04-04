"""
Shared helpers for all agent implementations.

Extracted to avoid duplication across general_agent, org_agent, and task_agent.
"""

import json
from typing import TypeVar

_T = TypeVar("_T")


def sse(payload: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(payload)}\n\n"


def next_chunk(it, sentinel: _T) -> _T:
    """PEP 479-safe wrapper: converts StopIteration into sentinel value.

    Used with ``run_in_executor`` to stream from a sync iterator
    without raising ``StopIteration`` across the coroutine boundary.
    """
    try:
        return next(it)
    except StopIteration:
        return sentinel
