"""Body/Tools — the keyboard.

The typing tools, wrapped so a chat request reaches them through the same guards
as a Desktop-tab keystroke. Nothing here is a new capability: every ``execute``
calls a method that already existed and is already guarded. What is new is that
a model can ask for them.

:meth:`_resolve_keyboard_hotkey` does real work before the gate is consulted, and
mirrors :mod:`face.api.routes_hands` exactly: a combination is screened by
:func:`~body.hands.hotkey_policy.guard_hotkey`, so one ZUZZ will never press is
refused here rather than becoming a confirmation the user could approve. There
is no trust level at which "press Ctrl+Shift+Q to stop ZUZZ" should be a
question ZUZZ asks about itself.

The pointer tools live in :mod:`body.tools.mouse_tools` and the clipboard ones in
:mod:`body.tools.clipboard_tools`, because reading the clipboard carries an
escalation the keyboard does not.
"""

from __future__ import annotations

from typing import Any, Dict, List

from body.hands import action_classifier
from body.hands.hotkey_policy import guard_hotkey
from body.tools.tool_spec import (
    ToolContext,
    ToolSpec,
    ToolTarget,
    bounded_text,
    key_names,
    required_text,
)


def _resolve_keyboard_type(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve typed text into the sample of it the gate judges."""
    text = required_text(params, "text", "keyboard_type")
    return ToolTarget(
        category=action_classifier.mouse_category(),
        target=bounded_text(params, "text", "keyboard_type"),
        preview={
            "description": f"Type {len(text)} characters into the focused window",
            "text": text[:200],
        },
        detail={"chars": len(text)},
    )


def _execute_keyboard_type(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Type through the Hands facade with an allowed decision."""
    return context.hands.type_text(decision, str(params["text"]))


def _resolve_keyboard_hotkey(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a key combination, refusing the ones ZUZZ will never press."""
    keys: List[str] = key_names(params, "keyboard_hotkey")
    guard_hotkey(keys)
    return ToolTarget(
        category=action_classifier.mouse_category(),
        target="+".join(keys),
        preview={"description": f"Press {'+'.join(keys)}", "keys": keys},
    )


def _execute_keyboard_hotkey(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Press the combination through the Hands facade with an allowed decision."""
    return context.hands.hotkey(decision, key_names(params, "keyboard_hotkey"))


#: The keyboard tools.
KEYBOARD_TOOLS: tuple = (
    ToolSpec(
        name="keyboard_type",
        summary="Type text into the focused window",
        resolve=_resolve_keyboard_type,
        execute=_execute_keyboard_type,
        returns="How many characters were typed",
    ),
    ToolSpec(
        name="keyboard_hotkey",
        summary="Press a key combination",
        resolve=_resolve_keyboard_hotkey,
        execute=_execute_keyboard_hotkey,
        returns="Which combination was pressed",
    ),
)