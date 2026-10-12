"""Multi-Agent Orchestrator — coordinate agent execution.

The :class:`AgentCoordinator` takes a team and a list of subtasks and runs
them, respecting dependencies. Independent subtasks run in parallel; dependent
ones wait for their prerequisites. Context is passed between agents so later
ones can use earlier outputs.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from multi_agent.task_decomposer import SubTask
from multi_agent.team_builder import Team

__all__ = [
    "AgentCoordinator",
    "ExecutionResult",
    "SessionState",
]


@dataclass
class ExecutionResult:
    """The result of running one subtask.

    Attributes:
        role: The specialist role that ran.
        instruction: What was asked.
        output: The agent's response.
        error: Any error that occurred.
        context_updates: Key-value pairs to merge into the shared context.
    """

    role: str
    instruction: str
    output: Any = None
    error: Optional[str] = None
    context_updates: Dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        """Return whether the task completed without error."""
        return self.error is None

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form."""
        return {
            "role": self.role,
            "instruction": self.instruction,
            "output": self.output,
            "error": self.error,
            "context_updates": self.context_updates,
        }


@dataclass
class SessionState:
    """The running state of a multi-agent session.

    Attributes:
        session_id: Stable identifier.
        team: The team executing the task.
        subtasks: The full list of subtasks.
        results: Completed results, in order of completion.
        context: Shared context accumulated during execution.
        status: One of "pending", "running", "completed", "failed", "stopped".
        error: Any fatal error that stopped the session.
    """

    session_id: str
    team: Team
    subtasks: List[SubTask]
    results: List[ExecutionResult] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form (without the team object)."""
        return {
            "session_id": self.session_id,
            "team_name": self.team.name,
            "team_roles": self.team.roles(),
            "subtask_count": len(self.subtasks),
            "completed_count": len(self.results),
            "context": self.context,
            "status": self.status,
            "error": self.error,
        }


class AgentCoordinator:
    """Run subtasks on a team, respecting dependencies."""

    def __init__(self) -> None:
        self._sessions: Dict[str, SessionState] = {}

    def create_session(
        self,
        session_id: str,
        team: Team,
        subtasks: List[SubTask],
        initial_context: Optional[Dict[str, Any]] = None,
    ) -> SessionState:
        """Register a new session."""
        state = SessionState(
            session_id=session_id,
            team=team,
            subtasks=subtasks,
            context=initial_context or {},
            status="pending",
        )
        self._sessions[session_id] = state
        return state

    def get_session(self, session_id: str) -> Optional[SessionState]:
        """Return the session state, or ``None`` if not found."""
        return self._sessions.get(session_id)

    def remove_session(self, session_id: str) -> bool:
        """Remove a session and return whether it existed."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    async def execute_session(self, session_id: str) -> SessionState:
        """Run all subtasks in a session.

        Subtasks with no unmet dependencies run in parallel. As each completes,
        its ``context_updates`` are merged into the shared context, potentially
        unblocking dependent tasks.

        Args:
            session_id: The session to run.

        Returns:
            The final session state.
        """
        state = self._sessions.get(session_id)
        if state is None:
            raise ValueError(f"session {session_id!r} not found")

        state.status = "running"
        pending: Set[int] = set(range(len(state.subtasks)))
        completed: Set[int] = set()

        while pending:
            # Find tasks whose dependencies are all satisfied
            ready = [
                i for i in pending
                if all(dep in completed for dep in self._dep_indices(state, i))
            ]

            if not ready:
                # Circular dependency or bug — run what's left sequentially
                ready = [min(pending)]

            # Run ready tasks in parallel
            await asyncio.gather(*[
                self._run_subtask(state, i) for i in ready
            ])

            for i in ready:
                pending.remove(i)
                completed.add(i)

        state.status = "completed" if all(r.succeeded for r in state.results) else "failed"
        return state

    def _dep_indices(self, state: SessionState, index: int) -> Set[int]:
        """Return the set of subtask indices that ``index`` depends on."""
        deps = state.subtasks[index].depends_on
        return {
            i for i, st in enumerate(state.subtasks)
            if st.role in deps
        }

    async def _run_subtask(self, state: SessionState, index: int) -> None:
        """Execute one subtask and record the result."""
        subtask = state.subtasks[index]
        agent = state.team.get_agent(subtask.role)

        if agent is None:
            result = ExecutionResult(
                role=subtask.role,
                instruction=subtask.instruction,
                error=f"no agent for role {subtask.role!r}",
            )
            state.results.append(result)
            return

        # Build context for this agent: shared context + subtask-specific context
        agent_context = {**state.context, **subtask.context}

        try:
            output = await agent.execute(subtask.instruction, agent_context)
            result = ExecutionResult(
                role=subtask.role,
                instruction=subtask.instruction,
                output=output,
                context_updates={subtask.role: output},
            )
        except Exception as e:
            result = ExecutionResult(
                role=subtask.role,
                instruction=subtask.instruction,
                error=str(e),
            )

        state.results.append(result)
        # Merge context updates for dependent tasks
        state.context.update(result.context_updates)

    def list_sessions(self) -> List[Dict[str, Any]]:
        """Return summary info for all sessions."""
        return [s.to_dict() for s in self._sessions.values()]