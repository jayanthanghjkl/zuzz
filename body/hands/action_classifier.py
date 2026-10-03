"""Body/Hands — action classification.

The Step 4 lesson, applied: the Body does not decide, so this module does not
decide either. It answers two narrower questions.

**Which trust category does this operation belong to?** The gate reads that
against ``config/trust_levels.json``. Every operation appears in the mapping,
because an operation with no row is one the gate refuses — and refusing is the
right answer to an operation nobody classified.

**What is this action going to touch?** ``resolve_target`` turns the caller's
description into something concrete — an application name into an executable, a
window title into a window — *before* anything is guarded. Guessing later is how
"the title I checked" and "the window I acted on" drift apart.

Coordinates are resolved by :meth:`body.hands.screens.Screens.check`, which is
already called for every coordinate the mouse layer is given. It lives with the
display layout because that is what it needs; this module is the dispatcher for
the two kinds that need a registry or a window list.

Three classifications are escalated rather than taken at face value:

* an application that is not on the whitelist maps to ``run_safe_commands``, so it
  asks at every level instead of auto-approving like the whitelisted ones;
* clipboard content that looks like a credential maps to ``access_credentials``;
* input into a full-screen window maps to ``fullscreen_control``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from brain.prefrontal_cortex.action import ActionCategory

from body.hands import app_registry

#: Operations that only read. A read still needs a decision, but it is the
#: cheapest kind and shares the matrix row ordinary reads use.
READ_OPERATIONS = frozenset(
    {
        "get_position",
        "get_active_window",
        "list_windows",
        "list_running_apps",
        "clipboard_read",
    }
)

#: Operations that put input on the machine.
INPUT_OPERATIONS = frozenset(
    {
        "move",
        "click",
        "double_click",
        "right_click",
        "drag",
        "scroll",
        "type_text",
        "press",
        "press_keys",
        "hold_key",
        "release_key",
        "hotkey",
        "focus_window",
        "minimize",
        "maximize",
        "resize_window",
        "move_window",
    }
)

#: The rest: clipboard writes, applications and processes.
OTHER_OPERATIONS = frozenset(
    {"clipboard_write", "clipboard_clear", "open_app", "close_app", "kill_process"}
)


def category_for(operation: str) -> str:
    """Return the trust category an operation maps onto.

    Args:
        operation: The Body/Hands operation name, e.g. ``right_click``.

    Returns:
        A config key from ``config/trust_levels.json``.

    Raises:
        ValueError: For an operation this layer does not know. Raising is
            deliberate: an unmapped operation must fail loudly here rather than
            fall through to a default and be decided by accident.
    """
    if operation in READ_OPERATIONS:
        return str(ActionCategory.READ_FILES)
    if operation in INPUT_OPERATIONS:
        return str(ActionCategory.MOUSE_KEYBOARD)
    if operation in OTHER_OPERATIONS:
        return str(ActionCategory.WRITE_FILES)
    raise ValueError(f"unknown operation: {operation}")


def mouse_category() -> str:
    """Return the category for input into an ordinary window."""
    return str(ActionCategory.MOUSE_KEYBOARD)


def app_open_category(whitelisted: bool) -> str:
    """Return the category for launching an application.

    Args:
        whitelisted: Whether the application is on the auto-approve list.

    Returns:
        ``open_apps`` for a whitelisted application, ``run_safe_commands`` for
        anything else — which asks at every trust level.
    """
    return (
        str(ActionCategory.OPEN_APPS)
        if whitelisted
        else str(ActionCategory.RUN_SAFE_COMMANDS)
    )


def app_close_category() -> str:
    """Return the category for closing an application or killing a process.

    Closing an application can lose unsaved work, so it gets its own category
    rather than borrowing one, and that category asks at every level.
    """
    return str(ActionCategory.CLOSE_APPS)


def clipboard_read_category(finding: Optional[Dict[str, str]]) -> str:
    """Return the category for a clipboard read.

    Args:
        finding: What :func:`~body.hands.text_scanner.looks_like_credential`
            returned, or None.

    Returns:
        ``access_credentials`` when the content looks like a secret, so the read
        asks at every level; ``read_files`` otherwise.
    """
    if finding:
        return str(ActionCategory.ACCESS_CREDENTIALS)
    return str(ActionCategory.READ_FILES)


def fullscreen_category() -> str:
    """Return the category for taking input into a full-screen window.

    Separate from ``mouse_keyboard`` because input into a game, a presentation
    or a video is not the same act as input into a text field, and the user
    should be asked separately about each.
    """
    return str(ActionCategory.FULLSCREEN_CONTROL)


def resolve_target(kind: str, value: Any) -> Dict[str, Any]:
    """Resolve a caller-supplied description into a concrete target.

    The shared entry point the Step 5 brief asks for. Every non-coordinate
    target is resolved before it is guarded, so the thing that was checked is
    the thing that will be acted on.

    Args:
        kind: ``app`` or ``window``.
        value: The caller's description of the target.

    Returns:
        The resolved target with the ``category`` the gate should decide.

    Raises:
        ValueError: For a kind this layer does not resolve.
    """
    if kind == "app":
        resolved = app_registry.resolve_app(value)
        return dict(
            resolved,
            category=app_open_category(resolved["whitelisted"]),
        )
    if kind == "window":
        from body.hands import window_manager

        return dict(
            window_manager.resolve_window(value),
            category=str(ActionCategory.MOUSE_KEYBOARD),
        )
    raise ValueError(
        f"unknown target kind: {kind!r}. Coordinates are resolved by "
        f"Screens.check(), which the mouse layer calls for every point."
    )