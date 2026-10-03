"""Body/Hands — displays.

Where a click lands is decided here, before anything moves. The Step 4 rule
applies unchanged: resolve the target, then classify it. A coordinate is
resolved against every attached display and refused when it is not on one,
because "off the edge of the screen" and "on a second monitor" are different
situations and only one of them is a bug.

Multi-monitor behaviour, per the Step 5 brief:

* Coordinates are validated against **all** monitors, so a click on a secondary
  display is legitimate input rather than a refusal.
* The primary display is the default when no position is given.
* A coordinate on a disconnected monitor is refused: the layout is read at
  startup, so a point outside it means stale information, not a click into void.
* The monitor an action targets is logged, so a surprising click can be traced
  to a display rather than guessed at.

DPI: PyAutoGUI reports physical pixels and the OS delivers pointer input in the
same space, so coordinates pass through unscaled. The scale factor is read and
shown in the UI but never applied to the coordinates — dividing them would put
every click in the wrong place on a scaled display, which is the exact failure
this module exists to prevent.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from body.hands.exceptions import OffScreenRefused

#: Win32 ``GetSystemMetrics`` indices for the virtual desktop rectangle.
_VIRTUAL = {"left": 76, "top": 77, "width": 78, "height": 79}


def virtual_desktop() -> Optional[Dict[str, int]]:
    """Return the virtual desktop rectangle, when the OS will say.

    Returns:
        ``{"left", "top", "width", "height"}`` covering every monitor, or None
        off Windows or when the call fails. A single-monitor machine is a
        normal answer, not an error.
    """
    try:  # pragma: no cover - Windows only
        import ctypes

        user32 = ctypes.windll.user32
        return {
            name: int(user32.GetSystemMetrics(index))
            for name, index in _VIRTUAL.items()
        }
    except Exception:
        return None


class Screens:
    """The displays this process can send input to.

    Attributes:
        width: Primary display width in pixels.
        height: Primary display height in pixels.
        monitors: Every monitor as ``{"left", "top", "width", "height",
            "primary"}``, falling back to the primary alone.
        scale: System DPI scale factor, 1.0 when unknown.
    """

    def __init__(
        self,
        actuator: Any,
        monitors: Optional[List[Dict[str, Any]]] = None,
        scale: float = 1.0,
    ) -> None:
        """Read the display layout.

        Args:
            actuator: The actuator to ask for the primary display size.
            monitors: Explicit monitor list, for tests and for callers that
                already know the layout.
            scale: System DPI scale factor; informational only.
        """
        self.width, self.height = actuator.size()
        self.monitors = monitors or self._enumerate()
        self.scale = scale

    def _enumerate(self) -> List[Dict[str, Any]]:
        """Return the monitors attached right now."""
        desktop = virtual_desktop()
        if desktop:
            return [dict(desktop, primary=True)]
        return [
            {
                "left": 0,
                "top": 0,
                "width": self.width,
                "height": self.height,
                "primary": True,
            }
        ]

    def bounds(self) -> Tuple[int, int, int, int]:
        """Return the whole desktop as ``(left, top, width, height)``."""
        left = min(m["left"] for m in self.monitors)
        top = min(m["top"] for m in self.monitors)
        right = max(m["left"] + m["width"] for m in self.monitors)
        bottom = max(m["top"] + m["height"] for m in self.monitors)
        return left, top, right - left, bottom - top

    def monitor_at(self, x: int, y: int) -> Optional[Dict[str, Any]]:
        """Return the monitor containing a point, or None when off-desktop.

        Args:
            x: Horizontal coordinate.
            y: Vertical coordinate.

        Returns:
            The monitor record with its index, or None.
        """
        for index, monitor in enumerate(self.monitors):
            left, top = monitor["left"], monitor["top"]
            if (
                left <= x < left + monitor["width"]
                and top <= y < top + monitor["height"]
            ):
                return dict(monitor, index=index)
        return None

    def contains(self, x: int, y: int) -> bool:
        """Return True when a point is on an attached monitor."""
        return self.monitor_at(x, y) is not None

    def check(self, x: Any, y: Any) -> Dict[str, Any]:
        """Validate a coordinate pair and say which monitor it is on.

        Args:
            x: Requested horizontal coordinate.
            y: Requested vertical coordinate.

        Returns:
            The resolved point plus the monitor it lands on, for the log.

        Raises:
            OffScreenRefused: The point is on no attached monitor, or the
                coordinates are not whole numbers.
        """
        px, py = _as_int(x, y)
        monitor = self.monitor_at(px, py)
        if monitor is None:
            left, top, width, height = self.bounds()
            raise OffScreenRefused(
                f"({px}, {py}) is not on any attached monitor. The desktop "
                f"spans ({left}, {top}) to ({left + width}, {top + height}) "
                f"across {len(self.monitors)} monitor(s)."
            )
        return {"x": px, "y": py, "monitor": monitor}

    def describe(self) -> Dict[str, Any]:
        """Return the layout for the UI: sizes, monitors and scale."""
        left, top, width, height = self.bounds()
        return {
            "primary": {"width": self.width, "height": self.height},
            "desktop": {
                "left": left,
                "top": top,
                "width": width,
                "height": height,
            },
            "monitors": self.monitors,
            "scale": self.scale,
        }


def _as_int(x: Any, y: Any) -> Tuple[int, int]:
    """Coerce a coordinate pair to integers.

    Floats truncate rather than round: a click at 10.7 goes to 10, and rounding
    up can land outside a control's bounds. Booleans are refused because ``True``
    is an ``int`` in Python, and ``click(True, True)`` must not quietly mean the
    screen origin.

    Args:
        x: Requested horizontal coordinate.
        y: Requested vertical coordinate.

    Returns:
        The integer pair.

    Raises:
        OffScreenRefused: A coordinate is a bool, or not a number at all.
    """
    if isinstance(x, bool) or isinstance(y, bool):
        raise OffScreenRefused("coordinates must be numbers, not booleans")
    try:
        return int(x), int(y)
    except (TypeError, ValueError) as exc:
        raise OffScreenRefused(
            f"coordinates must be whole numbers, got {x!r}, {y!r}"
        ) from exc