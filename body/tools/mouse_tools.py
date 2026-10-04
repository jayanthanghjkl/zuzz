"""Body/Tools — the pointer.

The mouse tools, wrapped so a chat request reaches them through the same guards
as a Desktop-tab click. Nothing here is a new capability: every ``execute``
calls a method that already existed and is already guarded. What is new is that
a model can ask for them.

The keyboard tools live in :mod:`body.tools.keyboard_tools` and the clipboard
ones in :mod:`body.tools.clipboard_tools`.

Coordinates are resolved to the ``"x,y"`` form the gate judges via
:func:`~body.hands.mouse.coordinate_target`, which is the same string the desktop
routes produce, so a click at (100, 200) is decided identically whichever way it
was asked for. The screen bounds themselves are checked by the mouse layer
against the real display; here, only a value that is not a point at all is
refused.
"""

from __future__ import annotations

from typing import Any, Dict

from body.hands import action_classifier
from body.hands.mouse import coordinate_target
from body.tools.tool_spec import (
    ToolContext,
    ToolRefused,
    ToolSpec,
    ToolTarget,
)


def _point(params: Dict[str, Any], tool: str) -> Dict[str, int]:
    """Return a validated ``x``/``y`` pair.

    Args:
        params: The validated parameters.
        tool: The tool name, for the error message.

    Returns:
        The two coordinates as ints.

    Raises:
        ToolRefused: If either coordinate is missing or not a whole number. The
            screen bounds are checked later, by the mouse layer, against the
            real display — this is only refusing a value that is not a point.
    """
    point: Dict[str, int] = {}
    for axis in ("x", "y"):
        value = params.get(axis)
        if not isinstance(value, int):
            raise ToolRefused(f"{tool}: {axis!r} must be a whole number")
        point[axis] = value
    return point


def _resolve_mouse_click(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a click into the coordinate the gate will judge."""
    point = _point(params, "mouse_click")
    button = str(params.get("button", "left"))
    return ToolTarget(
        category=action_classifier.mouse_category(),
        target=coordinate_target(point["x"], point["y"]),
        preview={
            "description": f"{button.capitalize()} at ({point['x']}, {point['y']})",
            **point,
            "button": button,
        },
    )


def _execute_mouse_click(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Click through the Hands facade with an allowed decision."""
    point = _point(params, "mouse_click")
    return context.hands.click(
        decision, point["x"], point["y"], button=str(params.get("button", "left"))
    )


def _resolve_mouse_move(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a pointer move into the coordinate the gate will judge."""
    point = _point(params, "mouse_move")
    return ToolTarget(
        category=action_classifier.mouse_category(),
        target=coordinate_target(point["x"], point["y"]),
        preview={
            "description": f"Move the pointer to ({point['x']}, {point['y']})",
            **point,
        },
    )


def _execute_mouse_move(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Move the pointer through the Hands facade with an allowed decision."""
    point = _point(params, "mouse_move")
    return context.hands.move(decision, point["x"], point["y"])


def _resolve_mouse_scroll(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a scroll into a cursor-relative target."""
    amount = params.get("amount")
    if not isinstance(amount, int):
        raise ToolRefused("mouse_scroll: 'amount' must be a whole number of notches")
    direction = "up" if amount > 0 else "down" if amount < 0 else "nowhere"
    return ToolTarget(
        category=action_classifier.mouse_category(),
        target="cursor",
        preview={
            "description": f"Scroll {abs(amount)} notch(es) {direction}",
            "amount": amount,
        },
    )


def _execute_mouse_scroll(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Scroll through the Hands facade with an allowed decision."""
    return context.hands.scroll(decision, int(params["amount"]))


#: The pointer tools.
MOUSE_TOOLS: tuple = (
    ToolSpec(
        name="mouse_click",
        summary="Click a point on the screen",
        resolve=_resolve_mouse_click,
        execute=_execute_mouse_click,
        returns="What was clicked, and at which coordinate",
    ),
    ToolSpec(
        name="mouse_move",
        summary="Move the pointer without clicking",
        resolve=_resolve_mouse_move,
        execute=_execute_mouse_move,
        returns="The new pointer position",
    ),
    ToolSpec(
        name="mouse_scroll",
        summary="Scroll the wheel at the cursor",
        resolve=_resolve_mouse_scroll,
        execute=_execute_mouse_scroll,
        returns="How far it scrolled",
    ),
)