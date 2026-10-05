"""Occipital Lobe — the region's own status report.

What the UI needs to show before it asks the region anything: which model is loaded,
whether it would fit, what the confidence thresholds are, how many monitors are
attached, and — most importantly — what the prompt-isolation policy says.

Nothing here needs a permission decision. It is the same class of read as the trust
level or the VRAM gauge: a panel nobody would look at if it needed a confirmation, and
the numbers a user needs in order to decide whether to grant one. It reads
configuration and asks the Hypothalamus; it never touches the screen.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from brain.occipital_lobe import thresholds, vision_resources


def build(
    hypothalamus: Optional[Any],
    monitors: List[Dict[str, Any]],
    prompts: Dict[str, Any],
    stored_count: int = 0,
) -> Dict[str, Any]:
    """Return the region's status for the UI and the API.

    Args:
        hypothalamus: The Hypothalamus, or None when none is wired.
        monitors: The monitor layout, already read by the caller.
        prompts: The prompt-isolation policy from
            :meth:`brain.occipital_lobe.vision_client.VisionClient.describe_prompts`.
        stored_count: How many screenshots are on disk.

    Returns:
        The model and budget state, the threshold table, the monitors and the
        isolation policy.
    """
    loaded: List[str] = []
    budget: Dict[str, Any] = {}
    if hypothalamus is not None:
        loaded = [entry.get("model") for entry in hypothalamus.models.loaded_models()]
        budget = hypothalamus.models.can_load(vision_resources.MODEL)
    return {
        "model": vision_resources.MODEL,
        "loaded": vision_resources.MODEL in loaded,
        "loaded_models": loaded,
        "budget": budget,
        "monitors": monitors,
        "stored_screenshots": stored_count,
        "thresholds": {job: thresholds.threshold_for(job) for job in thresholds.JOBS},
        "refuse_below": thresholds.refuse_below(),
        "on_refuse": thresholds.on_refuse(),
        "fallback": thresholds.fallback_policy(),
        "prompts": prompts,
    }