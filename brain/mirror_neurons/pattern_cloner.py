"""Mirror Neurons — the shape of a pattern and the macro it becomes.

A :class:`Pattern` is what the learner finds. A :class:`Macro` is what the
generator builds. Keeping them apart is the point: a pattern is an observation
about what happened, and a macro is a thing ZUZZ can store and run. The
conversion is in :func:`clone_pattern`, and it is the only place the two touch.

A pattern's steps are raw records — tool, target, context, timestamp. A macro's
steps are routine steps — tool, params, why — the shape the Basal Ganglia
stores. The conversion is mostly renaming, but it is also where a pattern
stops being a transcript and becomes a plan: the timestamps go, the target
becomes a parameter, and a ``why`` is added saying where the step came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

__all__ = ["Pattern", "Macro", "RoutineStep", "clone_pattern"]


@dataclass
class Pattern:
    """A sequence of acts that repeated.

    Attributes:
        id: Stable identifier, for the API and the UI.
        steps: The acts, oldest first. Each is a recorder record.
        occurrences: How many times the sequence appeared.
        first_seen: Timestamp of the first occurrence.
        last_seen: Timestamp of the most recent one.
        context: The context the user was in, when they were consistent.
    """

    id: str
    steps: List[Dict[str, Any]]
    occurrences: int = 1
    first_seen: float = 0.0
    last_seen: float = 0.0
    context: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form."""
        return {
            "id": self.id,
            "steps": [dict(step) for step in self.steps],
            "occurrences": self.occurrences,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "context": self.context,
        }


@dataclass
class RoutineStep:
    """One step of a macro, in the shape the Basal Ganglia stores.

    Attributes:
        tool: An existing tool name.
        params: That tool's parameters.
        why: What this step is for.
    """

    tool: str
    params: Dict[str, Any] = field(default_factory=dict)
    why: str = ""


@dataclass
class Macro:
    """A routine built from a pattern, ready to store.

    Attributes:
        name: The routine name.
        description: One line describing what it does.
        steps: The steps, in order.
        source: Always ``"mirror"`` — where it came from.
        pattern_id: The pattern this was built from.
    """

    name: str
    description: str
    steps: List[RoutineStep]
    source: str = "mirror"
    pattern_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form."""
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "pattern_id": self.pattern_id,
            "steps": [
                {"tool": step.tool, "params": dict(step.params), "why": step.why}
                for step in self.steps
            ],
        }


def _derive_name(pattern: Pattern) -> str:
    """Build a routine name from a pattern.

    The first tool in the sequence is the heart of the workflow — a sequence
    that starts with ``file_write`` is a writing workflow, and the name should
    say so. Lowercased and underscored, which is what the Basal Ganglia's
    ``validate_name`` expects.
    """
    if not pattern.steps:
        return "mirror_workflow"
    first = str(pattern.steps[0].get("tool", "workflow")).lower()
    return "mirror_" + first.replace(" ", "_")


def _derive_description(pattern: Pattern) -> str:
    """Build a one-line description of what the macro does."""
    if not pattern.steps:
        return "A workflow learned by watching"
    tools = [str(step.get("tool", "")) for step in pattern.steps]
    tools = [tool for tool in tools if tool]
    if not tools:
        return "A workflow learned by watching"
    if len(tools) == 1:
        return f"Run {tools[0]} repeatedly, as seen in {pattern.occurrences} sessions"
    return (
        f"A workflow using {', '.join(tools[:4])}"
        + ("..." if len(tools) > 4 else "")
        + f", seen {pattern.occurrences} times"
    )


def clone_pattern(
    pattern: Pattern,
    name: Optional[str] = None,
    description: str = "",
) -> Macro:
    """Turn a pattern into a macro.

    The conversion is mechanical on purpose: the steps are the same acts, in
    the same order, with the timestamps dropped and a ``why`` added naming
    where the step came from. Nothing is generalised and nothing is invented —
    the macro does exactly what was watched, which is the whole point.

    Args:
        pattern: The pattern to clone.
        name: What to call the routine. Derived when omitted.
        description: One line. Derived when omitted.

    Returns:
        The macro.
    """
    steps: List[RoutineStep] = []
    for index, raw in enumerate(pattern.steps):
        tool = str(raw.get("tool", "") or "").strip()
        target = str(raw.get("target", "") or "").strip()
        steps.append(
            RoutineStep(
                tool=tool,
                params={"text": target} if tool == "keyboard_type" and target else {},
                why=f"step {index + 1}, seen {pattern.occurrences} times",
            )
        )
    return Macro(
        name=name or _derive_name(pattern),
        description=description or _derive_description(pattern),
        steps=steps,
        pattern_id=pattern.id,
    )