"""Occipital Lobe — one screenshot, as data.

Separated from :mod:`brain.occipital_lobe.screen_capture` because everything else
in the region passes these around without ever producing one, and because the
coordinate arithmetic here deserves to be read on its own. A box read out of an
image is only useful if it can be turned back into a desktop coordinate, and the
only place that conversion exists is :attr:`Capture.to_desktop`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

#: Regions are recorded as this. ``None`` means "the whole monitor", which is a
#: different thing from ``(0, 0, w, h)`` — the first is a display, the second is a
#: crop of one — and the two produce different coordinates for the same click.
Region = Optional[Tuple[int, int, int, int]]


@dataclass(frozen=True)
class Capture:
    """One screenshot, as bytes plus where it came from.

    Attributes:
        image: PNG bytes.
        width: Image width in pixels.
        height: Image height in pixels.
        monitor: Index of the monitor it came from.
        origin: Desktop pixel coordinates of the image's top-left corner, so an
            image-local box can be mapped back to a clickable coordinate.
        region: The crop that was requested, monitor-local, or None for the whole
            monitor.
        captured_at: Unix timestamp of the capture.
        label: Short name the caller gave it, used in the stored filename.
        extra: Free-form detail for the caller and the log.
    """

    image: bytes
    width: int
    height: int
    monitor: int = 0
    origin: Tuple[int, int] = (0, 0)
    region: Region = None
    captured_at: float = field(default_factory=time.time)
    label: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def to_desktop(self) -> Callable[[float, float], Tuple[int, int]]:
        """Return a function mapping image-local coordinates to desktop ones.

        Returned as a closure rather than precomputed, because the origin is fixed
        but a caller may hold the function across a re-capture; building it on
        demand keeps the two tied together.

        Returns:
            A callable taking ``(x, y)`` and returning desktop pixel coordinates,
            clamped to the image's own extent so a rounding error cannot produce a
            point outside the capture.
        """

        def convert(x: float, y: float) -> Tuple[int, int]:
            """Return one image-local point in desktop pixels.

            Args:
                x: Horizontal position within the image.
                y: Vertical position within the image.

            Returns:
                The desktop coordinate pair.
            """
            left = min(max(0, int(x)), max(0, self.width - 1)) + self.origin[0]
            top = min(max(0, int(y)), max(0, self.height - 1)) + self.origin[1]
            return left, top

        return convert

    @property
    def desktop_bounds(self) -> Tuple[int, int, int, int]:
        """Return this capture's rectangle in desktop pixels.

        Returns:
            ``(left, top, width, height)``, i.e. the box a click validator would
            need to confirm the point is still on the same display.
        """
        return (self.origin[0], self.origin[1], self.width, self.height)

    def summary(self) -> Dict[str, Any]:
        """Return the metadata for the API, without the pixels.

        The image itself is left out on purpose: a screenshot is megabytes of
        base64 in a JSON response, and every caller that wanted one has a path to
        fetch it from the store instead.

        Returns:
            Size, monitor, origin, region, label and byte count.
        """
        return {
            "width": self.width,
            "height": self.height,
            "monitor": self.monitor,
            "origin": {"x": self.origin[0], "y": self.origin[1]},
            "region": None if self.region is None else dict(zip(("x", "y", "width", "height"), self.region)),
            "captured_at": round(self.captured_at, 3),
            "label": self.label,
            "bytes": len(self.image),
        }