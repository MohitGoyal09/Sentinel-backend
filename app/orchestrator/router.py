# Orchestrator Router
# FastAPI endpoints for orchestration

import uuid
import time
from typing import List, Dict
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime

from app.orchestrator.schemas import (
    OrchestrationRequest,
    OrchestrationResponse,
    AggregatedResult,
    AgentInfo,
    AgentListResponse,
    HealthResponse
)
from app.orchestrator.dispatcher import TaskDispatcher
from app.orchestrator.registry import AgentRegistry
from app.orchestrator.aggregator import ResultAggregator
from app.api.deps import get_current_user

router = APIRouter()

# Initialize dispatcher and aggregator
dispatcher = TaskDispatcher(max_workers=20)
aggregator = ResultAggregator()


@router.post("/orchestrate", response_model=OrchestrationResponse)
async def orchestrate(
    request: OrchestrationRequest,
    current_user: Dict = Depends(get_current_user)
) -> OrchestrationResponse:
    """
    Execute multiple agents in parallel and aggregate results.
    
    Request Body:
    {
        "task_group_id": "optional-group-id",
        "tasks": [
            {
                "task_id": "unique-id",
                "agent_id": "safety_valve",
                "payload": {...},
                "dependencies": []  // Optional task dependencies
            }
        ],
        "aggregation_strategy": "hierarchical | weighted_average | majority_vote | ensemble",
        "timeout_ms": 30000
    }
    """
    start_time = time.perf_counter()
    
    # Generate task group ID if not provided
    task_group_id = request.task_group_id or f"group_{uuid.uuid4().hex[:8]}"
    
    # Validate all agents exist
    for task in request.tasks:
        if not AgentRegistry.exists(task.agent_id):
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{task.agent_id}' not found. "
                       f"Available: {AgentRegistry.get_online_agents()}"
            )
    
    # Convert tasks to dict format
    task_dicts = [
        {
            "task_id": task.task_id,
            "agent_id": task.agent_id,
            "payload": task.payload,
            "dependencies": task.dependencies
        }
        for task in request.tasks
    ]
    
    # Execute parallel tasks
    results = await dispatcher.dispatch_parallel(task_dicts)
    
    # Aggregate results
    aggregated = aggregator.aggregate(results, request.aggregation_strategy)
    
    execution_time_ms = int((time.perf_counter() - start_time) * 1000)
    
    return OrchestrationResponse(
        task_group_id=task_group_id,
        aggregated_result=aggregated,
        individual_results=results,
        execution_time_ms=execution_time_ms
    )


@router.get("/agents", response_model=AgentListResponse)
async def list_agents() -> AgentListResponse:
    """
    List all available agents and their status.
    """
    agents = AgentRegistry.list_all()
    
    return AgentListResponse(
        agents=agents,
        total_count=len(agents)
    )


@router.get("/agents/{agent_id}", response_model=AgentInfo)
async def get_agent(agent_id: str) -> Dict:
    """
    Get detailed information about a specific agent.
    """
    agent = AgentRegistry.get(agent_id)
    
    if not agent:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{agent_id}' not found"
        )
    
    config = AgentRegistry.get_config(agent_id) or {}
    stats = AgentRegistry.get_stats(agent_id) or {}
    
    return {
        "agent_id": agent_id,
        "name": agent.name,
        "agent_type": getattr(agent, "agent_type", "unknown"),
        "capabilities": agent.get_capabilities(),
        "status": config.get("status", "unknown"),
        "max_concurrent_tasks": config.get("max_concurrent_tasks", 10),
        "timeout_seconds": config.get("timeout_seconds", 30),
        "stats": stats
    }


@router.get("/agents/{agent_id}/health", response_model=HealthResponse)
async def agent_health(agent_id: str) -> HealthResponse:
    """
    Check health status of an individual agent.
    """
    agent = AgentRegistry.get(agent_id)
    
    if not agent:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{agent_id}' not found"
        )
    
    config = AgentRegistry.get_config(agent_id) or {}
    stats = AgentRegistry.get_stats(agent_id) or {}
    
    # Determine health status
    if config.get("status") == "offline":
        status = "unhealthy"
    elif stats.get("failed_executions", 0) > 10:
        # More than 10 recent failures
        status = "degraded"
    else:
        status = "healthy"
    
    return HealthResponse(
        status=status,
        agent_id=agent_id,
        timestamp=datetime.utcnow(),
        uptime_seconds=stats.get("total_execution_time_ms", 0) // 1000
    )


@router.post("/agents/{agent_id}/status")
async def set_agent_status(
    agent_id: str,
    status: str,
    current_user: Dict = Depends(get_current_user)
) -> Dict:
    """
    Set the status of an agent (online/offline/busy).
    Requires admin authentication.
    """
    # Check admin role
    user_role = current_user.get("role", "employee")
    if user_role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only admins can change agent status"
        )
    
    if not AgentRegistry.exists(agent_id):
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{agent_id}' not found"
        )
    
    valid_statuses = ["online", "offline", "busy"]
    if status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {valid_statuses}"
        )
    
    AgentRegistry.set_status(agent_id, status)
    
    return {
        "success": True,
        "agent_id": agent_id,
        "new_status": status
    }


@router.get("/health")
async def orchestrator_health() -> Dict:
    """
    Overall orchestrator health check.
    """
    agents = AgentRegistry.list_all()
    
    online_count = sum(1 for a in agents if a.get("status") == "online")
    offline_count = sum(1 for a in agents if a.get("status") == "offline")
    
    if offline_count == len(agents):
        status = "unhealthy"
    elif offline_count > 0:
        status = "degraded"
    else:
        status = "healthy"
    
    return {
        "status": status,
        "orchestrator": " AlgoQuest Orchestrator",
        "timestamp": datetime.utcnow().isoformat(),
        "agents": {
            "total": len(agents),
            "online": online_count,
            "offline": offline_count
        }
    }
