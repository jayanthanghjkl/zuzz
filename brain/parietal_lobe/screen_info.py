"""Parietal Lobe — display geometry.

Answers "what is this machine's screen setup", which is a question the agent cannot
answer by looking: the Occipital Lobe can report the size of the frame it captured, but
that is one monitor's rectangle, and on a two-display machine it is not the desktop.

**This module deliberately does not add ``screeninfo``.** The brief listed it as
optional, and it would be easy to install. It would also be wrong, for a reason this
project has already paid for once. The monitor layout is *already* read twice on this
machine — by :class:`body.hands.screens.Screens` for click validation, and by
:class:`brain.occipital_lobe.mss_backend.MssBackend` for capture indexing — and the
two already have to agree or a click validated against one layout is sent using
another's coordinates. A third reader would be a third answer. When
``vision_capture_screen`` and ``system_awareness`` disagree about where monitor 1 is,
there is no way to tell which one the safety check used, and the result is a
screenshot of the wrong screen that looks correct.

So the layout is read from the Occipital Lobe's backend when it is available, because
that is the one that decides which pixels a capture grabs, and it falls back to
:func:`body.hands.screens.virtual_desktop`. What this module adds on top is the part
neither of them carries:

* **DPI scale**, from ``GetDpiForSystem``, which both of the above explicitly decline
  to apply to coordinates (:mod:`body.hands.screens` explains why — dividing would put
  every click in the wrong place on a scaled display). It is reported, never applied.
* **The physical desktop rectangle**, so a point can be validated against the whole
  workspace rather than one monitor.
* **A single ``resolution`` summary**, because that is what a user asking "what's my
  screen setup" is asking for, and making them read three monitors' worth of JSON to
  learn their own display is a bad answer to a simple question.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from body.hands import screens

from brain.parietal_lobe.exceptions import SystemAwarenessUnavailable

#: Win32 ``GetSystemMetrics`` index for the desktop's DPI scale, as a percentage.
_SM_DPI = 88


class ScreenInfo:
    """The display layout this machine reports.

    Attributes:
        backend: The Occipital Lobe's capture backend, when one is attached — the
            authority on which pixels a capture grabs.
        desktop: Callable returning the virtual desktop rectangle.
        dpi: Callable returning the system DPI scale, or None off Windows.
    """

    def __init__(
        self,
        backend: Any = None,
        desktop: Optional[Any] = None,
        dpi: Optional[Any] = None,
    ) -> None:
        """Wire the reader.

        Args:
            backend: Anything with ``layout()`` returning the monitor list. Injected so
                the region can be tested with no display attached.
            desktop: Virtual desktop rectangle reader, defaulting to
                :func:`body.hands.screens.virtual_desktop`.
            dpi: DPI scale reader, defaulting to ``GetDpiForSystem``.
        """
        self.backend = backend
        self.desktop = desktop if desktop is not None else screens.virtual_desktop
        self.dpi = dpi if dpi is not None else _system_dpi

    def monitors(self) -> List[Dict[str, Any]]:
        """Return every attached monitor, indexed as the capture index.

        Returns:
            One record per monitor with ``left``, ``top``, ``width``, ``height``,
            ``primary`` and ``index``.

        Raises:
            SystemAwarenessUnavailable: No source would report a layout. Refused
                rather than answered with a fabricated 1920x1080: a plausible default
                here would be a screen capture of a monitor that does not exist, and
                the user would have no way to tell from the answer that it was invented.
        """
        layout = self._from_backend()
        if layout is None:
            desktop = self._desktop()
            if desktop:
                layout = [dict(desktop, primary=True)]
        if not layout:
            raise SystemAwarenessUnavailable(
                "no source would report the display layout: the capture backend "
                "and the Win32 desktop metrics both declined",
                detail="screen-info",
            )
        return [dict(monitor, index=index) for index, monitor in enumerate(layout)]

    def describe(self) -> Dict[str, Any]:
        """Return the layout for a human, and for the model.

        Returns:
            ``resolution`` (the total desktop, which is what "my screen setup" means),
            ``primary`` (the one display most people mean by "my screen"),
            ``monitors``, ``monitor_count``, ``dpi``, ``scale``, ``dpi_aware`` and
            ``source``.

            The last two are not decoration. This process may or may not be
            DPI-aware depending on what was imported before it, and an unaware
            process gets its Win32 metrics **scaled down** by ``96/dpi`` — a
            1920x1080 display reports as 1536x864, which is 0.8 of the truth. So the
            same machine can answer this call with two different resolutions
            depending on import order, and nothing in the number says which. Reporting
            ``dpi_aware`` and ``source`` makes the difference visible instead of
            letting it depend on which module loaded first.
        """
        layout = self.monitors()
        primary = next(
            (m for m in layout if m.get("primary")), layout[0] if layout else {}
        )
        left = min(m.get("left", 0) for m in layout)
        top = min(m.get("top", 0) for m in layout)
        right = max(m.get("left", 0) + m.get("width", 0) for m in layout)
        bottom = max(m.get("top", 0) + m.get("height", 0) for m in layout)
        dpi = self.dpi_value()
        return {
            "resolution": {
                "width": right - left,
                "height": bottom - top,
            },
            "primary": {
                "width": primary.get("width"),
                "height": primary.get("height"),
            },
            "desktop": {
                "left": left,
                "top": top,
                "width": right - left,
                "height": bottom - top,
            },
            "monitor_count": len(layout),
            "monitors": layout,
            "multi_monitor": len(layout) > 1,
            "dpi": dpi,
            "scale": round(dpi / 96.0, 2) if dpi else 1.0,
            "dpi_aware": _is_dpi_aware(),
            "source": "capture-backend" if self.backend is not None else "win32-metrics",
        }

    def scale(self) -> float:
        """Return the system DPI scale factor.

        Returns:
            The scale as a multiple — ``1.0`` at 96 DPI, ``1.5`` at 144. Reported for
            display and never applied to a coordinate, for the reason given in
            :mod:`body.hands.screens`: PyAutoGUI reports physical pixels and the OS
            delivers input in the same space, so dividing would misplace every click on
            a scaled display.
        """
        dpi = self.dpi_value()
        return round(dpi / 96.0, 2) if dpi else 1.0

    def dpi_value(self) -> Optional[int]:
        """Return the raw system DPI, or None when the OS will not say.

        Returns:
            The DPI as an integer, or None. None rather than 96, because 96 is a real
            DPI and reporting it for a machine that refused to answer makes "cannot
            tell" indistinguishable from "unscaled".
        """
        try:
            return self.dpi()
        except Exception:
            return None

    def _from_backend(self) -> Optional[List[Dict[str, Any]]]:
        """Return the capture backend's layout, or None when it will not say."""
        if self.backend is None:
            return None
        try:
            layout = self.backend.layout()
        except Exception:
            return None
        return list(layout) if layout else None

    def _desktop(self) -> Optional[Dict[str, Any]]:
        """Return the virtual desktop rectangle, or None when it cannot be read."""
        try:
            return self.desktop()
        except Exception:
            return None


