"""Body/Tools — routines.

Listing, creating, changing and planning the user's saved routines.

Every tool resolves to the ``routine_management`` trust category, which asks at
trust levels 1 and 2 and auto-approves from 3 — the same shape as
``mouse_keyboard``, because a routine is a stored bundle of desktop acts and the
user approved those acts individually. ``config/trust_levels.json``'s
``routine_note`` has the full argument.

**``routine_execute`` plans; it does not run.** This is the single most important
thing in the file. The tool resolves every step, asks the gate about each at the
current trust level, and returns what would happen — which steps are permitted,
which need the user to confirm, and which are forbidden outright. It never reaches
an executor.

The reason is that a routine's steps are separate acts with separate verdicts, and
collapsing them into one would either run them all on the strength of a single
decision the gate never gave, or stop halfway and leave the desktop half-changed.
Planning and running are separate calls so the user sees the whole picture, and
so the confirmations arrive with evidence attached.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from body.tools.tool_spec import (
    ToolContext,
    ToolRefused,
    ToolSpec,
    ToolTarget,
    required_text,
)
from brain.basal_ganglia.exceptions import RoutineNameInvalid, RoutineRefused

#: The trust category every routine tool resolves to.
CATEGORY = "routine_management"

#: What a flag means when the model sends the string "true". These schemas use
#: strings rather than booleans because the coercion layer refuses a string where
#: a boolean is declared, and a local model will send a string.
_TRUE = ("true", "yes", "on", "1")


def _ganglia(context: ToolContext, tool: str):
    """Return the Basal Ganglia, refusing when the region is not attached.

    Args:
        context: The collaborators the tool may act through.
        tool: The tool name, for the message.

    Returns:
        The Basal Ganglia.

    Raises:
        ToolRefused: When ``context.basal_ganglia`` is None.
    """
    ganglia = context.basal_ganglia
    if ganglia is None:
        raise ToolRefused(
            f"{tool}: routines are not attached, so ZUZZ cannot read or change them"
        )
    return ganglia


def _flag(params: Dict[str, Any], key: str, default: bool = False) -> bool:
    """Return a boolean flag from a validated string parameter."""
    raw = str(params.get(key, "") or "").strip().lower()
    if not raw:
        return default
    return raw in _TRUE


def _steps(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the steps from a create or update call, normalised.

    Args:
        params: The validated parameters.

    Returns:
        A list of ``{"tool", "params", "why"}`` mappings.

    Raises:
        ToolRefused: When there are no steps, or one names no tool. Both are
            refused here rather than at store time so the model learns immediately.
    """
    raw = params.get("steps")
    if not isinstance(raw, list) or not raw:
        raise ToolRefused("a routine needs at least one step")
    steps: List[Dict[str, Any]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ToolRefused(f"step {index + 1} is not an object")
        tool = str(entry.get("tool", "") or "").strip()
        if not tool:
            raise ToolRefused(f"step {index + 1} names no tool")
        steps.append(
            {
                "tool": tool,
                "params": dict(entry.get("params", {}) or {}),
                "why": str(entry.get("why", "") or ""),
            }
        )
    return steps


def _trigger(params: Dict[str, Any]) -> Optional[Any]:
    """Return the requested trigger spec, or None to leave the trigger alone."""
    raw = params.get("trigger")
    if raw is None or str(raw).strip() == "":
        return None
    return {"kind": str(raw).strip().lower()}


def _refusal(exc: RoutineRefused, tool: str) -> ToolRefused:
    """Re-raise a routine refusal as a tool precheck refusal."""
    return ToolRefused(f"{tool}: {exc}")


# -- routine_list_all --------------------------------------------------------


def _resolve_list(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a routine read. The target is fixed: there is nothing to name."""
    _ganglia(context, "routine_list_all")
    return ToolTarget(
        category=CATEGORY,
        target="all saved routines",
        preview={"description": "List the saved routines"},
        detail={"include_steps": _flag(params, "include_steps")},
    )


def _execute_list(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Return the stored routines, with steps only when asked."""
    include = _flag(params, "include_steps")
    routines = _ganglia(context, "routine_list_all").list_routines()
    payload = []
    for routine in routines:
        entry = routine.to_dict()
        if not include:
            entry.pop("steps", None)
        payload.append(entry)
    return {"count": len(payload), "routines": payload}


# -- routine_execute ---------------------------------------------------------


def _resolve_execute(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a routine run into the routine the gate judges."""
    name = required_text(params, "name", "routine_execute")
    ganglia = _ganglia(context, "routine_execute")
    # Resolved before the gate so a missing or disabled routine is a precheck
    # refusal rather than a permission question about something that is not there.
    try:
        routine = ganglia.get_routine(name)
    except RoutineRefused as exc:
        raise _refusal(exc, "routine_execute") from exc
    if not routine.enabled:
        raise ToolRefused(
            f"routine_execute: the routine {routine.name!r} is switched off; "
            "enable it before running it"
        )
    return ToolTarget(
        category=CATEGORY,
        target=routine.name,
        preview={
            "description": f"Plan the routine {routine.name!r}",
            "steps": len(routine.steps),
            "note": "every step is checked before any of them runs",
        },
        detail={"trigger": routine.trigger.to_dict()["description"]},
    )


def _execute_execute(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Plan the routine and report every step's verdict. Runs nothing."""
    ganglia = _ganglia(context, "routine_execute")
    name = str(params["name"])
    confirmations: List[int] = []
    for raw in params.get("confirm_steps", []) or []:
        try:
            confirmations.append(int(str(raw).strip()))
        except ValueError:
            continue
    try:
        plan = ganglia.plan(name)
    except RoutineRefused as exc:
        raise _refusal(exc, "routine_execute") from exc

    payload = plan.to_dict()
    payload["confirmation_supplied"] = confirmations
    payload["executed"] = False
    payload["next"] = (
        "run each permitted step as its own tool call, in order"
        if plan.runnable
        else "ask the user to confirm the pending steps, then run them in order"
        if plan.permitted
        else "this routine cannot run at the current trust level"
    )
    return payload


# -- routine_create ----------------------------------------------------------


def _resolve_create(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a routine creation into the name the gate judges."""
    name = required_text(params, "name", "routine_create")
    ganglia = _ganglia(context, "routine_create")
    try:
        from brain.basal_ganglia.routine_builder import validate_name

        validated = validate_name(name)
        if ganglia.registry.get(validated) is not None:
            raise ToolRefused(
                f"routine_create: a routine called {validated!r} already exists — "
                "use routine_update instead"
            )
    except RoutineNameInvalid as exc:
        raise _refusal(exc, "routine_create") from exc
    steps = _steps(params)
    return ToolTarget(
        category=CATEGORY,
        target=validated,
        preview={
            "description": f"Save the routine {validated!r}",
            "steps": [step["tool"] for step in steps],
        },
        detail={"trigger": str(params.get("trigger", "manual"))},
    )


def _execute_create(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Store the new routine."""
    ganglia = _ganglia(context, "routine_create")
    try:
        routine = ganglia.create_routine(
            {
                "name": str(params["name"]),
                "description": str(params.get("description", "") or ""),
                "steps": _steps(params),
                "trigger": _trigger(params) or "manual",
                "enabled": False,
                "source": "user",
            }
        )
    except RoutineRefused as exc:
        raise _refusal(exc, "routine_create") from exc
    return routine.to_dict()


# -- routine_update ----------------------------------------------------------


def _resolve_update(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a routine change into the routine the gate judges."""
    name = required_text(params, "name", "routine_update")
    _ganglia(context, "routine_update")
    return ToolTarget(
        category=CATEGORY,
        target=name.lower(),
        preview={"description": f"Change the routine {name!r}"},
        detail={"fields": sorted(params)},
    )


def _execute_update(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Apply the change to the stored routine."""
    ganglia = _ganglia(context, "routine_update")
    changes: Dict[str, Any] = {}
    if "description" in params:
        changes["description"] = str(params["description"])
    if "steps" in params:
        changes["steps"] = _steps(params)
    if "trigger" in params:
        changes["trigger"] = _trigger(params)
    if "enabled" in params:
        changes["enabled"] = _flag(params, "enabled")
    if not changes:
        raise ToolRefused("routine_update: nothing to change was supplied")
    try:
        routine = ganglia.update_routine(str(params["name"]), changes)
    except RoutineRefused as exc:
        raise _refusal(exc, "routine_update") from exc
    return routine.to_dict()


# -- routine_delete ----------------------------------------------------------


def _resolve_delete(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a routine deletion into the routine the gate judges."""
    name = required_text(params, "name", "routine_delete")
    ganglia = _ganglia(context, "routine_delete")
    try:
        ganglia.get_routine(name)
    except RoutineRefused as exc:
        raise _refusal(exc, "routine_delete") from exc
    return ToolTarget(
        category=CATEGORY,
        target=name.lower(),
        preview={
            "description": f"Delete the routine {name!r}",
            "warning": "this cannot be undone",
        },
        detail={},
    )


def _execute_delete(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Delete the routine."""
    name = str(params["name"]).strip().lower()
    deleted = _ganglia(context, "routine_delete").delete_routine(name)
    return {"name": name, "deleted": bool(deleted)}


#: The routine tools.
ROUTINE_TOOLS: tuple = (
    ToolSpec(
        name="routine_list_all",
        summary="List saved routines",
        resolve=_resolve_list,
        execute=_execute_list,
        returns="Every routine with its trigger and step count",
    ),
    ToolSpec(
        name="routine_execute",
        summary="Plan a routine's steps against the trust matrix",
        resolve=_resolve_execute,
        execute=_execute_execute,
        returns="Every step with its verdict; runs nothing",
    ),
    ToolSpec(
        name="routine_create",
        summary="Save a new routine",
        resolve=_resolve_create,
        execute=_execute_create,
        returns="The routine as stored",
    ),
    ToolSpec(
        name="routine_update",
        summary="Change a saved routine",
        resolve=_resolve_update,
        execute=_execute_update,
        returns="The routine as stored",
    ),
    ToolSpec(
        name="routine_delete",
        summary="Delete a saved routine",
        resolve=_resolve_delete,
        execute=_execute_delete,
        returns="Whether it was there and is now gone",
    ),
)