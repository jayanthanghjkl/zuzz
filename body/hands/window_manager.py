"""Body/Hands — window discovery.

Finding and describing the windows on the desktop, without changing any of them.

The Windows API is reached through :mod:`ctypes` with a lazy import and a
try/except around every call. That is not defensive noise: this is the boundary
where ZUZZ meets an operating system, and the right behaviour when the OS
refuses to answer is "no windows", which the gate then treats as "nothing to
act on" — rather than an exception from three frames deep inside a click.

Resolution is the part that matters. Every handle ZUZZ is about to act on is
resolved here first, so "the window I checked" and "the window I acted on"
cannot drift apart. An explicit handle matches exactly. A title matches exactly,
and then as a fragment. A fragment matching more than one window is refused
rather than resolved by taking the first: guessing which of two windows the user
meant is how input lands somewhere they did not choose.
"""

from __future__ import annotations

from typing import Any, Dict, List

from body.hands.exceptions import InvalidInputRefused, TargetNotFoundRefused


def user32() -> Any:
    """Return the Win32 user32 entry point, or None off Windows."""
    try:  # pragma: no cover - Windows only
        import ctypes

        return ctypes.windll.user32
    except Exception:
        return None


def rect_type() -> Any:
    """Return a fresh ``RECT`` ctypes structure.

    Built on demand rather than at import time, because ctypes is only imported
    on Windows and a module-level structure would make this file unimportable
    elsewhere.
    """
    import ctypes

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    return RECT()


def process_name(pid: int) -> str:
    """Return a process name for a pid, or an empty string.

    Args:
        pid: The process id.

    Returns:
        The bare process name, e.g. ``notepad``; empty when it cannot be read,
        which is a normal answer for a process that exited mid-query.
    """
    try:
        import psutil

        return psutil.Process(pid).name().lower()
    except Exception:
        return ""


def list_windows() -> List[Dict[str, Any]]:
    """Return every visible top-level window.

    Returns:
        A list of ``{"hwnd", "title", "pid", "process", "rect", "visible"}``.
        Empty off Windows or when enumeration fails.
    """
    api = user32()
    if api is None:
        return []
    import ctypes

    found: List[Dict[str, Any]] = []
    rect_class = rect_type()

    def collect(hwnd: int, _lparam: int) -> bool:
        try:
            if not api.IsWindowVisible(hwnd):
                return True
            length = api.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(length + 1)
            api.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value
            if not title.strip():
                return True
            pid = ctypes.c_ulong()
            api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            rect = rect_class()
            api.GetWindowRect(hwnd, ctypes.byref(rect))
        except Exception:
            return True
        found.append(
            {
                "hwnd": int(hwnd),
                "title": title,
                "pid": int(pid.value),
                "process": process_name(pid.value),
                "rect": {
                    "left": rect.left,
                    "top": rect.top,
                    "width": rect.right - rect.left,
                    "height": rect.bottom - rect.top,
                },
                "visible": True,
            }
        )
        return True

    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_long)
    try:
        api.EnumWindows(callback_type(collect), 0)
    except Exception:
        return found
    return found


def active_window() -> Any:
    """Return the window that currently has focus, or None.

    Falls back to a bare handle record when enumeration missed the window — a
    window with no title can still have focus, and "here is a handle with no
    title" is more useful to the guards than "no window".
    """
    api = user32()
    if api is None:
        return None
    hwnd = api.GetForegroundWindow()
    if not hwnd:
        return None
    for window in list_windows():
        if window["hwnd"] == int(hwnd):
            return window
    return {
        "hwnd": int(hwnd),
        "title": "",
        "pid": 0,
        "process": "",
        "rect": {},
        "visible": True,
    }


def resolve_window(
    description: Any, windows: List[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Turn a caller's description of a window into a concrete window.

    Args:
        description: A window handle, an exact title, or a fragment of a title
            or process name.
        windows: The windows to search. Enumerated fresh when None, and
            supplied by tests and by callers that already hold a list.

    Returns:
        The window record.

    Raises:
        InvalidInputRefused: The description is empty.
        TargetNotFoundRefused: No window matched, or the fragment matched more
            than one.
    """
    if description is None or not str(description).strip():
        raise InvalidInputRefused("a window handle or title is required")
    text = str(description).strip()
    if windows is None:
        windows = list_windows()

    if text.isdigit():
        handle = int(text)
        for window in windows:
            if window["hwnd"] == handle:
                return window
        raise TargetNotFoundRefused(f"no window with handle {handle}")

    exact = [window for window in windows if window["title"] == text]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise TargetNotFoundRefused(
            f"{len(exact)} windows are titled {text!r}. ZUZZ will not guess "
            f"which one to act on; pass a window handle instead."
        )

    needle = text.lower()
    partial = [
        window
        for window in windows
        if needle in str(window["title"]).lower()
        or needle in str(window.get("process", "")).lower()
    ]
    if not partial:
        raise TargetNotFoundRefused(f"no window matches {text!r}")
    if len(partial) > 1:
        titles = ", ".join(repr(window["title"]) for window in partial[:5])
        raise TargetNotFoundRefused(
            f"{text!r} matches {len(partial)} windows ({titles}). ZUZZ will not "
            f"guess which one; pass a window handle instead."
        )
    return partial[0]