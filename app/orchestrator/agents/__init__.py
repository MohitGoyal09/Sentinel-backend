# Orchestrator Agents Package
# Exports all agent wrappers

from app.orchestrator.agents.safety_valve import SafetyValveAgent
from app.orchestrator.agents.talent_scout import TalentScoutAgent
from app.orchestrator.agents.culture_thermometer import CultureThermometerAgent
from app.orchestrator.agents.llm_context import LLMContextAgent
from app.orchestrator.agents.nudge_dispatcher import NudgeDispatcherAgent

__all__ = [
    "SafetyValveAgent",
    "TalentScoutAgent",
    "CultureThermometerAgent",
    "LLMContextAgent",
    "NudgeDispatcherAgent",
]

# Agent registry - auto-register all agents
AGENT_CLASSES = [
    SafetyValveAgent,
    TalentScoutAgent,
    CultureThermometerAgent,
    LLMContextAgent,
    NudgeDispatcherAgent,
]
