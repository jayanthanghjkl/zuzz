"""Multi-Agent Orchestrator — native Cerebrum/LiteLLM-based agent teams.

This package provides a pure-Python multi-agent system built on ZUZZ's
Cerebrum and LiteLLM, with no external CrewAI or LangChain dependencies.

The orchestrator decomposes complex prompts into specialist subtasks,
composes teams of agents, coordinates their execution with dependency
awareness, and aggregates their outputs into a unified response.

See :mod:`multi_agent.orchestrator` for the main entry point.
"""

from multi_agent.orchestrator import MultiAgentOrchestrator
from multi_agent.task_decomposer import TaskDecomposer, SubTask, ROLE_KEYWORDS
from multi_agent.team_builder import TeamBuilder, Team, ROLE_TO_AGENT_CLASS
from multi_agent.agent_coordinator import AgentCoordinator, SessionState, ExecutionResult
from multi_agent.result_aggregator import ResultAggregator, AggregatedResult

__all__ = [
    "MultiAgentOrchestrator",
    "TaskDecomposer",
    "SubTask",
    "ROLE_KEYWORDS",
    "TeamBuilder",
    "Team",
    "ROLE_TO_AGENT_CLASS",
    "AgentCoordinator",
    "SessionState",
    "ExecutionResult",
    "ResultAggregator",
    "AggregatedResult",
]