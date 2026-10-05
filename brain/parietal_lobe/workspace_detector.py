"""Parietal Lobe — workspace layout.

Answers "what surfaces are there to work on", which is the question behind "which
monitor is that window on" and "is the pointer on a screen I can click".

**Windows virtual desktops are not reported, and that is deliberate.** Win32 exposes
no query for how many virtual desktops exist or which one is showing. The supported
route is the ``IVirtualDesktopManager`` COM interface, which needs a COM apartment and
either ``comtypes`` or ``pywin32`` — neither of which is a dependency of this project,
and either of which would be a new native-COM dependency added for a widget. So this
module reports what Win32 *does* answer reliably, and says plainly that the virtual
desktop count is unavailable, rather than returning a plausible number it cannot
substantiate.

The distinction matters more than it might seem. A user with four virtual desktops and
one monitor who is told "you have one workspace" is not misled about anything they
asked; a user told "you are on workspace 2 of 4" when ZUZZ guessed the index from a
monitor number is told something false, and will act on it. Reporting
``virtual_desktops.available: False`` with a reason is the difference between a gap the
user understands and a bug they cannot.

What *is* reported:

* each attached monitor as a workspace surface, with its rectangle and primary flag —
  the Occipital Lobe crops by monitor index, so this is the index that has to be right;
* the virtual desktop rectangle spanning all of them, which is the region a pointer
  coordinate can legitimately fall in;
* the monitor the focused window is on, which is the surface the user is actually
  working in and the one the context detector's inference refers to.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

#: Why Win32 cannot answer this, stated in the answer rather than only in a docstring,
#: because the answer is what the UI and the model will read.
NO_VIRTUAL_DESKTOP_API = (
    "Windows does not expose the virtual desktop count to a normal Win32 call; it "
    "needs the IVirtualDesktopManager COM interface, which this build does not "
    "depend on. The monitor layout below is what the OS does report."
)


class WorkspaceDetector:
    """The desktop surfaces available to work on.

    Attributes:
        screens: The region's :class:`~brain.parietal_lobe.screen_info.ScreenInfo`,
            used as the single authority on the layout rather than re-reading it.
        active_window: Callable returning the focused window record, or None.
    """

    def __init__(self, screens: Any, active_window: Optional[Callable[[], Any]] = None) -> None:
        """Wire the detector.

        Args:
            screens: The region's ``ScreenInfo``. Passed in rather than constructed so
                there is exactly one monitor reader in the process; two readers is how a
                screenshot of monitor 1 and a click validated against monitor 2 end up
                disagreeing.
            active_window: Callable returning the focused window record.
        """
        self.screens = screens
        self.active_window = active_window

    def describe(self) -> Dict[str, Any]:
        """Return the workspace layout.

        Returns:
            ``monitors`` (one record per surface, carrying the capture index),
            ``surface_count``, ``desktop`` (the spanning rectangle),
            ``virtual_desktops`` (always ``{"available": False, "reason": ...}`` — see
            the module docstring), and ``focused_surface``, which is the monitor the
            focused window is on, or None.

        Raises:
            SystemAwarenessUnavailable: The layout could not be read at all. Refused
                rather than reported as an empty desktop, because "no workspaces" and
                "ZUZZ could not read the layout" are different facts and the second one
                being dressed as the first makes a working machine look broken.
        """
        layout = self.screens.monitors()
        focused = self._active_window()
        focused_surface = None
        if focused and focused.get("rect"):
            focused_surface = _surface_for(layout, focused["rect"])
        return {
            "monitors": layout,
            "surface_count": len(layout),
            "desktop": self.screens.describe()["desktop"],
            "virtual_desktops": {"available": False, "reason": NO_VIRTUAL_DESKTOP_API},
            "focused_surface": focused_surface,
        }

    def surface_at(self, x: int, y: int) -> Optional[Dict[str, Any]]:
        """Return the surface containing a desktop point.

        Args:
            x: Horizontal desktop coordinate.
            y: Vertical desktop coordinate.

        Returns:
            The monitor record with its index, or None when the point is off-desktop —
            which happens during a resolution change and is a real answer.
        """
        from brain.parietal_lobe.parietal_guards import monitor_at

        return monitor_at(self.screens.monitors(), x, y)

    def _active_window(self) -> Optional[Dict[str, Any]]:
        """Return the focused window record, or None when it cannot be read."""
        if self.active_window is None:
            return None
        try:
            return self.active_window()
        except Exception:
            return None


def _surface_for(
    layout: List[Dict[str, Any]], rect: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Return the monitor that mostly contains a window rectangle.

    Args:
        layout: The monitor records.
        rect: The window's desktop rectangle.

    Returns:
        The monitor with the greatest overlap with the window, or None when there is
        none. Greatest-overlap rather than top-left-corner containment because Windows
        reports windows that straddle two displays with the corner on one and most of
        the window on the other, and "which screen is that" should mean the one the
        user is actually looking at.
    """
    if not rect:
        return None
    left, top = rect.get("left", 0), rect.get("top", 0)
    right = left + rect.get("width", 0)
    bottom = top + rect.get("height", 0)
    best: Optional[Dict[str, Any]] = None
    best_overlap = 0
    for index, monitor in enumerate(layout):
        m_left, m_top = monitor.get("left", 0), monitor.get("top", 0)
        m_right = m_left + monitor.get("width", 0)
        m_bottom = m_top + monitor.get("height", 0)
        overlap_w = min(right, m_right) - max(left, m_left)
        overlap_h = min(bottom, m_bottom) - max(top, m_top)
        overlap = max(0, overlap_w) * max(0, overlap_h)
        if overlap > best_overlap:
            best_overlap = overlap
            best = dict(monitor, index=index)
    return best
