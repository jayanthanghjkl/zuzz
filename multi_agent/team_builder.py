"""Multi-Agent Orchestrator — compose a team of specialist agents.

A :class:`Team` is a named group of agents, each with a role. The
:class:`TeamBuilder` takes a list of required roles and returns a team with
the right agents instantiated. The builder is stateless and deterministic —
the same roles always produce the same team composition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from multi_agent.agents import (
    ResearcherAgent,
    WriterAgent,
    AnalystAgent,
    PlannerAgent,
    EditorAgent,
    TranslatorAgent,
    SummarizerAgent,
    FactCheckerAgent,
    BrainstormerAgent,
    SchedulerAgent,
)

__all__ = [
    "Team",
    "TeamBuilder",
    "ROLE_TO_AGENT_CLASS",
]


#: Map from role name to agent class. The keys must match the roles used by
#: :class:`~multi_agent.task_decomposer.TaskDecomposer`.
ROLE_TO_AGENT_CLASS: Dict[str, type] = {
    "researcher": ResearcherAgent,
    "writer": WriterAgent,
    "analyst": AnalystAgent,
    "planner": PlannerAgent,
    "editor": EditorAgent,
    "translator": TranslatorAgent,
    "summarizer": SummarizerAgent,
    "fact_checker": FactCheckerAgent,
    "brainstormer": BrainstormerAgent,
    "scheduler": SchedulerAgent,
}


@dataclass
class Team:
    """A composed team of specialist agents.

    Attributes:
        name: Human-readable name for the team.
        agents: Map from role to instantiated agent.
        metadata: Extra data about the team (created_at, prompt, etc.).
    """

    name: str
    agents: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def get_agent(self, role: str) -> Optional[Any]:
        """Return the agent for ``role``, or ``None`` if not in team."""
        return self.agents.get(role)

    def has_role(self, role: str) -> bool:
        """Return whether the team includes ``role``."""
        return role in self.agents

    def roles(self) -> List[str]:
        """Return the list of roles in this team."""
        return list(self.agents.keys())


class TeamBuilder:
    """Instantiate agents for the required roles.

    The builder is stateless — it does not keep references to teams it builds.
    Each call to :meth:`build` returns a fresh :class:`Team` with fresh agent
    instances.
    """

    def __init__(
        self,
        cerebrum: Any,
        role_to_class: Optional[Dict[str, type]] = None,
    ) -> None:
        """Build a team builder.

        Args:
            cerebrum: The Cerebrum instance to pass to every agent.
            role_to_class: Custom role-to-class map. Defaults to
                :data:`ROLE_TO_AGENT_CLASS`.
        """
        self.cerebrum = cerebrum
        self.role_to_class = role_to_class or ROLE_TO_AGENT_CLASS

    def build(
        self,
        roles: List[str],
        name: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Team:
        """Create a team with agents for ``roles``.

        Args:
            roles: The specialist roles to include.
            name: Team name. Generated from roles when omitted.
            metadata: Extra data to attach to the team.

        Returns:
            A :class:`Team` with instantiated agents.

        Raises:
            ValueError: When a role has no registered agent class.
        """
        agents: Dict[str, Any] = {}
        for role in roles:
            agent_class = self.role_to_class.get(role)
            if agent_class is None:
                raise ValueError(f"no agent class registered for role {role!r}")
            agents[role] = agent_class(self.cerebrum)

        if not name:
            name = "Team: " + ", ".join(roles)

        return Team(
            name=name,
            agents=agents,
            metadata=metadata or {},
        )

    def available_roles(self) -> List[str]:
        """Return the list of roles that can be composed."""
        return list(self.role_to_class.keys())