"""Body/Tools — machine telemetry.

One tool: read this machine's live resource usage.

**It is gated, unlike the dashboard.** ``/api/resources/*`` is deliberately
ungated — a gauge that needed a permission would be a gauge nobody looked at,
and reading your own hardware is not a side effect. A chat-driven tool call is a
different act, though: the model chose to ask, on the strength of a sentence
from a user, in the same message that may also be asking for something else.
It therefore goes through the gate as ``read_files``, which auto-approves from
trust level 2 and asks at level 1.

That asymmetry is a choice, not an oversight. Trust level 1 means "ask permission
for everything", and a tool call that fires without the user being told which
tool fired is exactly what level 1 exists to prevent. At level 2 — "auto-approve
read-only actions" — it is silent, like the dashboard.

The figures come from the Hypothalamus, which is the only component that reads
the sensors, so a chat answer and the dashboard can never disagree.
"""

from __future__ import annotations

from typing import Any, Dict

from brain.prefrontal_cortex.action import ActionCategory
from body.tools.tool_spec import ToolContext, ToolRefused, ToolSpec, ToolTarget


#: The sensor keys reported back to the model. :meth:`Hypothalamus.snapshot`
#: returns fourteen keys, several of which are the same numbers the region-wide
#: status repeats at greater length. A model asked "how is my machine doing"
#: needs the readings; it does not need the resource budget arithmetic the
#: Hypothalamus runs for its own scheduling decisions, and returning it would put
#: several hundred tokens of internal accounting into every chat answer.
REPORTED_SENSORS = ("ram", "vram", "cpu", "gpu", "disk")


def _resolve_resources(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve the resource read into a telemetry category."""
    if context.hypothalamus is None:
        raise ToolRefused(
            "the resource layer is not attached, so machine telemetry is "
            "not available to read"
        )
    return ToolTarget(
        category=str(ActionCategory.READ_FILES),
        target="system_resources",
        preview={"description": "Read this machine's live resource usage"},
    )


def _execute_resources(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Return one reading per sensor, plus the region-wide status.

    ``Hypothalamus.snapshot`` and ``get_status`` are the real methods, verified
    against the class rather than assumed: both take no arguments and both are
    safe to call before ``start_polling``, so a chat answer and the dashboard
    read the same figures even when the one-second cycle has not run yet.
    """
    brain = context.hypothalamus
    snapshot = brain.snapshot()
    readings = {key: snapshot.get(key) for key in REPORTED_SENSORS}
    return {
        "readings": readings,
        "thermal_level": snapshot.get("thermal_level"),
        "cloud_only": snapshot.get("cloud_only"),
        "status": brain.get_status(),
    }


#: The machine telemetry tools.
SYSTEM_TOOLS: tuple = (
    ToolSpec(
        name="get_system_resources",
        summary="Read RAM, VRAM, CPU, GPU and disk usage",
        resolve=_resolve_resources,
        execute=_execute_resources,
        returns="Live sensor readings and what could not be measured",
    ),
)
