"""Occipital Lobe — parsing a box out of what the model said.

Moondream does not return a structured bounding box from ``/api/generate``; it
returns a line of text and somebody has to read the numbers out of it. That somebody
is this module. It is separated from :mod:`brain.occipital_lobe.box_validation` so
that the two failure classes stay apart: *the model did not say a box* is a parsing
question answered here, and *the box it said cannot be clicked* is a safety question
answered there.

The rules this module enforces:

* **Four numbers, or nothing.** ``BOX none``, a sentence and a truncated reply are
  all "not found", never a partial guess.
* **Coordinates are rescaled, not trusted.** A model that answers in normalised 0-1
  coordinates because the image was large is as likely to be right as one answering
  in pixels, and which it did is decided from the numbers themselves. Accepting
  ``0.42`` as a pixel coordinate is how a click lands in the top-left corner.
* **Corner order is not repaired.** A box with ``x2 < x1`` raises rather than being
  flipped: which corner was meant is not knowable, and picking one is a random click.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple

from brain.occipital_lobe.box_result import BackwardsBox, ParsedBox
from brain.occipital_lobe.box_validation import normalise_edges, validate

#: ``BOX x1 y1 x2 y2 CONFIDENCE 0.93``.
BOX_LINE = re.compile(
    r"BOX\s+(?P<x1>-?\d+(?:\.\d+)?)\s+(?P<y1>-?\d+(?:\.\d+)?)\s+"
    r"(?P<x2>-?\d+(?:\.\d+)?)\s+(?P<y2>-?\d+(?:\.\d+)?)"
    r"(?:\s+CONFIDENCE\s+(?P<confidence>-?\d+(?:\.\d+)?))?",
    re.IGNORECASE,
)

#: The model saying there is nothing to find.
NO_BOX = re.compile(r"BOX\s+none", re.IGNORECASE)

#: A bare bracketed box, which is how Moondream often volunteers one when a prompt
#: nudges it towards coordinates.
BRACKETED = re.compile(r"\[\s*-?\d+\s*,\s*-?\d+\s*,\s*-?\d+\s*,\s*-?\d+\s*\]")


def parse_box(reply: str, width: int, height: int) -> ParsedBox:
    """Recover a bounding box from what the vision model said.

    Never raises. "The model did not find it" is an answer the caller reports to a
    user, not a fault, so it comes back as a :class:`ParsedBox` carrying the reason
    rather than as an exception.

    Args:
        reply: The model's answer.
        width: Image width in pixels, used to rescale and to validate.
        height: Image height in pixels.

    Returns:
        The box in image pixels with the model's confidence, or a :class:`ParsedBox`
        whose ``reason`` says why there is not one.
    """
    text = str(reply or "").strip()
    if not text or NO_BOX.search(text):
        return ParsedBox((0, 0, 0, 0), 0.0, "the model reported no box")
    match = BOX_LINE.search(text) or _bracketed(text)
    if match is None:
        return ParsedBox((0, 0, 0, 0), 0.0, "no box in the model's answer")
    numbers = _numbers(match)
    if numbers is None:
        return ParsedBox((0, 0, 0, 0), 0.0, "the box was not four numbers")
    confidence = _confidence(match, text)
    try:
        box = normalise_edges(*numbers, width, height)
    except BackwardsBox as exc:
        return ParsedBox((0, 0, 0, 0), confidence, str(exc))
    return validate(box, confidence, width, height)


class _Named:
    """A four-number match shim, so both parse paths yield the same shape.

    Indexable as well as attribute-accessible, because
    :func:`_numbers` reads ``match["x1"]`` — the same access a real
    :class:`re.Match` supports for a named group. Without ``__getitem__`` the
    bracketed path below this class could never be read, and every answer of the
    shape Moondream volunteers most often would be reported as "not four numbers".

    Attributes:
        x1: Left edge.
        y1: Top edge.
        x2: Right edge.
        y2: Bottom edge.
    """

    #: The named-group spelling of each edge, so ``match[name]`` resolves.
    NAMES = ("x1", "y1", "x2", "y2")

    def __init__(self, x1: float, y1: float, x2: float, y2: float) -> None:
        """Record the four numbers.

        Args:
            x1: Left edge.
            y1: Top edge.
            x2: Right edge.
            y2: Bottom edge.
        """
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2

    def __getitem__(self, name: str) -> float:
        """Return one edge by its named-group spelling.

        Args:
            name: One of :data:`NAMES`.

        Returns:
            That edge.

        Raises:
            KeyError: The name is not one of the four edges.
        """
        if name not in self.NAMES:
            raise KeyError(name)
        return float(getattr(self, name))

    def groupdict(self) -> dict:
        """Return the edges as a mapping, matching :meth:`re.Match.groupdict`.

        Returns:
            The four edges keyed by name, with the confidence absent — which is what
            :func:`_confidence` reads to learn that none was stated.
        """
        return {name: getattr(self, name) for name in self.NAMES}


def _bracketed(text: str) -> Optional[_Named]:
    """Return a bracketed box as a match-shaped object, or None.

    Args:
        text: The model's answer.

    Returns:
        The four numbers, or None when there is no bracketed box in the text.
    """
    found = BRACKETED.search(text)
    if found is None:
        return None
    values = [float(part) for part in re.findall(r"-?\d+", found.group(0))]
    return _Named(*values[:4]) if len(values) >= 4 else None


def _numbers(match: Any) -> Optional[Tuple[float, float, float, float]]:
    """Return a match's four edge numbers.

    Read through ``match[name]``, which both a real :class:`re.Match` and
    :class:`_Named` support. Attribute access looks like it would work on the former
    and does not: a named group is ``match["x1"]``, not ``match.x1``.

    Args:
        match: Anything indexable by ``"x1"``..``"y2"``.

    Returns:
        The four numbers as floats, or None when any is missing or unparseable.
    """
    try:
        return tuple(float(match[name]) for name in ("x1", "y1", "x2", "y2"))  # type: ignore[return-value]
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return None


def _confidence(match: Any, text: str) -> float:
    """Return the confidence the model gave, or 0.0 when it gave none.

    Args:
        match: The parsed match, which may carry the number itself.
        text: The whole answer, searched as a fallback.

    Returns:
        A confidence in 0.0-1.0, or 0.0. Zero rather than a plausible middle value:
        the caller's thresholds treat a low number as "ask", and a default here would
        let an uncalibrated answer through that gate without ever consulting it.
    """
    stated = match.groupdict().get("confidence") if hasattr(match, "groupdict") else None
    if stated is None:
        fallback = re.search(r"CONFIDENCE\s+(-?\d+(?:\.\d+)?)", text, re.IGNORECASE)
        stated = fallback.group(1) if fallback else None
    if stated is None:
        return 0.0
    try:
        return max(0.0, min(1.0, float(stated)))
    except (TypeError, ValueError):
        return 0.0