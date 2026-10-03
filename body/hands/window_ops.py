"""Body/Hands — window control and the foreground-window probe.

Changing windows, and answering the two questions the input layers ask about the
window that currently has focus.

**Is it full screen?** A full-screen window is a game, a presentation or a video.
Input there is gated separately, and a window that becomes full screen while a
decision is in flight must not inherit a decision made for something else. The
test is geometric — does the window cover its whole monitor — rather than a
style bit, because a borderless window covering the screen behaves like full
screen to the user, and a window carrying the full-screen style bit but not
covering the screen does not.

**Is it elevated?** If the foreground window runs as administrator and ZUZZ does
not, input either does nothing or is refused by the OS. ZUZZ never requests
elevation, so the answer it gives is an explanation.

Both answers come from one probe so they cannot differ between the mouse layer
and the keyboard layer, and so a test can replace one callable instead of two.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from body.hands.exceptions import InvalidInputRefused
from body.hands.window_manager import user32

#: ``ShowWindow`` commands for the state changes ZUZZ performs.
SW_MINIMIZE = 6
SW_MAXIMIZE = 3
SW_RESTORE = 9


def is_fullscreen(window: Optional[Dict[str, Any]] = None) -> bool:
    """Return True when a window covers its whole monitor.

    Args:
        window: The window to test. The foreground window when None.

    Returns:
        True within a pixel of the monitor bounds. The tolerance is there
        because a maximised window reports a border the monitor does not, and
        without it a maximised window would pass as not full screen and slip
        past the full-screen gate.
    """
    api = user32()
    if api is None:
        return False
    if window is None:
        from body.hands.window_manager import active_window

        window = active_window()
    if not window or not window.get("rect"):
        return False

    import ctypes

    from body.hands.window_manager import rect_type

    try:
        api.MonitorFromWindow.restype = ctypes.c_void_p
        monitor = api.MonitorFromWindow(
            ctypes.c_void_p(int(window["hwnd"])), 2  # MONITOR_DEFAULTTONEAREST
        )
        if not monitor:
            return False
        info = rect_type()()
        api.GetMonitorInfoW(ctypes.c_void_p(monitor), ctypes.byref(info))
    except Exception:
        return False

    rect = window["rect"]
    return (
        rect["left"] <= info.left + 1
        and rect["top"] <= info.top + 1
        and rect["left"] + rect["width"] >= info.right - 1
        and rect["top"] + rect["height"] >= info.bottom - 1
    )


def is_elevated() -> bool:
    """Return True when this process itself runs as administrator.

    Asked of ZUZZ rather than of each window, because ZUZZ never requests
    elevation: it is elevated for the whole session or not at all.
    """
    try:  # pragma: no cover - Windows only
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def foreground_probe(
    probe: Optional[Callable[[], Any]] = None,
) -> Callable[[], Dict[str, Any]]:
    """Return a callable describing the foreground window, for the input layers.

    The mouse and keyboard layers need "is something full screen has focus" and
    "is this window a login prompt" on every call. This builds that callable
    once, so both layers read the same window through the same code.

    Args:
        probe: An override returning a window record, used by tests. When given,
            the full-screen answer is taken from the record's ``fullscreen``
            key rather than measured.

    Returns:
        A callable returning ``{"fullscreen", "title", "window"}``.
    """

    def read() -> Dict[str, Any]:
        if probe is not None:
            record = probe() or {}
            return {
                "fullscreen": bool(record.get("fullscreen", False)),
                "title": str(record.get("title", "")),
                "window": record,
            }
        from body.hands.window_manager import active_window

        record = active_window() or {}
        return {
            "fullscreen": is_fullscreen(record),
            "title": str(record.get("title", "")),
            "window": record,
        }

    return read


def focus_window(hwnd: int) -> bool:
    """Bring a window to the foreground.

    A minimised window is restored first, because focusing a minimised window
    leaves it minimised on many window managers, and the caller would believe it
    had raised a window that is still out of the way.

    Args:
        hwnd: The window handle.

    Returns:
        True when the window was raised.
    """
    api = user32()
    if api is None:
        return False
    try:  # pragma: no cover - Windows only
        api.ShowWindow(hwnd, SW_RESTORE)
        return bool(api.SetForegroundWindow(hwnd))
    except Exception:
        return False


def set_state(hwnd: int, command: int) -> bool:
    """Minimise, maximise or restore a window.

    Args:
        hwnd: The window handle.
        command: One of the ``SW_`` constants in this module.

    Returns:
        True when the call was accepted.
    """
    api = user32()
    if api is None:
        return False
    try:  # pragma: no cover - Windows only
        return bool(api.ShowWindow(hwnd, command))
    except Exception:
        return False


def set_rect(hwnd: int, left: int, top: int, width: int, height: int) -> bool:
    """Move and resize a window in one call.

    Args:
        hwnd: The window handle.
        left: New left edge.
        top: New top edge.
        width: New width, in pixels.
        height: New height, in pixels.

    Returns:
        True when the call was accepted.

    Raises:
        InvalidInputRefused: The size is not positive.
    """
    if width <= 0 or height <= 0:
        raise InvalidInputRefused(
            f"window size must be positive, got {width}x{height}"
        )
    api = user32()
    if api is None:
        return False
    try:  # pragma: no cover - Windows only
        return bool(
            api.MoveWindow(
                hwnd, int(left), int(top), int(width), int(height), True
            )
        )
    except Exception:
        return False


def close_window(hwnd: int) -> bool:
    """Ask a window to close, the way its own close button would.

    ``WM_CLOSE`` is posted rather than the process being terminated, so the
    application gets its chance to save, prompt, or cancel. Killing the process
    is a separate operation with its own permission category, because it skips
    all of that.

    Args:
        hwnd: The window handle.

    Returns:
        True when the message was posted.
    """
    api = user32()
    if api is None:
        return False
    try:  # pragma: no cover - Windows only
        api.PostMessageW(int(hwnd), 0x0010, 0, 0)  # WM_CLOSE
        return True
    except Exception:
        return False