"""Multi-Agent Orchestrator — synthesize multiple agent outputs.

The :class:`ResultAggregator` takes the results from a completed session and
produces a unified response. It knows how to combine different specialist
outputs — research feeds writing, analysis feeds planning, editing refines
writing, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from multi_agent.agent_coordinator import ExecutionResult, SessionState

__all__ = [
    "AggregatedResult",
    "ResultAggregator",
]


@dataclass
class AggregatedResult:
    """The synthesized output of a multi-agent session.

    Attributes:
        session_id: The session this came from.
        primary_output: The main deliverable (usually from the last writer).
        supporting_outputs: Other agent outputs, keyed by role.
        summary: A human-readable summary of what was done.
        errors: Any errors that occurred during execution.
        metadata: Extra data (timing, token counts, etc.).
    """

    session_id: str
    primary_output: Any
    supporting_outputs: Dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    errors: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form."""
        return {
            "session_id": self.session_id,
            "primary_output": self.primary_output,
            "supporting_outputs": self.supporting_outputs,
            "summary": self.summary,
            "errors": self.errors,
            "metadata": self.metadata,
        }


class ResultAggregator:
    """Combine agent results into a unified response."""

    #: Roles whose output is typically the "primary" deliverable.
    PRIMARY_ROLES = ("writer", "planner", "analyst", "summarizer", "translator")

    #: Roles that modify another role's output.
    MODIFIER_ROLES = ("editor", "fact_checker")

    def aggregate(self, state: SessionState) -> AggregatedResult:
        """Synthesize results from a completed session.

        Args:
            state: The final session state from :class:`AgentCoordinator`.

        Returns:
            An :class:`AggregatedResult`.
        """
        results_by_role = {r.role: r for r in state.results}
        supporting: Dict[str, Any] = {}
        errors: List[str] = []

        for role, result in results_by_role.items():
            if result.error:
                errors.append(f"{role}: {result.error}")
            elif result.output is not None:
                supporting[role] = result.output

        # Pick primary output: prefer the last primary-role task that succeeded
        primary = None
        for role in reversed(self.PRIMARY_ROLES):
            if role in results_by_role and results_by_role[role].succeeded:
                primary = results_by_role[role].output
                break

        # If no primary role succeeded, fall back to any successful output
        if primary is None:
            for result in reversed(state.results):
                if result.succeeded and result.output is not None:
                    primary = result.output
                    break

        # Apply modifiers (editor, fact_checker) to primary if present
        if primary is not None:
            for modifier in self.MODIFIER_ROLES:
                if modifier in results_by_role and results_by_role[modifier].succeeded:
                    modifier_output = results_by_role[modifier].output
                    primary = self._apply_modifier(primary, modifier, modifier_output)

        summary = self._build_summary(state, results_by_role)

        return AggregatedResult(
            session_id=state.session_id,
            primary_output=primary,
            supporting_outputs=supporting,
            summary=summary,
            errors=errors,
            metadata={
                "subtask_count": len(state.subtasks),
                "completed_count": len([r for r in state.results if r.succeeded]),
                "failed_count": len([r for r in state.results if not r.succeeded]),
                "team_roles": state.team.roles(),
            },
        )

    def _apply_modifier(self, primary: Any, modifier_role: str, modifier_output: Any) -> Any:
        """Apply a modifier's output to the primary output.

        This is intentionally simple: if both are strings, the modifier output
        replaces the primary (editor rewrites, fact_checker corrects). For
        structured data, the modifier output is merged as a ``_review`` field.
        """
        if isinstance(primary, str) and isinstance(modifier_output, str):
            return modifier_output
        if isinstance(primary, dict):
            result = dict(primary)
            result[f"_{modifier_role}_review"] = modifier_output
            return result
        return primary

    def _build_summary(self, state: SessionState, results_by_role: Dict[str, ExecutionResult]) -> str:
        """Build a human-readable summary."""
        parts = [f"Team '{state.team.name}' completed {len(state.subtasks)} tasks."]
        successful = [r.role for r in state.results if r.succeeded]
        failed = [r.role for r in state.results if not r.succeeded]
        if successful:
            parts.append(f"Successful: {', '.join(successful)}.")
        if failed:
            parts.append(f"Failed: {', '.join(failed)}.")
        return " ".join(parts)