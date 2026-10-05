"""Occipital Lobe — the real capture backend, via ``mss``.

Separate from :mod:`brain.occipital_lobe.capture_backend` so that importing the
backend interface costs nothing on a machine with no display, and so the failure
mode stays specific: an absent ``mss`` is
:class:`~brain.occipital_lobe.exceptions.VisionModelUnavailable` with
``reason="missing_dependency"``, which the API maps to a 503 with a sentence
telling the user what to install.

``mss`` is used rather than ``pyautogui.screenshot`` for three reasons: it grabs the
framebuffer rather than compositing through the window manager, so it captures a
minimised or background window and a secondary monitor without focusing it first;
it is an order of magnitude faster; and it reports the monitor layout, which the
region needs anyway for crop validation.

The grabber is a context manager because it holds an OS handle on the
framebuffer. Leaking one per capture exhausts the desktop's handles over a long
session, and the symptom — everything getting slower — points nowhere near the
cause.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Iterator, List

from brain.occipital_lobe import image_ops
from brain.occipital_lobe.capture_backend import CaptureBackend, Grabbed
from brain.occipital_lobe.exceptions import MonitorNotFound, VisionModelUnavailable


class MssBackend(CaptureBackend):
    """Captures through ``mss``, which reads the framebuffer directly."""

    def layout(self) -> List[Dict[str, Any]]:
        """Return the monitor layout as ``mss`` reports it.

        Returns:
            One record per monitor, with ``mss``'s own ``left``/``top`` in desktop
            pixels. The primary display is flagged, falling back to the first
            monitor when ``mss`` does not say which one it is — a laptop with one
            panel reports no ``primary`` key at all on some versions.
        """
        with open_grabber() as grabber:
            monitors = list(grabber.monitors)
        primary = next((monitor for monitor in monitors if monitor.get("primary")), None)
        fallback = monitors[0] if monitors else None
        return [
            {
                "left": int(monitor["left"]),
                "top": int(monitor["top"]),
                "width": int(monitor["width"]),
                "height": int(monitor["height"]),
                "primary": monitor is (primary or fallback),
            }
            for monitor in monitors
        ]

    def grab(self, monitor: int = 0) -> Grabbed:
        """Grab one monitor.

        Args:
            monitor: Zero-based index into :meth:`layout`.

        Returns:
            PNG bytes and the rectangle they came from.

        Raises:
            MonitorNotFound: ``mss`` reported no monitors.
        """
        with open_grabber() as grabber:
            targets = list(grabber.monitors)
            if not targets:
                raise MonitorNotFound("mss reported no monitors to capture")
            try:
                rect = dict(targets[monitor])
            except IndexError as exc:
                raise MonitorNotFound(
                    f"monitor {monitor} is not attached; mss reports {len(targets)}"
                ) from exc
            shot = grabber.grab(rect)
            png = image_ops.encode_rgb(bytes(shot.rgb), tuple(shot.size))
        return png, {
            "left": int(rect["left"]),
            "top": int(rect["top"]),
            "width": int(rect["width"]),
            "height": int(rect["height"]),
        }


@contextmanager
def open_grabber() -> Iterator[Any]:
    """Open an ``mss`` grabber for the duration of the block.

    Yields:
        The ``mss.mss`` instance.

    Raises:
        VisionModelUnavailable: ``mss`` is not installed, or the platform refused
            to give it a screen. Both are reported with the underlying reason
            attached, because they need different answers from a human — one is
            fixed by installing a package, the other by restoring a desktop
            session.
    """
    try:
        import mss  # lazy: only a real capture needs it
    except ImportError as exc:
        raise VisionModelUnavailable(
            "mss is not installed - run: pip install mss", reason="missing_dependency"
        ) from exc
    try:
        grabber = mss.mss()
    except Exception as exc:  # mss raises platform-specific errors here
        raise VisionModelUnavailable(
            f"could not open a screen capture: {exc}", reason="no_display"
        ) from exc
    try:
        yield grabber
    finally:
        try:
            grabber.close()
        except Exception:
            pass