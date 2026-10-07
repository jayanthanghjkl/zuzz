"""Background Agents - agent registry."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class AgentStatus(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    DEPLOYING = "deploying"


@dataclass
class Agent:
    """A deployed background agent."""
    agent_id: str
    name: str
    agent_type: str
    status: AgentStatus = AgentStatus.DEPLOYING
    config: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_run: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    schedule: Optional[str] = None
    colab_url: Optional[str] = None


class AgentRegistry:
    """Registry of deployed background agents."""

    def __init__(self) -> None:
        self._agents: Dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        """Register a new agent."""
        self._agents[agent.agent_id] = agent

    def unregister(self, agent_id: str) -> None:
        """Remove an agent."""
        self._agents.pop(agent_id, None)

    def get(self, agent_id: str) -> Optional[Agent]:
        """Get an agent by ID."""
        return self._agents.get(agent_id)

    def list_agents(self) -> List[Agent]:
        """Return all registered agents."""
        return list(self._agents.values())

    def update_status(self, agent_id: str, status: AgentStatus) -> None:
        """Update an agent's status."""
        agent = self._agents.get(agent_id)
        if agent:
            agent.status = status
            if status == AgentStatus.RUNNING:
                agent.last_run = time.time()

    def set_result(self, agent_id: str, result: Dict[str, Any]) -> None:
        """Set the latest result for an agent."""
        agent = self._agents.get(agent_id)
        if agent:
            agent.result = result
            agent.last_run = time.time()
