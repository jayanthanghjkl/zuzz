"""Basal Ganglia — routine building.

Turns a detected pattern into a runnable routine, and holds the three seed
routines the project ships with.

**A step names an existing tool.** :class:`RoutineStep` carries a tool name and
parameters, never a bespoke action. That means a routine is executed through
exactly the same resolve-then-gate-then-execute pipeline as a tool call the model
asked for, and there is no second path into the Body with its own idea of what
needs checking. The alternative — routines holding their own action handlers —
would mean a routine could open an application in a way no gated tool call
could, which is the whole problem this architecture exists to prevent.

**A routine stores no permission.** :class:`Routine` records what to do and when,
never whether it may. Every step is re-resolved and re-decided against the trust
matrix at the moment the routine runs, so a routine created at level 5 and
executed at level 1 does only what level 1 permits. See
``config/trust_levels.json``'s ``routine_note``.

**Building from a pattern keeps the evidence.** A routine built from a pattern
records the signature and the repetition count it came from, so a user reading the
routines list can see that "start_work" is not something ZUZZ decided — it is
something it watched happen six times.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from brain.basal_ganglia.context_triggers import MANUAL, Trigger, parse
from brain.basal_ganglia.exceptions import RoutineNameInvalid, StepRefused
from brain.basal_ganglia.pattern_detector import Pattern

__all__ = [
    "MAX_ROUTINE_NAME",
    "MAX_STEPS",
    "Routine",
    "RoutineStep",
    "SEED_ROUTINES",
    "build_from_pattern",
    "suggest_name",
    "validate_name",
]

#: A routine's name must fit in a CLI argument and a URL path segment.
MAX_ROUTINE_NAME = 40

#: How many steps a routine may hold. Bounded because a routine is a stored
#: sequence of acts on the user's own desktop, and a hundred-step routine is a
#: script nobody reads before running.
MAX_STEPS = 12

#: Characters allowed in a name. Lowercase identifier shape only: a name reaches
#: a CLI argument, a URL path segment and a JSON key, and anything else has to be
#: escaped in at least one of them.
_ALLOWED_NAME = set("abcdefghijklmnopqrstuvwxyz0123456789_")


@dataclass(frozen=True)
class RoutineStep:
    """One action inside a routine.

    Attributes:
        tool: The registered tool name to call.
        params: Its parameters. Validated against the tool's schema before the
            routine is stored, so a routine cannot be saved that will be refused
            every time it runs.
        category: The trust category the step was decided as, for display.
        target: What the step acts on, for display and for the gate's target.
        why: What this step is for, in one line, so the user can read the routine
            rather than infer it.
    """

    tool: str
    params: Dict[str, Any] = field(default_factory=dict)
    category: str = ""
    target: str = ""
    why: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form."""
        return {
            "tool": self.tool,
            "params": dict(self.params),
            "category": self.category,
            "target": self.target,
            "why": self.why,
        }


@dataclass(frozen=True)
class Routine:
    """A named sequence of tool calls with a trigger.

    Attributes:
        name: The identifier. Lowercase, digits and underscores.
        description: One line a user reads.
        trigger: When it fires.
        steps: What it does.
        enabled: Whether it may run. Separate from being deleted, so a routine
            can be parked without being retyped. Defaults to ``False``, and that
            default is the safe direction on purpose: a routine built here is a
            proposal that nobody has read yet, and a routine that runs unattended
            has to be armed by the person who understands what it does.
        source: ``seed``, ``pattern`` or ``user`` — how it came to exist.
        evidence: The pattern signature and repetition count, when built from an
            observed habit.
        created: Unix timestamp.
        updated: Unix timestamp of the last change.
        times_run: How many times it has completed.
        last_run: Unix timestamp of the last completion, or 0.0.
    """

    name: str
    description: str = ""
    trigger: Trigger = field(default_factory=Trigger)
    steps: Tuple[RoutineStep, ...] = ()
    enabled: bool = False
    source: str = "user"
    evidence: Dict[str, Any] = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    times_run: int = 0
    last_run: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form, including the trigger's description."""
        return {
            "name": self.name,
            "description": self.description,
            "trigger": self.trigger.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
            "step_count": len(self.steps),
            "enabled": self.enabled,
            "source": self.source,
            "evidence": dict(self.evidence),
            "created": self.created,
            "updated": self.updated,
            "times_run": self.times_run,
            "last_run": self.last_run,
        }