def _system_dpi() -> int:
    """Return the system DPI from Win32.

    Returns:
        The DPI as an integer.

    Raises:
        SystemAwarenessUnavailable: This is not Windows, or the call failed. Callers
            degrade to a scale of 1.0, which is what a machine with no DPI awareness
            actually reports.
    """
    try:  # pragma: no cover - Windows only
        import ctypes

        try:
            dpi = ctypes.windll.user32.GetDpiForSystem()
        except AttributeError:
            # Pre-1607 Windows has no GetDpiForSystem; GetDeviceCaps is the older
            # route to the same number and is still correct, just less direct. The
            # handle types are declared because ctypes otherwise truncates a DC handle
            # to a 32-bit int and the call faults on a 64-bit process.
            gdi32 = ctypes.windll.gdi32
            user32 = ctypes.windll.user32
            gdi32.GetDeviceCaps.argtypes = [ctypes.c_void_p, ctypes.c_int]
            gdi32.GetDeviceCaps.restype = ctypes.c_int
            user32.GetDC.argtypes = [ctypes.c_void_p]
            user32.GetDC.restype = ctypes.c_void_p
            user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            user32.ReleaseDC.restype = ctypes.c_int
            dc = user32.GetDC(None)
            try:
                dpi = gdi32.GetDeviceCaps(dc, _SM_DPI)
            finally:
                user32.ReleaseDC(None, dc)
        if not dpi:
            raise SystemAwarenessUnavailable(
                "the system reported no DPI value", detail="screen-info"
            )
        return int(dpi)
    except SystemAwarenessUnavailable:
        raise
    except Exception as exc:
        raise SystemAwarenessUnavailable(
            f"the system DPI could not be read: {exc}", detail="screen-info"
        ) from exc


def _is_dpi_aware() -> Optional[bool]:
    """Return whether this process is per-monitor DPI aware, or None if unknowable.

    ``GetProcessDpiAwareness`` distinguishes two states that matter here and that
    ``GetDpiForSystem`` conflates: an unaware process, which is handed Win32 metrics
    scaled by ``96/dpi``, and a system-aware one, which is handed physical pixels. On
    the Session 13 machine the answer is ``0`` (unaware) until something in the process
    — ``mss`` on import, in practice — calls ``SetProcessDpiAwareness``, after which it
    becomes 2. Which is why :meth:`ScreenInfo.describe` reports this next to the
    resolution.

    The out-parameter is passed by reference and the handle is the real current-process
    pseudo-handle. Both matter: the signature is
    ``HRESULT GetProcessDpiAwareness(HANDLE, PROCESS_DPI_AWARENESS *)``, so calling it
    as ``GetProcessDpiAwareness(None)`` leaves the callee writing an ``int`` through a
    null pointer, which is an access violation rather than an exception and takes the
    whole process down with no traceback. That is not hypothetical — it is what this
    function did before, and the snapshot read is what surfaced it.

    Returns:
        True, False, or None when the API is unavailable or refused.
    """
    try:  # pragma: no cover - Windows only
        import ctypes

        awareness = ctypes.c_int(0)
        kernel32 = ctypes.windll.kernel32
        shcore = ctypes.windll.shcore
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        shcore.GetProcessDpiAwareness.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
        ]
        shcore.GetProcessDpiAwareness.restype = ctypes.c_long
        result = shcore.GetProcessDpiAwareness(
            kernel32.GetCurrentProcess(), ctypes.byref(awareness)
        )
        if result != 0:
            return None
        # 0 is DPI_UNAWARE, 1 SYSTEM_DPI_AWARE, 2 PER_MONITOR_DPI_AWARE.
        return int(awareness.value) != 0
    except Exception:
        return None


def monitor_for(monitors: Sequence[Dict[str, Any]], x: int, y: int) -> Optional[Dict[str, Any]]:
    """Return the monitor containing a desktop point.

    Args:
        monitors: The layout, as :meth:`ScreenInfo.monitors` returns it.
        x: Horizontal desktop coordinate.
        y: Vertical desktop coordinate.

    Returns:
        The monitor record with its index, or None when the point is off-desktop.
    """
    from brain.parietal_lobe.parietal_guards import monitor_at

    return monitor_at(monitors, x, y)
