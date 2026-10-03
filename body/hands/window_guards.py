"""Body/Hands — window and process guards.

Three questions about the thing ZUZZ is about to drive, none of which the trust
matrix answers, because all three are about the *target* rather than the action.

**Is this a credential prompt?** Windows will not let a normal process type into
one, so the OS already refuses. This says no first, and says why, instead of
letting the user watch an action fail.

**Is it elevated?** If the window runs as administrator and this process does
not, input either does nothing or is refused by the OS. ZUZZ never requests
elevation for itself, so the answer is an explanation rather than an attempt to
work around it.

**Is it full screen?** A full-screen window is a game, a presentation or a video.
Input there is a different act from input into a text field, so it is gated
separately — and a window that appears full screen between the decision and the
action must not silently inherit a decision made for something else.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from body.hands import app_registry
from body.hands.exceptions import (
    ElevatedWindowRefused,
    FullscreenRefused,
    LoginDialogRefused,
    ProtectedProcessRefused,
)

#: Titles that mark a window as a credential prompt. Matched as substrings and
#: case-insensitively, because these strings vary between locales, versions and
#: themes, and a list of exact titles would be trivially bypassed by a rename.
_LOGIN_MARKERS = (
    "sign in",
    "log in",
    "login",
    "password",
    "credential",
    "windows security",
    "user account control",
)


def is_login_window(title: str) -> bool:
    """Return True when a window title looks like a credential prompt.

    Args:
        title: The window title.

    Returns:
        True when any marker appears in the title.
    """
    lowered = str(title or "").strip().lower()
    if not lowered:
        return False
    return any(marker in lowered for marker in _LOGIN_MARKERS)


def guard_window(window: Dict[str, Any], elevated_ok: bool = False) -> None:
    """Refuse a window ZUZZ must not drive.

    Args:
        window: The resolved window record, with ``title`` and optionally
            ``elevated``.
        elevated_ok: True when this process is itself elevated, which makes an
            elevated window no obstacle.

    Raises:
        LoginDialogRefused: The window is a credential prompt.
        ElevatedWindowRefused: The window is elevated and this process is not.
    """
    record = window or {}
    if is_login_window(str(record.get("title", ""))):
        raise LoginDialogRefused(
            f"{record.get('title')!r} is a credential prompt. Windows will not "
            f"let a normal process type into it, and ZUZZ will not try to make "
            f"that work."
        )
    if not elevated_ok and record.get("elevated"):
        raise ElevatedWindowRefused(
            f"{record.get('title')!r} is running as administrator and this "
            f"process is not. ZUZZ does not request elevation for itself, so the "
            f"answer is an explanation rather than an attempt."
        )


def guard_fullscreen(fullscreen: bool, permitted: bool = False) -> None:
    """Refuse input into a full-screen window without the right decision.

    Args:
        fullscreen: Whether a full-screen window currently has focus.
        permitted: True when the decision being executed covers
            ``fullscreen_control``.

    Raises:
        FullscreenRefused: A full-screen window has focus and the decision was
            not for the full-screen category.
    """
    if fullscreen and not permitted:
        raise FullscreenRefused(
            "a full-screen window has focus. Input there is controlled by "
            "fullscreen_control, which asks at every trust level."
        )


def guard_process(name: str, pid: Optional[int] = None) -> None:
    """Refuse to signal a process that is the machine rather than the user.

    Both the pid and the name are checked. The pid catches 0 and 4, which
    address the kernel and the system and cannot be signalled at all. The name
    catches the machine's own processes wherever they appear, including ones
    running under a different user.

    Args:
        name: The process name, with or without ``.exe``.
        pid: The process id, when known.

    Raises:
        ProtectedProcessRefused: The process is protected, or the pid is 0 or 4.
    """
    if pid in (0, 4):
        raise ProtectedProcessRefused(
            f"pid {pid} is not a user process. ZUZZ will not signal it."
        )
    if app_registry.is_protected_process(name, pid):
        raise ProtectedProcessRefused(
            f"{name} is a system process, blocked at every trust level. "
            f"Terminating it takes the machine down rather than closing a window."
        )


def describe() -> Dict[str, Any]:
    """Return what is off limits, for the Desktop tab."""
    return {
        "protected_processes": app_registry.describe()["protected_processes"],
        "login_window_markers": list(_LOGIN_MARKERS),
        "elevated_windows": "refused unless ZUZZ is itself elevated",
    }