"""Multi-Agent Orchestrator — main entry point.

The :class:`MultiAgentOrchestrator` is the single object the tools and routes
use. It wires together the decomposer, builder, coordinator, and aggregator
into a cohesive workflow:

1. ``execute_complex_task`` — decompose, build team, run, aggregate
2. ``deploy_team`` — build and run a team for a list of roles
3. ``get_session_status`` — check a running or completed session
4. ``stop_session`` — cancel a running session

The orchestrator is stateless across sessions but holds session state in
memory while they run.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from brain.cerebrum.cerebrum import Cerebrum

from multi_agent.task_decomposer import TaskDecomposer
from multi_agent.team_builder import TeamBuilder
from multi_agent.agent_coordinator import AgentCoordinator, SessionState
from multi_agent.result_aggregator import ResultAggregator, AggregatedResult

__all__ = [
    "MultiAgentOrchestrator",
]


class MultiAgentOrchestrator:
    """Coordinate multi-agent execution for complex tasks."""

    def __init__(
        self,
        cerebrum: Cerebrum,
        decomposer: Optional[TaskDecomposer] = None,
        builder: Optional[TeamBuilder] = None,
        coordinator: Optional[AgentCoordinator] = None,
        aggregator: Optional[ResultAggregator] = None,
    ) -> None:
        """Wire up the orchestrator.

        Args:
            cerebrum: The Cerebrum instance (passed to all agents).
            decomposer: Custom task decomposer. Defaults to a new instance.
            builder: Custom team builder. Defaults to a new instance.
            coordinator: Custom coordinator. Defaults to a new instance.
            aggregator: Custom result aggregator. Defaults to a new instance.
        """
        self.cerebrum = cerebrum
        self.decomposer = decomposer or TaskDecomposer()
        self.builder = builder or TeamBuilder(cerebrum)
        self.coordinator = coordinator or AgentCoordinator()
        self.aggregator = aggregator or ResultAggregator()

    async def execute_complex_task(
        self,
        prompt: str,
        context: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> AggregatedResult:
        """Decompose a prompt, deploy a team, run it, and aggregate results.

        This is the main "one call" entry point for complex prompts.

        Args:
            prompt: The user's complex request.
            context: Optional initial context (files, preferences, history).
            session_id: Optional stable ID. Generated when omitted.

        Returns:
            An :class:`~multi_agent.result_aggregator.AggregatedResult`.
        """
        session_id = session_id or f"ma_{uuid.uuid4().hex[:12]}"

        # 1. Decompose
        subtasks = self.decomposer.decompose(prompt, context)

        # 2. Build team with all required roles
        roles = [st.role for st in subtasks]
        team = self.builder.build(roles=roles, metadata={"prompt": prompt})

        # 3. Create and run session
        self.coordinator.create_session(session_id, team, subtasks, context)
        await self.coordinator.execute_session(session_id)

        # 4. Get final state and aggregate
        state = self.coordinator.get_session(session_id)
        if state is None:
            raise RuntimeError(f"session {session_id} vanished after execution")

        result = self.aggregator.aggregate(state)

        # 5. Clean up session (keep for status queries if needed)
        # self.coordinator.remove_session(session_id)

        return result

    async def deploy_team(
        self,
        roles: List[str],
        instructions: Dict[str, str],
        context: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> AggregatedResult:
        """Deploy a specific team with custom instructions per role.

        This bypasses the decomposer — the caller specifies exactly which
        roles and what each should do.

        Args:
            roles: The specialist roles to include.
            instructions: Map from role to its instruction.
            context: Optional shared context.
            session_id: Optional stable ID.

        Returns:
            An :class:`~multi_agent.result_aggregator.AggregatedResult`.
        """
        session_id = session_id or f"ma_{uuid.uuid4().hex[:12]}"

        from multi_agent.task_decomposer import SubTask

        subtasks = [
            SubTask(role=role, instruction=instructions.get(role, ""), context=context or {})
            for role in roles
        ]

        team = self.builder.build(roles=roles)
        self.coordinator.create_session(session_id, team, subtasks, context)
        await self.coordinator.execute_session(session_id)

        state = self.coordinator.get_session(session_id)
        if state is None:
            raise RuntimeError(f"session {session_id} vanished after execution")

        return self.aggregator.aggregate(state)

    def get_session_status(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Return the current status of a session.

        Args:
            session_id: The session to query.

        Returns:
            Session summary dict, or ``None`` if not found.
        """
        state = self.coordinator.get_session(session_id)
        if state is None:
            return None
        return state.to_dict()

    def stop_session(self, session_id: str) -> bool:
        """Stop a running session and clean up.

        Args:
            session_id: The session to stop.

        Returns:
            Whether the session existed and was removed.
        """
        state = self.coordinator.get_session(session_id)
        if state is not None:
            state.status = "stopped"
        return self.coordinator.remove_session(session_id)

    def list_sessions(self) -> List[Dict[str, Any]]:
        """Return summaries of all tracked sessions."""
        return self.coordinator.list_sessions()

    def get_available_roles(self) -> List[str]:
        """Return the list of specialist roles that can be deployed."""
        return self.builder.available_roles()