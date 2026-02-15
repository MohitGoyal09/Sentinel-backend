# Orchestrator Package
# Multi-agent orchestration system for AlgoQuest

__version__ = "1.0.0"

# Core components
from app.orchestrator.base import BaseAgent
from app.orchestrator.registry import AgentRegistry, get_registry
from app.orchestrator.dispatcher import OrchestrationDispatcher
from app.orchestrator.aggregator import ResultAggregator, AggregationStrategy
from app.orchestrator.router import router, OrchestrationPayload, OrchestrationResult
from app.orchestrator.agents import AGENT_CLASSES

__all__ = [
    # Version
    "__version__",
    # Base classes
    "BaseAgent",
    # Registry
    "AgentRegistry",
    "get_registry",
    # Dispatcher
    "OrchestrationDispatcher",
    # Aggregator
    "ResultAggregator",
    "AggregationStrategy",
    # Router
    "router",
    "OrchestrationPayload",
    "OrchestrationResult",
    # Agents
    "AGENT_CLASSES",
]


def register_all_agents() -> AgentRegistry:
    """
    Convenience function to register all agents.
    
    Returns:
        AgentRegistry instance with all agents registered
    """
    from app.orchestrator.agents import AGENT_CLASSES
    
    registry = get_registry()
    
    for agent_class in AGENT_CLASSES:
        agent = agent_class()
        registry.register(agent)
    
    return registry
