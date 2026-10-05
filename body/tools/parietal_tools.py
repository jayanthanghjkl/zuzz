"""Body/Tools — system awareness.

Six tools wrapping the Parietal Lobe's capabilities:
- parietal_get_active_window: which window has focus
- parietal_get_screen_info: monitors, resolution and DPI
- parietal_get_running_apps: running applications and their resources
- parietal_get_mouse_position: where the pointer is
- parietal_detect_context: what the user appears to be doing
- parietal_get_snapshot: all of the above at once

All are categorised ``system_awareness``, which auto-approves at every trust level.
That row is a decision, not an oversight, and it is argued in
``config/trust_levels.json``: a window title is not a file, but it is also not a
rendered bank portal, and the inference it feeds is computed on this machine and
reduced to one word before the model ever sees it. What the row does not do is skip a
check — every call below still proposes an action, still gets scored, decided, audited
and kill-switched, and the six targets are spelled so that an approval for one cannot
be replayed on another.

**Window titles are the sensitive part, and they are not what the gate judges.** The
decision target is the operation name, never the title, so a document name is not
matched by the Amygdala's regexes, not written to the audit log as if it were a path,
and not rendered into a confirmation popup. The title still reaches the model, because
"what am I working on" is unanswerable without it — which is why
``parietal_detect_context`` returns the *inferred context* with its evidence and the
raw title, and why the caller that wants to avoid the title should ask for the context
alone.
"""

from __future__ import annotations

from typing import Any, Dict

from brain.parietal_lobe.parietal_lobe import ParietalLobe
from brain.prefrontal_cortex.action import ActionCategory
from body.tools.tool_spec import ToolContext, ToolRefused, ToolSpec, ToolTarget


def _get_lobe(context: ToolContext) -> ParietalLobe:
    """Return the Parietal Lobe from context, refusing when absent.

    Args:
        context: The collaborators the tool may act through.

    Returns:
        The region.

    Raises:
        ToolRefused: The region is not attached. Same reasoning as the vision tools:
            a registered tool that cannot reach its backend is a tool the model is
            told about and cannot use, which is how a capable agent starts claiming
            it saw a screen it never captured.
    """
    lobe = getattr(context, "parietal_lobe", None)
    if lobe is None:
        raise ToolRefused(
            "system awareness tools require the Parietal Lobe, which is not attached"
        )
    if not isinstance(lobe, ParietalLobe):
        raise ToolRefused("context.parietal_lobe is not a ParietalLobe instance")
    return lobe


def _target(lobe: ParietalLobe, operation: str, monitor: int = 0) -> ToolTarget:
    """Resolve one system read into a gate target.

    The target comes from the region's own :meth:`~brain.parietal_lobe.parietal_lobe.ParietalLobe.propose`,
    for the same reason the vision tools do it: ``parietal_guards.same_target``
    compares the target exactly, so the string the gate judges has to be
    character-for-character the one the region re-checks when it executes. Two
    spellings would make every approved read fail at the last line with a replay error.

    Args:
        lobe: The region.
        operation: One of the region's six operation names.
        monitor: Zero-based monitor index, when the operation takes one.

    Returns:
        The resolved target, ready for the gate.
    """
    action = lobe.propose(operation, monitor if operation == "screen-info" else None)
    return ToolTarget(
        category=str(ActionCategory.SYSTEM_AWARENESS),
        target=action.target,
        preview={"description": _PREVIEWS[operation], "reads_screen": False},
    )


#: What each confirmation/audit line says it is about. Deliberately generic: the point
#: is that a reader of the log learns which *read* happened, not what was on screen.
_PREVIEWS = {
    "active-window": "Read which window has focus",
    "screen-info": "Read this machine's display layout",
    "running-apps": "List the running applications",
    "mouse-position": "Read the pointer position",
    "detect-context": "Infer what the user is working on",
    "snapshot": "Read a full system-awareness snapshot",
}


def _resolve_active_window(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve the active-window read."""
    return _target(_get_lobe(context), "active-window")


def _execute_active_window(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Return the focused window."""
    return _get_lobe(context).get_active_window(decision)


def _resolve_screen_info(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve the screen-info read."""
    monitor = int(params.get("monitor", 0))
    return _target(_get_lobe(context), "screen-info", monitor)


def _execute_screen_info(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Return the display layout."""
    return _get_lobe(context).get_screen_info(decision)


def _resolve_running_apps(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve the running-apps read."""
    return _target(_get_lobe(context), "running-apps")


def _execute_running_apps(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Return the running applications."""
    include_hidden = bool(params.get("include_hidden", False))
    limit = int(params.get("limit", 50))
    return _get_lobe(context).get_running_apps(
        decision, include_hidden=include_hidden, limit=limit
    )


def _resolve_mouse_position(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve the mouse-position read."""
    return _target(_get_lobe(context), "mouse-position")


def _execute_mouse_position(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Return the pointer position."""
    position = _get_lobe(context).get_mouse_position(decision)
    # The full monitor record is dropped here: the model needs to know *which* screen,
    # not a second copy of the layout it can get from parietal_get_screen_info.
    return {
        "x": position.get("x"),
        "y": position.get("y"),
        "monitor_index": position.get("monitor_index"),
        "on_desktop": position.get("on_desktop"),
        "cursor_visible": position.get("visible"),
    }


def _resolve_detect_context(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve the detect-context read."""
    return _target(_get_lobe(context), "detect-context")


def _execute_detect_context(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Return the inferred activity context with its evidence."""
    return _get_lobe(context).detect_user_context(decision)


def _resolve_snapshot(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve the snapshot read."""
    return _target(_get_lobe(context), "snapshot")


def _execute_snapshot(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Return the full snapshot."""
    return _get_lobe(context).get_full_snapshot(decision)


#: The system awareness tools.
PARIETAL_TOOLS: tuple = (
    ToolSpec(
        name="parietal_get_active_window",
        summary="Report which application window currently has focus",
        resolve=_resolve_active_window,
        execute=_execute_active_window,
        returns="Focused window with title, application, position and minimised state",
    ),
    ToolSpec(
        name="parietal_get_screen_info",
        summary="Report the display layout: resolution, monitors and DPI",
        resolve=_resolve_screen_info,
        execute=_execute_screen_info,
        returns="Resolution, monitor list, DPI scale and whether the process is DPI aware",
    ),
    ToolSpec(
        name="parietal_get_running_apps",
        summary="List the running applications and their memory use",
        resolve=_resolve_running_apps,
        execute=_execute_running_apps,
        returns="Application count, hidden process count, and per-app memory and CPU",
    ),
    ToolSpec(
        name="parietal_get_mouse_position",
        summary="Report where the pointer is and which monitor it is on",
        resolve=_resolve_mouse_position,
        execute=_execute_mouse_position,
        returns="Pointer coordinates, monitor index, and whether the cursor is visible",
    ),
    ToolSpec(
        name="parietal_detect_context",
        summary="Infer what the user is working on right now",
        resolve=_resolve_detect_context,
        execute=_execute_detect_context,
        returns="One of coding, browsing, writing, reading, gaming, idle or unknown, with confidence and the evidence behind it",
    ),
    ToolSpec(
        name="parietal_get_snapshot",
        summary="Read every system-awareness signal at once",
        resolve=_resolve_snapshot,
        execute=_execute_snapshot,
        returns="Active window, screen info, running apps, pointer, inferred context and workspace layout",
    ),
)
