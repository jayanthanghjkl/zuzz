"""Occipital Lobe — vision thresholds.

Reads ``config/vision_thresholds.json`` and answers two questions the region asks
constantly: *how confident does an answer have to be before it is acted on
silently*, and *what happens below that*.

The file is re-read on every lookup rather than cached. It is the intended place to
tune the region on real hardware, where the useful numbers will differ from any
value guessed before a single screenshot has been taken, and a stale cache would
mean the region enforcing a confidence the user no longer believes in.

The three-way answer is the point. ``refuse_below`` is not the same number as a job's
threshold: below the job's threshold the answer is right often enough to show the
user and ask, and below ``refuse_below`` it is not right often enough to show at
all, because offering a coordinate ZUZZ believes is wrong teaches it to ignore the
offer. A wrong click is worse than no click.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from core.config import get_vision_thresholds

#: Used when the config is missing or unreadable. Deliberately conservative: the
#: flat 70% the Phase 3 plan proposed before the thresholds were split per job,
#: rounded up to the lowest per-job value rather than down to the average, so a
#: deleted config file makes the region stricter instead of looser.
DEFAULT_THRESHOLD = 0.70
DEFAULT_REFUSE_BELOW = 0.50

#: The jobs the config names, and what each one is for. A job not listed here is a
#: programming error rather than a configuration choice.
JOBS = (
    "click_or_form_fill",
    "information_extract",
    "describe_scene",
    "ocr_text_read",
)


def load() -> Dict[str, Any]:
    """Return the vision threshold configuration.

    Returns:
        The parsed ``config/vision_thresholds.json``, or an empty dict when it is
        absent. Callers use the accessors below rather than indexing it, so a
        missing or partial file degrades to the documented defaults instead of
        raising in the middle of a capture.
    """
    return get_vision_thresholds()


def threshold_for(job: str) -> Dict[str, Any]:
    """Return the threshold policy for one job.

    Args:
        job: One of :data:`JOBS`.

    Returns:
        ``{"value": float, "on_below": str, "why": str}``. The defaults are used
        for any field the config does not supply, so a hand-edited file with only
        ``value`` set still behaves sensibly.
    """
    entry = load().get("thresholds", {}).get(job, {})
    if not isinstance(entry, dict):
        entry = {}
    return {
        "value": _bounded(entry.get("value"), DEFAULT_THRESHOLD),
        "on_below": str(entry.get("on_below", "ask")),
        "why": str(entry.get("why", "")),
    }


def refuse_below() -> float:
    """Return the confidence under which an answer is declined outright."""
    return _bounded(load().get("refuse_below"), DEFAULT_REFUSE_BELOW)


def on_refuse() -> str:
    """Return what to do below :func:`refuse_below`."""
    return str(load().get("on_refuse", "decline and propose an alternative approach"))


def fallback_policy() -> Dict[str, Any]:
    """Return the OCR fallback ladder.

    Returns:
        The ``fallback`` block, with defaults filled in: Moondream first,
        Tesseract as the second read, consulted when the first answer's confidence
        is under ``secondary_trigger_below``.
    """
    block = load().get("fallback", {})
    if not isinstance(block, dict):
        block = {}
    return {
        "primary": str(block.get("primary", "moondream")),
        "secondary": str(block.get("secondary", "tesseract")),
        "secondary_trigger_below": _bounded(
            block.get("secondary_trigger_below"), 0.85
        ),
        "on_agreement": str(block.get("on_agreement", "use the agreed text")),
        "on_disagreement": str(block.get("on_disagreement", "")),
        "on_total_failure": str(block.get("on_total_failure", "")),
    }


def verdict(confidence: float, job: str) -> Dict[str, Any]:
    """Decide what a confidence means for one job.

    Args:
        confidence: The confidence the model reported, 0.0-1.0. Values outside
            the range are clamped rather than trusted, because a model returning
            ``1.4`` should not read as more certain than one returning ``1.0``.
        job: One of :data:`JOBS`.

    Returns:
        ``{"status", "confidence", "threshold", "refuse_below", "why"}`` where
        ``status`` is ``"accepted"``, ``"needs_confirmation"`` or ``"declined"``.
    """
    policy = threshold_for(job)
    score = _bounded(confidence, 0.0)
    floor = refuse_below()
    if score >= policy["value"]:
        status = "accepted"
    elif score >= floor:
        status = "needs_confirmation"
    else:
        status = "declined"
    return {
        "status": status,
        "confidence": score,
        "threshold": policy["value"],
        "refuse_below": floor,
        "job": job,
        "on_refuse": on_refuse(),
        "why": policy["why"],
    }


def _bounded(value: Any, default: float) -> float:
    """Return a confidence clamped into 0.0-1.0.

    Args:
        value: The configured value.
        default: Used when the value is missing or not a number.

    Returns:
        The clamped value.
    """
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default