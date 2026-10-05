"""Parietal Lobe — pointer position.

Answers "where is the pointer", which is needed for two different jobs and they have
different tolerances. For a click, a position one poll stale is a click in the wrong
place. For an activity inference, a position is evidence of *engagement* — a pointer
parked at the same spot for ten minutes while the user reads is not the same signal as
one that has just moved.

**The hidden cursor is a real answer, not a failure.** Windows reports a cursor
position while it is hidden, and reports ``(0, 0)`` for a display that has no pointer.
Both would otherwise read as "the pointer is in the top-left corner", which is a claim
about where the user is pointing. :func:`cursor_visible` uses ``GetCursorInfo``, which
reports the cursor's own display and whether it is in a hide sequence — so a zeroed
pointer can be told apart from a real one sitting at the origin.

Everything here is a *read*. No method in this module moves the pointer, and nothing
here can: that is :mod:`body.hands.mouse` and it takes its own decision. Keeping the
read separate from the write is why "ZUZZ looked at the pointer" and "ZUZZ moved it"
can never share a code path — and it is why the read still needs a decision of its own,
since a title bar and a pointer position are both reads of what the user is doing.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from brain.parietal_lobe.exceptions import SystemAwarenessUnavailable
from brain.parietal_lobe.parietal_guards import monitor_at
from brain.parietal_lobe.screen_info import ScreenInfo


class MouseTracker:
    """Where the pointer is.

    Attributes:
        position: Callable returning ``(x, y)``, or None when the OS reports failure.
        visible: Callable returning whether the cursor is on screen.
        monitors: Callable returning the display layout, used to name the monitor the
            pointer is on — a position without a monitor is not enough for the
            Occipital Lobe to crop the right screen.
    """

    def __init__(
        self,
        position: Optional[Callable[[], Any]] = None,
        visible: Optional[Callable[[], Any]] = None,
        monitors: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    ) -> None:
        """Wire the tracker.

        Args:
            position: Pointer position reader, defaulting to ``GetCursorPos``.
            visible: Cursor visibility reader, defaulting to ``GetCursorInfo``.
            monitors: Display layout reader. Defaults to the same
                :class:`~brain.parietal_lobe.screen_info.ScreenInfo` the rest of the
                region uses, and is injectable so the region is testable with no
                display attached. Not left as ``None``: a tracker with no layout
                reports ``on_desktop: False`` for every position, which is an answer
                about the desktop rather than about the pointer.
        """
        self.position = position if position is not None else _cursor_position
        self.visible = visible if visible is not None else _cursor_visible
        self.monitors = (
            monitors
            if monitors is not None
            else (lambda: ScreenInfo().monitors())
        )

    def locate(self) -> Dict[str, Any]:
        """Return the pointer's position and which display it is on.

        Returns:
            ``x``, ``y``, ``monitor`` (the record, with its index), ``monitor_index``,
            ``on_desktop`` and ``visible``. ``on_desktop`` is False when the pointer is
            on no attached display — which happens during a resolution change — and is
            reported rather than rounded to monitor 0, because "the pointer is on a
            screen that is not there" and "the pointer is on the primary" call for
            different responses.

        Raises:
            SystemAwarenessUnavailable: The OS would not report a position. Not
                raised for ``(0, 0)`` with the cursor hidden: that is a position, and
                it comes back with ``visible: False`` so the caller can say so.
        """
        try:
            raw = self.position()
        except SystemAwarenessUnavailable:
            raise
        except Exception as exc:  # pragma: no cover - platform boundary
            raise SystemAwarenessUnavailable(
                f"the pointer position could not be read: {exc}",
                detail="mouse-position",
            ) from exc
        if raw is None:
            raise SystemAwarenessUnavailable(
                "the OS reported no pointer position", detail="mouse-position"
            )
        x, y = int(raw[0]), int(raw[1])
        layout = self._monitors()
        monitor = monitor_at(layout, x, y) if layout else None
        return {
            "x": x,
            "y": y,
            "monitor": monitor,
            "monitor_index": monitor.get("index") if monitor else None,
            "on_desktop": monitor is not None,
            "visible": self.cursor_visible(),
        }

    def cursor_visible(self) -> Optional[bool]:
        """Return whether the pointer is currently on screen.

        Returns:
            True or False, or None when the OS would not say. None is preserved
            rather than defaulted to True: the difference between "the cursor is
            hidden" and "ZUZZ cannot tell whether the cursor is hidden" is the
            difference between a correct explanation of a stale-looking pointer and a
            wrong one.
        """
        try:
            return self.visible()
        except Exception:
            return None

    def _monitors(self) -> List[Dict[str, Any]]:
        """Return the display layout, or an empty list when it cannot be read."""
        if self.monitors is None:
            return []
        try:
            return list(self.monitors() or [])
        except Exception:
            return []


def _cursor_position() -> Optional[Any]:
    """Return the pointer's desktop position.

    Returns:
        ``(x, y)``, or None when ``GetCursorPos`` reported failure — which is what a
        locked session looks like, and is returned rather than raised because a
        refused ``GetCursorPos`` and a cursor at the origin are different facts.

    Raises:
        SystemAwarenessUnavailable: This is not Windows, so the call cannot be made.
    """
    user32, point = _win32_types()
    position = point()
    if not user32.GetCursorPos(_by_ref(position)):
        return None
    return (int(position.x), int(position.y))


def _cursor_visible() -> Optional[bool]:
    """Return whether the cursor is on screen, via ``GetCursorInfo``.

    ``GetCursorInfo`` is used rather than ``ShowCursor`` because ``ShowCursor`` has a
    display-count side effect: every unbalanced call changes the cursor's visibility,
    so *asking* with it would change the answer it returns. ``GetCursorInfo`` is a pure
    read, which is the only kind a perception module is allowed to perform.

    Returns:
        True when the cursor is on screen (bit 0 of ``flags``, ``CURSOR_SHOWING``),
        False when it is hidden or there is no display, None when the call failed.
    """
    user32, point = _win32_types()
    cursor_info = _cursor_info_type(point)
    info = cursor_info()
    info.cbSize = _sizeof(info)
    if not user32.GetCursorInfo(_by_ref(info)):
        return None
    return bool(info.flags & 1)


def _win32_types() -> Any:
    """Return ``(user32, POINT)``, building the structures on demand.

    Built inside the function rather than at module level so this file stays
    importable on a machine with no ``ctypes.windll`` — the same reason
    :func:`body.hands.window_manager.rect_type` builds its ``RECT`` on demand.

    Returns:
        A tuple of the user32 entry point and the ``POINT`` structure type.

    Raises:
        SystemAwarenessUnavailable: ``ctypes`` or ``user32`` is unavailable.
    """
    try:  # pragma: no cover - Windows only
        import ctypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        return ctypes.windll.user32, POINT
    except Exception as exc:
        raise SystemAwarenessUnavailable(
            f"pointer state needs the Win32 API and this is not Windows: {exc}",
            detail="mouse-position",
        ) from exc


def _cursor_info_type(point: Any) -> Any:
    """Return the ``CURSORINFO`` structure type, given ``POINT``.

    Args:
        point: The ``POINT`` structure type, so the embedded ``screenPos`` field has
            the layout Windows expects. Passing a different ``POINT`` here produces a
            struct of the wrong size and ``GetCursorInfo`` fails on every call.

    Returns:
        The ``CURSORINFO`` structure type.

    Raises:
        SystemAwarenessUnavailable: ``ctypes`` is unavailable.
    """
    try:  # pragma: no cover - Windows only
        import ctypes

        class CURSORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_ulong),
                ("flags", ctypes.c_ulong),
                ("hCursor", ctypes.c_void_p),
                ("screenPos", point),
            ]

        return CURSORINFO
    except Exception as exc:
        raise SystemAwarenessUnavailable(
            f"cursor visibility needs the Win32 API: {exc}", detail="mouse-position"
        ) from exc


def _by_ref(structure: Any) -> Any:
    """Return a by-reference pointer to a ctypes structure.

    Args:
        structure: The structure instance.

    Returns:
        The ``ctypes`` pointer ``GetCursorPos`` and ``GetCursorInfo`` require — both
        are ``DWORD``-returning APIs taking a pointer, and passing the structure
        itself is a ``TypeError`` deep inside the call.
    """
    import ctypes

    return ctypes.byref(structure)


def _sizeof(structure: Any) -> int:
    """Return the size in bytes of a ctypes structure instance.

    Args:
        structure: The structure instance.

    Returns:
        Its size, which ``GetCursorInfo`` requires in ``cbSize`` before it will fill
        the struct in.
    """
    import ctypes

    return ctypes.sizeof(structure)
