"""Occipital Lobe — how confident is a located element, really?

Separated from :mod:`brain.occipital_lobe.element_finder` because the answer is the
whole safety argument and it should be readable without the transport and gating
code around it.

The confidence is **derived from observable evidence, never invented**, and the two
components are visible in the output so a reviewer can see why a number came out the
way it did:

* **The model's own number**, weighted at :data:`MODEL_WEIGHT`. Moondream states a
  confidence but does not calibrate it, so it is trusted as a signal and not as a
  measurement — hence well under half the total.
* **Label agreement**, weighted at :data:`LABEL_WEIGHT`. A model that found the
  Search bar usually says so, so overlap between what was asked for and what came
  back is weak corroboration. Small on purpose: a model that answers "Search bar"
  while pointing at the logo is a known failure, and this component should not be
  able to talk a bad box into looking good.

An answer with **no stated confidence scores 0.0**, not a default. This is the single
most important line in the region: a made-up mid-range number would satisfy the
``describe_scene`` threshold, and the region would then offer a coordinate it has no
evidence for while appearing to have weighed it.
"""

from __future__ import annotations

import re
from typing import Any, Dict

#: How much the model's own confidence is trusted, when it gave one.
MODEL_WEIGHT = 0.75

#: How much the label agreement is trusted.
LABEL_WEIGHT = 0.25

#: Words too common to be evidence of anything. "button" matches every button on the
#: screen, so agreeing with it says nothing about which one was found.
STOPWORDS = frozenset({"the", "a", "an", "of", "in", "on", "at", "to", "button", "input", "field"})


def score(model_confidence: float, description: str, answered: str) -> float:
    """Return the derived confidence for one located element.

    Args:
        model_confidence: What the model said it was sure of, 0.0 when it said
            nothing.
        description: What was asked for.
        answered: What the model said.

    Returns:
        The derived confidence in 0.0-1.0, or 0.0 when the model stated no confidence.
    """
    if model_confidence <= 0.0:
        return 0.0
    agreement = agreement_score(description, answered)
    return round(min(1.0, MODEL_WEIGHT * model_confidence + LABEL_WEIGHT * agreement), 3)


def agreement_score(description: str, answered: str) -> float:
    """Return how well the answer's own words match what was asked for.

    Args:
        description: What was asked for.
        answered: What the model said.

    Returns:
        Token overlap in 0.0-1.0, and 0.0 when either side has no content words —
        a genuine zero rather than an unknown.
    """
    asked = content_words(description)
    said = content_words(answered)
    if not asked or not said:
        return 0.0
    return round(len(asked & said) / len(asked), 3)


def content_words(text: str) -> set:
    """Return the content words in some text, lower-cased.

    Args:
        text: Any text.

    Returns:
        Words of three or more characters that are not stopwords. The length floor
        drops the ``in`` and ``of`` that survive stopword filtering only in some
        phrasings.
    """
    return {
        word
        for word in re.findall(r"[a-z0-9]+", str(text).lower())
        if len(word) >= 3 and word not in STOPWORDS
    }


def record(
    capture: Any,
    box: Any,
    confidence: float,
    verdict: Dict[str, Any],
    description: str,
    answered: str,
) -> Dict[str, Any]:
    """Assemble the record a caller acts on, in both coordinate spaces.

    Pixel and normalised coordinates are both returned because they answer different
    questions. A pixel coordinate is what a click needs; a normalised one survives
    being compared against a capture of a different size, which is how "the button
    is in the same place as last time" becomes checkable at all.

    Args:
        capture: The
            :class:`~brain.occipital_lobe.capture.Capture` the box was found in.
        box: ``(left, top, width, height)`` in image pixels.
        confidence: The derived confidence.
        verdict: The threshold verdict from
            :func:`brain.occipital_lobe.thresholds.verdict`.
        description: What was asked for.
        answered: What the model said.

    Returns:
        The record, including desktop coordinates for the centre so a caller can hand
        them straight to the Hands layer without redoing the origin arithmetic.
    """
    left, top, width, height = (int(value) for value in box)
    centre_x, centre_y = left + width // 2, top + height // 2
    desktop_x, desktop_y = capture.to_desktop(centre_x, centre_y)
    return {
        "status": verdict["status"],
        "found": verdict["status"] == "accepted",
        "needs_confirmation": verdict["status"] == "needs_confirmation",
        "description": description,
        "confidence": confidence,
        "threshold": verdict["threshold"],
        "refuse_below": verdict["refuse_below"],
        "job": verdict["job"],
        "box": {"x": left, "y": top, "width": width, "height": height},
        # The shape the Phase 3 plan asked for: a point and a confidence.
        "x": centre_x,
        "y": centre_y,
        "center": {"x": centre_x, "y": centre_y},
        "desktop_center": {"x": desktop_x, "y": desktop_y},
        "normalized": {
            "x": _fraction(left, capture.width),
            "y": _fraction(top, capture.height),
            "width": _fraction(width, capture.width),
            "height": _fraction(height, capture.height),
            "center_x": _fraction(centre_x, capture.width),
            "center_y": _fraction(centre_y, capture.height),
        },
        "image_size": {"width": capture.width, "height": capture.height},
        "monitor": capture.monitor,
        "answered": answered.strip()[:200],
    }


def _fraction(value: float, total: int) -> float:
    """Return one coordinate as a fraction of the frame.

    Args:
        value: A pixel coordinate.
        total: The frame's size along that axis.

    Returns:
        The fraction to four places. A zero-sized frame gives 0.0 rather than raising,
        because this runs while assembling a report and a report that fails is worse
        than a report with one unusable field.
    """
    if not total:
        return 0.0
    return round(value / total, 4)