# Agent Registry
# Manages agent registration and lookup

from typing import Dict, List, Optional
from app.orchestrator.base import BaseAgent
import structlog

log = structlog.get_logger(__name__)


class AgentRegistry:
    """
    Singleton registry for managing agent instances.
    
    Provides:
    - Agent registration
    - Agent lookup by ID
    - Listing all agents
    - Agent health/status tracking
    """
    
    _instance: "AgentRegistry" = None
    _agents: Dict[str, BaseAgent] = {}
    _agent_configs: Dict[str, Dict] = {}
    _agent_stats: Dict[str, Dict] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def register(
        cls,
        agent: BaseAgent,
        config: Optional[Dict] = None
    ) -> None:
        """
        Register an agent with the registry.
        
        Args:
            agent: BaseAgent instance
            config: Optional agent configuration
        """
        agent_id = agent.agent_id
        
        if agent_id in cls._agents:
            log.warning(
                "agent.reregister",
                agent_id=agent_id,
                message="Agent already registered, overwriting"
            )
        
        cls._agents[agent_id] = agent
        
        # Default configuration
        default_config = {
            "status": "online",
            "max_concurrent_tasks": 10,
            "timeout_seconds": 30,
            "retry_count": 3
        }
        if config:
            default_config.update(config)
        
        cls._agent_configs[agent_id] = default_config
        
        # Initialize stats
        cls._agent_stats[agent_id] = {
            "total_executions": 0,
            "successful_executions": 0,
            "failed_executions": 0,
            "total_execution_time_ms": 0
        }
        
        log.info(
            "agent.registered",
            agent_id=agent_id,
            name=agent.name,
            capabilities=agent.get_capabilities()
        )
    
    @classmethod
    def get(cls, agent_id: str) -> Optional[BaseAgent]:
        """
        Get an agent by ID.
        
        Args:
            agent_id: The agent's unique identifier
            
        Returns:
            BaseAgent instance or None if not found
        """
        return cls._agents.get(agent_id)
    
    @classmethod
    def get_config(cls, agent_id: str) -> Optional[Dict]:
        """
        Get configuration for an agent.
        
        Args:
            agent_id: The agent's unique identifier
            
        Returns:
            Configuration dict or None
        """
        return cls._agent_configs.get(agent_id)
    
    @classmethod
    def get_stats(cls, agent_id: str) -> Optional[Dict]:
        """
        Get execution statistics for an agent.
        
        Args:
            agent_id: The agent's unique identifier
            
        Returns:
            Statistics dict or None
        """
        return cls._agent_stats.get(agent_id)
    
    @classmethod
    def update_stats(
        cls,
        agent_id: str,
        success: bool,
        execution_time_ms: int
    ) -> None:
        """Update execution statistics for an agent."""
        if agent_id not in cls._agent_stats:
            return
        
        stats = cls._agent_stats[agent_id]
        stats["total_executions"] += 1
        stats["total_execution_time_ms"] += execution_time_ms
        
        if success:
            stats["successful_executions"] += 1
        else:
            stats["failed_executions"] += 1
    
    @classmethod
    def list_all(cls) -> List[Dict]:
        """
        List all registered agents.
        
        Returns:
            List of agent information dicts
        """
        return [
            {
                "agent_id": agent_id,
                "name": agent.name,
                "agent_type": getattr(agent, "agent_type", "unknown"),
                "status": cls._agent_configs.get(agent_id, {}).get("status", "unknown"),
                "capabilities": agent.get_capabilities(),
                "config": cls._agent_configs.get(agent_id, {}),
                "stats": cls._agent_stats.get(agent_id, {})
            }
            for agent_id, agent in cls._agents.items()
        ]
    
    @classmethod
    def exists(cls, agent_id: str) -> bool:
        """
        Check if an agent is registered.
        
        Args:
            agent_id: The agent's unique identifier
            
        Returns:
            True if agent exists
        """
        return agent_id in cls._agents
    
    @classmethod
    def get_online_agents(cls) -> List[str]:
        """Get list of all online agent IDs."""
        return [
            agent_id
            for agent_id, config in cls._agent_configs.items()
            if config.get("status") == "online"
        ]
    
    @classmethod
    def set_status(cls, agent_id: str, status: str) -> bool:
        """
        Set the status for an agent.
        
        Args:
            agent_id: The agent's unique identifier
            status: New status (online, offline, busy)
            
        Returns:
            True if successful
        """
        if agent_id not in cls._agent_configs:
            return False
        
        cls._agent_configs[agent_id]["status"] = status
        return True
    
    @classmethod
    def clear(cls) -> None:
        """Clear all registered agents (for testing)."""
        cls._agents.clear()
        cls._agent_configs.clear()
        cls._agent_stats.clear()
        log.info("agent_registry.cleared")


def get_registry() -> AgentRegistry:
    """
    Get the singleton AgentRegistry instance.
    
    Returns:
        The singleton AgentRegistry instance
    """
    return AgentRegistry()
