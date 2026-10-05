"""Occipital Lobe — capture backends.

The seam between the Occipital Lobe and the machine's screen. A backend does two
things and nothing else: report the layout, and return one monitor's pixels. It
makes no decisions about permissions, crops or storage — those belong to
:mod:`brain.occipital_lobe.screen_capture`, which is the only thing that should be
deciding anything about a screenshot.

:class:`StaticBackend` is the fake the tests and the dry-run preview share. It
exists in production rather than in ``tests/`` for the reason
:class:`body.hands.recorder.RecordingActuator` does: a dry-run preview needs the
same stand-in a test does, and a preview that reached the real framebuffer would
photograph it — the one thing this region refuses to do unprompted.

The real ``mss`` backend lives in :mod:`brain.occipital_lobe.mss_backend`, so that
importing this module costs nothing on a machine with no display.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from brain.occipital_lobe import image_ops

#: What ``grab`` returns: PNG bytes, and the rectangle they came from.
Grabbed = Tuple[bytes, Dict[str, Any]]


class CaptureBackend:
    """The interface the Occipital Lobe captures through."""

    def layout(self) -> List[Dict[str, Any]]:
        """Return every attached monitor.

        Returns:
            One record per monitor: ``left``, ``top``, ``width``, ``height`` and
            ``primary``. Index order is the capture index.
        """
        raise NotImplementedError

    def grab(self, monitor: int = 0) -> Grabbed:
        """Return one monitor's pixels.

        Args:
            monitor: Zero-based index into :meth:`layout`.

        Returns:
            PNG bytes and the rectangle they came from.
        """
        raise NotImplementedError


class StaticBackend(CaptureBackend):
    """A backend that returns fixed images, for tests and dry-run previews.

    Attributes:
        images: PNG bytes keyed by monitor index. An index with no entry falls
            back to monitor 0, so a one-image fake still serves a two-monitor
            layout — which is the case that catches code assuming the secondary
            display has its own pixels.
    """

    def __init__(
        self,
        images: Dict[int, bytes],
        monitors: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> None:
        """Build the backend.

        Args:
            images: PNG bytes per monitor index.
            monitors: The layout to report. Defaults to one primary monitor sized
                to the first image, read from the PNG header.
        """
        self.images = dict(images)
        self._monitors = (
            [dict(monitor) for monitor in monitors] if monitors else _default_layout(self.images)
        )

    def layout(self) -> List[Dict[str, Any]]:
        """Return the fixed layout."""
        return [dict(monitor) for monitor in self._monitors]

    def grab(self, monitor: int = 0) -> Grabbed:
        """Return the fixed image for one monitor.

        Args:
            monitor: Zero-based index into :meth:`layout`.

        Returns:
            PNG bytes and the rectangle they came from.

        Raises:
            IndexError: The monitor has no image and there is no fallback either.
        """
        rect = self._monitors[monitor]
        if monitor in self.images:
            data = self.images[monitor]
        elif 0 in self.images:
            data = self.images[0]
        else:
            raise IndexError(f"no image configured for monitor {monitor}")
        return data, {key: rect[key] for key in ("left", "top", "width", "height")}


def _default_layout(images: Dict[int, bytes]) -> List[Dict[str, Any]]:
    """Return a one-monitor layout matching the fake images' own size.

    Args:
        images: PNG bytes per monitor index.

    Returns:
        A single primary monitor sized to the first image, or 1920x1080 when the
        images carry no readable PNG header. A fake is a test's own construction,
        so the fallback is a conventional desktop rather than an error.
    """
    size = image_ops.png_size(images.get(0, b"")) or (1920, 1080)
    return [{"left": 0, "top": 0, "width": size[0], "height": size[1], "primary": True}]