def validate_name(name: Any) -> str:
    """Return a routine name, or refuse one.

    Args:
        name: The candidate.

    Returns:
        The stripped, lowercased name.

    Raises:
        RoutineNameInvalid: When it is empty, too long, or uses a character that
            would need quoting in a CLI argument, a URL or a JSON key. Refused
            rather than sanitised: silently rewriting a name would leave the user
            unable to find the routine they just created.
    """
    text = str(name if name is not None else "").strip().lower()
    if not text:
        raise RoutineNameInvalid("a routine needs a name")
    if len(text) > MAX_ROUTINE_NAME:
        raise RoutineNameInvalid(
            f"a routine name may be at most {MAX_ROUTINE_NAME} characters"
        )
    bad = sorted(set(text) - _ALLOWED_NAME)
    if bad:
        raise RoutineNameInvalid(
            f"a routine name may use only lowercase letters, digits and "
            f"underscores — not: {''.join(bad)}"
        )
    return text


def suggest_name(pattern: Pattern) -> str:
    """Return a readable routine name for a detected pattern.

    Args:
        pattern: The observed sequence.

    Returns:
        A valid name derived from the tools involved, e.g. ``open_then_browse``.
        Always valid, because the tool names it draws from are already
        identifier-shaped.
    """
    verbs: List[str] = []
    for step in pattern.steps:
        head = re.sub(r"[^a-z0-9]+", "_", step.tool.lower()).strip("_")
        if head and head not in verbs:
            verbs.append(head)
    joined = "_then_".join(verbs[:3]) or "routine"
    return validate_name(joined[:MAX_ROUTINE_NAME])


def build_from_pattern(
    pattern: Pattern,
    name: Optional[str] = None,
    trigger: Any = None,
) -> Routine:
    """Turn a detected pattern into a routine.

    Args:
        pattern: The observed sequence.
        name: The routine's name. Derived from the pattern when omitted.
        trigger: When it should fire. Manual when omitted — a pattern that has
            happened six times says nothing about when the user would want it
            done automatically, and defaulting to automatic would arm a habit
            nobody agreed to arm.

    Returns:
        The routine, ready to store.

    Raises:
        StepRefused: When a step's tool is not something ZUZZ can call, or the
            sequence is empty or too long.
        RoutineNameInvalid: When the name is unusable.
    """
    if not pattern.steps:
        raise StepRefused("a pattern with no steps cannot become a routine")
    if len(pattern.steps) > MAX_STEPS:
        raise StepRefused(
            f"a routine may hold at most {MAX_STEPS} steps; this pattern has "
            f"{len(pattern.steps)}"
        )

    steps: List[RoutineStep] = []
    for observed in pattern.steps:
        params = _params_for(observed.tool, observed.target)
        steps.append(
            RoutineStep(
                tool=observed.tool,
                params=params,
                category=observed.category,
                target=observed.target,
                why=f"as observed {pattern.repetitions} times",
            )
        )

    resolved = trigger if isinstance(trigger, Trigger) else parse(trigger or MANUAL)
    return Routine(
        name=validate_name(name or suggest_name(pattern)),
        description=(
            f"Built from a pattern seen {pattern.repetitions} time"
            f"{'s' if pattern.repetitions != 1 else ''}"
        ),
        trigger=resolved,
        steps=tuple(steps),
        source="pattern",
        evidence={
            "signature": pattern.signature,
            "repetitions": pattern.repetitions,
            "sessions": pattern.sessions,
            "confidence": pattern.confidence,
        },
    )


