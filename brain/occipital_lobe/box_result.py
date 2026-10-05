"""Occipital Lobe — what a parsed box is, and when a box cannot be read at all.

Two tiny types shared by :mod:`brain.occipital_lobe.box_parser` and
:mod:`brain.occipital_lobe.box_validation`, in their own module so the two can depend
on each other's results without depending on each other.

:class:`BackwardsBox` is its own exception rather than a sentinel string because it is
the one parse failure that must never be normalised into a click: a model that
answered ``(400, 90, 300, 60)`` may have meant ``x1`` is the right edge, and ZUZZ
cannot tell which reading was intended. Swapping the corners to make it parse picks
one at random — and a random click is a click on whatever happened to be in the
middle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class ParsedBox:
    """A bounding box read out of the model's answer.

    Attributes:
        box: ``(left, top, width, height)`` in image pixels.
        confidence: The model's own confidence, or 0.0 when it gave none.
        reason: Why the answer was rejected, when it was. Empty on success.
    """

    box: Tuple[int, int, int, int]
    confidence: float = 0.0
    reason: str = ""

    @property
    def found(self) -> bool:
        """Return True when a usable box was recovered."""
        return not self.reason


class BackwardsBox(Exception):
    """The model's box runs backwards, so which corner is which is ambiguous.

    Attributes:
        corners: The four numbers as given, ``(x1, y1, x2, y2)``.

    Raises:
        Exception: Always. Raised by
            :func:`brain.occipital_lobe.box_validation.normalise_edges`, caught by
            :func:`brain.occipital_lobe.box_parser.parse_box`, and turned into a
            :class:`ParsedBox` carrying the reason. It never reaches a caller.
    """

    def __init__(self, corners: Tuple[float, float, float, float]) -> None:
        """Record the offending corners.

        Args:
            corners: ``(x1, y1, x2, y2)`` as the model gave them.
        """
        super().__init__(
            f"box ({corners[0]:g}, {corners[1]:g}, {corners[2]:g}, {corners[3]:g}) "
            "runs backwards; which corner is which is ambiguous, so it is not clicked"
        )
        self.corners = corners