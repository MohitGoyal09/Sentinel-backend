# Task Dispatcher
# Parallel task execution with thread pool

import asyncio
import uuid
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor
import structlog

from app.orchestrator.registry import AgentRegistry

log = structlog.get_logger(__name__)


class TaskDispatcher:
    """
    Dispatches tasks to agents with parallel execution.
    
    Features:
    - Thread pool for concurrent execution
    - Automatic timeout handling
    - Statistics tracking
    - Graceful error handling
    """
    
    def __init__(
        self,
        max_workers: int = 20,
        default_timeout_seconds: int = 30
    ):
        """
        Initialize the task dispatcher.
        
        Args:
            max_workers: Maximum concurrent tasks
            default_timeout_seconds: Default task timeout
        """
        self.max_workers = max_workers
        self.default_timeout = default_timeout_seconds
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.active_tasks: Dict[str, asyncio.Task] = {}
    
    async def dispatch_parallel(
        self,
        tasks: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Dispatch multiple tasks to execute in parallel.
        
        Args:
            tasks: List of task dicts with:
                - task_id: Unique task identifier
                - agent_id: Agent to execute
                - payload: Agent input
                - dependencies: List of task_ids this depends on (optional)
        
        Returns:
            Dict mapping task_id to result
        """
        if not tasks:
            log.info("dispatcher.no_tasks")
            return {}
        
        log.info(
            "dispatcher.start",
            task_count=len(tasks),
            agent_ids=list(set(t.get("agent_id") for t in tasks))
        )
        
        # Build task map for dependency resolution
        task_map = {task["task_id"]: task for task in tasks}
        
        # Resolve dependencies and determine execution order
        execution_order = self._resolve_dependencies(task_map)
        
        # Execute tasks in dependency order
        results: Dict[str, Any] = {}
        
        for level in execution_order:
            level_tasks = [task_map[task_id] for task_id in level]
            
            # Execute all tasks in this level in parallel
            level_results = await self._execute_level(level_tasks)
            
            results.update(level_results)
            
            log.debug(
                "dispatcher.level_complete",
                level_tasks=level,
                completed=len(level_results)
            )
        
        log.info(
            "dispatcher.complete",
            total_tasks=len(results),
            successful=sum(1 for r in results.values() if r.get("status") == "success"),
            failed=sum(1 for r in results.values() if r.get("status") != "success")
        )
        
        return results
    
    def _resolve_dependencies(
        self,
        task_map: Dict[str, Dict]
    ) -> List[List[str]]:
        """
        Resolve task dependencies and return execution order.
        
        Args:
            task_map: Dict mapping task_id to task
            
        Returns:
            List of levels, where each level is a list of task_ids
        """
        # Kahn's algorithm for topological sort
        in_degree: Dict[str, int] = {}
        dependency_map: Dict[str, List[str]] = {}
        
        for task_id, task in task_map.items():
            deps = task.get("dependencies", [])
            dependency_map[task_id] = deps
            in_degree[task_id] = len(deps)
        
        # Start with tasks with no dependencies
        queue = [tid for tid, deg in in_degree.items() if deg == 0]
        levels = []
        
        while queue:
            level = list(queue)
            levels.append(level)
            
            next_queue = []
            
            for completed_id in level:
                for task_id, deps in dependency_map.items():
                    if completed_id in deps:
                        in_degree[task_id] -= 1
                        if in_degree[task_id] == 0:
                            next_queue.append(task_id)
            
            queue = next_queue
        
        # Check for cycles
        if sum(len(level) for level in levels) != len(task_map):
            log.error("dispatcher.cycle_detected")
            # Fall back to FIFO execution
            return [list(task_map.keys())]
        
        return levels
    
    async def _execute_level(
        self,
        tasks: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Execute a level of tasks in parallel.
        
        Args:
            tasks: List of task dicts
            
        Returns:
            Dict mapping task_id to result
        """
        if not tasks:
            return {}
        
        # Create coroutines for all tasks
        coroutines = [
            self._execute_task(
                task["agent_id"],
                task.get("payload", {}),
                task.get("task_id", f"task_{i}")
            )
            for i, task in enumerate(tasks)
        ]
        
        # Execute with error handling
        results = {}
        completed = await asyncio.gather(
            *coroutines,
            return_exceptions=True
        )
        
        for i, result in enumerate(completed):
            task_id = tasks[i].get("task_id", f"task_{i}")
            
            if isinstance(result, Exception):
                results[task_id] = {
                    "task_id": task_id,
                    "status": "error",
                    "error": str(result)
                }
            else:
                results[task_id] = result
        
        return results
    
    async def _execute_task(
        self,
        agent_id: str,
        payload: Dict[str, Any],
        task_id: str
    ) -> Dict[str, Any]:
        """
        Execute a single task against an agent.
        
        Args:
            agent_id: Agent to execute
            payload: Agent input
            task_id: Unique task identifier
            
        Returns:
            Result dict with status, result, and metrics
        """
        agent = AgentRegistry.get(agent_id)
        
        if not agent:
            return {
                "task_id": task_id,
                "agent_id": agent_id,
                "status": "error",
                "error": f"Agent {agent_id} not found"
            }
        
        config = AgentRegistry.get_config(agent_id)
        timeout = config.get("timeout_seconds", self.default_timeout)
        
        loop = asyncio.get_event_loop()
        start_time = asyncio.get_event_loop().time()
        
        try:
            # Execute with timeout
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    self.executor,
                    lambda: agent.run(payload)
                ),
                timeout=timeout
            )
            
            execution_time_ms = int((loop.time() - start_time) * 1000)
            
            # Update statistics
            AgentRegistry.update_stats(
                agent_id,
                success=(result.get("status") == "success"),
                execution_time_ms=execution_time_ms
            )
            
            return result
            
        except asyncio.TimeoutError:
            execution_time_ms = int((loop.time() - start_time) * 1000)
            
            AgentRegistry.update_stats(agent_id, False, execution_time_ms)
            
            log.warning(
                "dispatcher.task_timeout",
                task_id=task_id,
                agent_id=agent_id,
                timeout_seconds=timeout
            )
            
            return {
                "task_id": task_id,
                "agent_id": agent_id,
                "status": "timeout",
                "error": f"Task timed out after {timeout}s",
                "execution_time_ms": execution_time_ms
            }
            
        except Exception as e:
            execution_time_ms = int((loop.time() - start_time) * 1000)
            
            AgentRegistry.update_stats(agent_id, False, execution_time_ms)
            
            log.error(
                "dispatcher.task_error",
                task_id=task_id,
                agent_id=agent_id,
                error=str(e)
            )
            
            return {
                "task_id": task_id,
                "agent_id": agent_id,
                "status": "error",
                "error": str(e),
                "execution_time_ms": execution_time_ms
            }
    
    def shutdown(self) -> None:
        """Shutdown the executor pool."""
        self.executor.shutdown(wait=True)
        log.info("dispatcher.shutdown")


# Alias for backward compatibility
OrchestrationDispatcher = TaskDispatcher
