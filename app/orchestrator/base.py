# Base Agent Class
# Abstract base class for all agents

from abc import ABC, abstractmethod
from typing import Dict, Any, List
import asyncio
import time
import structlog

log = structlog.get_logger(__name__)


class BaseAgent(ABC):
    """Abstract base class for all orchestrator agents."""
    
    agent_id: str = "base_agent"
    name: str = "Base Agent"
    agent_type: str = "base"
    
    @abstractmethod
    async def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute agent logic with payload.
        
        Args:
            payload: Dictionary containing agent-specific parameters
            
        Returns:
            Dictionary with agent results
        """
        pass
    
    @abstractmethod
    def get_capabilities(self) -> List[str]:
        """
        Return list of agent capabilities.
        
        Returns:
            List of capability strings
        """
        pass
    
    async def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute agent with logging, metrics, and error handling.
        
        Args:
            payload: Agent input parameters
            
        Returns:
            Dictionary with status, result, and metrics
        """
        start_time = time.perf_counter()
        task_id = payload.get("task_id", self.agent_id)
        
        try:
            log.info(
                "agent.start",
                agent_id=self.agent_id,
                task_id=task_id,
                payload_keys=list(payload.keys()) if payload else []
            )
            
            # Execute the agent logic
            result = await self.execute(payload)
            
            execution_time_ms = int((time.perf_counter() - start_time) * 1000)
            
            log.info(
                "agent.complete",
                agent_id=self.agent_id,
                task_id=task_id,
                execution_time_ms=execution_time_ms
            )
            
            return {
                "agent_id": self.agent_id,
                "task_id": task_id,
                "status": "success",
                "result": result,
                "execution_time_ms": execution_time_ms
            }
            
        except asyncio.TimeoutError as e:
            execution_time_ms = int((time.perf_counter() - start_time) * 1000)
            
            log.error(
                "agent.timeout",
                agent_id=self.agent_id,
                task_id=task_id,
                error=str(e),
                execution_time_ms=execution_time_ms
            )
            
            return {
                "agent_id": self.agent_id,
                "task_id": task_id,
                "status": "timeout",
                "error": f"Task timed out: {str(e)}",
                "execution_time_ms": execution_time_ms
            }
            
        except Exception as e:
            execution_time_ms = int((time.perf_counter() - start_time) * 1000)
            
            log.error(
                "agent.error",
                agent_id=self.agent_id,
                task_id=task_id,
                error=str(e),
                error_type=type(e).__name__,
                execution_time_ms=execution_time_ms
            )
            
            return {
                "agent_id": self.agent_id,
                "task_id": task_id,
                "status": "error",
                "error": str(e),
                "error_type": type(e).__name__,
                "execution_time_ms": execution_time_ms
            }
    
    def validate_payload(self, payload: Dict[str, Any], required_fields: List[str]) -> bool:
        """
        Validate that required fields are present in payload.
        
        Args:
            payload: Input payload to validate
            required_fields: List of required field names
            
        Returns:
            True if valid, raises ValueError otherwise
        """
        missing = [field for field in required_fields if field not in payload]
        if missing:
            raise ValueError(f"Missing required fields: {missing}")
        return True
