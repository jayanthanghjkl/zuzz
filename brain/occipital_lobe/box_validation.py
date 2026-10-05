"""Occipital Lobe — deciding whether a parsed box can be clicked.

Separated from :mod:`brain.occipital_lobe.box_parser` because this is where the
safety argument lives, and it should be readable without the regexes in the way.

Every refusal here exists because the alternative is a click in the wrong place:

* **too small** — a two-pixel box is a rounding artefact, and its centre is a coin
  toss;
* **starts off the image** — a negative coordinate means the model's frame and ZUZZ's
  frame disagree, and one of them is wrong about where the screen starts;
* **runs off the image** — the same disagreement, at the far edge;
* **is the whole screen** — a model asked for a button will sometimes return the
  frame. Clicking its centre is clicking the middle of the page, which is a worse
  failure than admitting it found nothing.

A box is normalised to ``(left, top, width, height)`` only after it has passed. The
rounding is to the nearest pixel rather than to a floor: a box that is 0.4 pixels
short of the edge is a box at the edge, and refusing it would be refusing a rounding
difference.
"""

from __future__ import annotations

from typing import Optional, Tuple

from brain.occipital_lobe.box_result import BackwardsBox, ParsedBox

#: Below this, in pixels, a box is not a control.
MIN_SIDE = 2

#: Above this fraction of the frame, a "box" is the screen rather than an element.
#: 0.98 rather than 1.0 because a model asked to locate a full-screen dialog will
#: sometimes answer with the whole frame, and that is a real answer to a different
#: question.
MAX_AREA_FRACTION = 0.98


def normalise_edges(x1: float, y1: float, x2: float, y2: float, width: int, height: int) -> Optional[Tuple[float, float, float, float]]:
    """Rescale a parsed box and check its corner order.

    Args:
        x1: Left edge as the model gave it.
        y1: Top edge.
        x2: Right edge.
        y2: Bottom edge.
        width: Image width in pixels.
        height: Image height in pixels.

    Returns:
        ``(left, top, width, height)``, or None when the corners run backwards or the
        numbers were clearly normalised 0-1 rather than pixel coordinates.

    Raises:
        BackwardsBox: The box runs backwards, so which corner is which is ambiguous.
    """
    if max(x1, y1, x2, y2) <= 1.0:
        x1, y1, x2, y2 = x1 * width, y1 * height, x2 * width, y2 * height
    if x2 < x1 or y2 < y1:
        raise BackwardsBox((x1, y1, x2, y2))
    return (x1, y1, x2 - x1, y2 - y1)


def validate(
    box: Tuple[float, float, float, float],
    confidence: float,
    width: int,
    height: int,
) -> ParsedBox:
    """Check a box against the image it claims to describe.

    Args:
        box: ``(left, top, width, height)``, possibly fractional.
        confidence: The model's confidence, carried through so a refusal still
            reports what the model believed.
        width: Image width in pixels.
        height: Image height in pixels.

    Returns:
        The integer box, or a :class:`ParsedBox` whose ``reason`` says why it was
        refused.
    """
    left, top, box_width, box_height = box
    rounded = (round(left), round(top), round(box_width), round(box_height))
    if rounded[2] < MIN_SIDE or rounded[3] < MIN_SIDE:
        return ParsedBox((0, 0, 0, 0), confidence, f"box {rounded} is too small to click")
    if rounded[0] < 0 or rounded[1] < 0:
        return ParsedBox((0, 0, 0, 0), confidence, f"box {rounded} starts off the image")
    if rounded[0] + rounded[2] > width or rounded[1] + rounded[3] > height:
        return ParsedBox((0, 0, 0, 0), confidence, f"box {rounded} runs off the {width}x{height} image")
    if rounded[2] * rounded[3] > MAX_AREA_FRACTION * width * height:
        return ParsedBox((0, 0, 0, 0), confidence, f"box {rounded} is the whole screen, not an element")
    return ParsedBox(rounded, confidence)