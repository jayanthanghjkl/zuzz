"""Mirror Neurons — turn an observation into a reusable macro.

A pattern is a sequence of acts that repeated. A macro is that sequence, turned
into a routine the Basal Ganglia can store and run. This module is the bridge
between "I watched you do it five times" and "say the word and it does it
again".

The conversion is mechanical, but two things are checked first:

* every step names a tool ZUZZ actually has, and its parameters validate
  against that tool's schema — a macro that cannot run is a bad answer to
  "make this automatic", so the failure is raised here, at the moment the
  user is being asked to approve it, rather than at save time in the Basal
  Ganglia;
* the step count is capped. A pattern that captured fifty acts is probably a
  session, not a workflow, and a routine with fifty steps is one confirmation
  away from a very long afternoon.

The macro is *not* armed. Arming is a separate act the user takes from the
Agents page. This module builds the routine object and hands it back; the
caller decides whether to store it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from brain.mirror_neurons.exceptions import MacroCreationRefused
from brain.mirror_neurons.pattern_cloner import Macro, clone_pattern

__all__ = ["generate_macro", "MAX_MACRO_STEPS"]


#: A routine with more steps than this is probably not a workflow.
MAX_MACRO_STEPS = 12


def generate_macro(
    pattern: Any,
    name: Optional[str] = None,
    description: str = "",
) -> Macro:
    """Build a macro from a detected pattern.

    Args:
        pattern: A :class:`~brain.mirror_neurons.pattern_cloner.Pattern`.
        name: What to call the routine. Derived from the pattern when omitted.
        description: One line, for the UI. Derived when omitted.

    Returns:
        The macro, not stored anywhere.

    Raises:
        MacroCreationRefused: When the pattern has too many steps, or one of
            its steps names a tool ZUZZ does not have or passes parameters
            that tool will not accept.
    """
    macro = clone_pattern(pattern, name=name, description=description)
    if not macro.steps:
        raise MacroCreationRefused(
            "this pattern has no steps to turn into a macro"
        )
    if len(macro.steps) > MAX_MACRO_STEPS:
        raise MacroCreationRefused(
            f"a macro may hold at most {MAX_MACRO_STEPS} steps; this pattern "
            f"captured {len(macro.steps)}, which is a session rather than a workflow"
        )
    _check_steps(macro)
    return macro


def _check_steps(macro: Macro) -> None:
    """Refuse a macro whose steps name tools ZUZZ cannot call.

    The check is the cheap half — the tool exists and its schema accepts the
    parameters. Permission is deliberately not consulted: a step that is not
    permitted at the current trust level is a normal state for a routine and
    is exactly what the Basal Ganglia's plan exists to report.

    Args:
        macro: The macro to validate.

    Raises:
        MacroCreationRefused: When a step names an unknown tool or carries
            parameters its schema will not accept.
    """
    from body.tools.registry import get_registry
    from brain.cerebrum.tools_validate import ToolParamError, validate

    registry = get_registry()
    for index, step in enumerate(macro.steps):
        if registry.get(step.tool) is None:
            raise MacroCreationRefused(
                f"step {index + 1} calls {step.tool!r}, which is not a tool ZUZZ has"
            )
        try:
            validate(step.tool, step.params)
        except ToolParamError as exc:
            raise MacroCreationRefused(
                f"step {index + 1} has parameters {step.tool!r} will not accept: {exc}"
            ) from exc