#: How an observed target is mapped back onto a tool's parameters. The key is the
#: recorded tool name; the value names the parameter the target belongs in.
#:
#: Only tools whose parameter is recoverable from the activity log are here. A
#: tool that is not listed still produces a step, with no parameters — which the
#: schema validator will then refuse unless the tool needs none. That is the right
#: direction to fail: the user sees that a routine cannot be built from this
#: pattern, rather than ZUZZ inventing a parameter value.
_PARAM_NAMES: Dict[str, str] = {
    "open_app": "app_name",
    "close_app": "process_name",
    "browser_navigate": "url",
    "browser_search_web": "query",
}


def _params_for(tool: str, target: str) -> Dict[str, Any]:
    """Return the parameters that reproduce one observed action.

    Args:
        tool: The recorded tool name.
        target: The recorded target, e.g. ``vscode.exe`` or a URL.

    Returns:
        A parameter mapping, empty when the target cannot be mapped back.
    """
    key = _PARAM_NAMES.get(str(tool))
    if key is None or not target:
        return {}
    value = str(target)
    # The log records the resolved executable, because the gate judges the
    # executable rather than the alias. A routine wants the name a user would type.
    if key == "app_name" and "." in value:
        value = value.rsplit(".", 1)[0]
    return {key: value}


#: The routines ZUZZ ships with.
#:
#: Seeded rather than detected on purpose: these are the ones the project asked
#: for by name, and waiting for the activity log to prove them would leave the
#: feature looking empty on a fresh install. All three are ``enabled: false`` and
#: ``manual``, for the same reason — a routine that runs unattended has to be
#: switched on by the person who understands what it does.
#:
#: Every step names a tool that exists in
#: :mod:`brain.cerebrum.tools_schema`. That is checked by the tests rather than
#: assumed, because a seed routine naming a tool that does not exist is a routine
#: that can never run and a ``StepRefused`` the first time somebody tries. The
#: steps are therefore also scoped to what the Body can actually do today: there
#: is no ``focus_window`` tool, no git tool and no way to toggle a system
#: notification setting, so "save progress" and "enable do-not-disturb" are not
#: steps here. They would be invented tool names, and an invented tool name in a
#: routine is worse than a missing feature, because it looks implemented.
SEED_ROUTINES: Tuple[Dict[str, Any], ...] = (
    {
        "name": "start_work",
        "description": "Open the editor, the browser and the work tracker.",
        "trigger": {"kind": "manual"},
        "enabled": False,
        "source": "seed",
        "steps": [
            {"tool": "open_app", "params": {"app_name": "vscode"}, "why": "the editor"},
            {"tool": "open_app", "params": {"app_name": "chrome"}, "why": "the browser"},
            {
                "tool": "browser_navigate",
                "params": {"url": "https://github.com"},
                "why": "the work",
            },
        ],
    },
    {
        "name": "end_day",
        "description": "Close the browser and the applications left open.",
        "trigger": {"kind": "manual"},
        "enabled": False,
        "source": "seed",
        "steps": [
            {"tool": "browser_close", "params": {}, "why": "the browser away first"},
            {"tool": "close_app", "params": {"process_name": "chrome"}, "why": "then the app"},
        ],
    },
    {
        "name": "focus_mode",
        "description": "Close the obvious distraction and get back to the editor.",
        "trigger": {"kind": "manual"},
        "enabled": False,
        "source": "seed",
        "steps": [
            {"tool": "close_app", "params": {"process_name": "chrome"}, "why": "browser away"},
            {"tool": "open_app", "params": {"app_name": "vscode"}, "why": "editor back"},
        ],
    },
)


def seed_routines() -> List[Routine]:
    """Return the shipped routines as :class:`Routine` objects.

    Returns:
        Three routines, all disabled and manual. Seeded on an install that already
        has routines of the same name are not overwritten — the user changed them.
    """
    built: List[Routine] = []
    for entry in SEED_ROUTINES:
        built.append(
            Routine(
                name=validate_name(entry["name"]),
                description=str(entry["description"]),
                trigger=parse(entry["trigger"]),
                steps=tuple(
                    RoutineStep(
                        tool=str(step["tool"]),
                        params=dict(step.get("params", {})),
                        why=str(step.get("why", "")),
                    )
                    for step in entry["steps"]
                ),
                enabled=bool(entry.get("enabled", False)),
                source=str(entry.get("source", "seed")),
            )
        )
    return built