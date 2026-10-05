"""Occipital Lobe — resource admission for the vision model.

Moondream is ~1.5 GB of VRAM on a card with 6 GB, and the budget in
``config/resource_limits.json`` reserves 1.5 GB of headroom on top of a 4.5 GB cap.
Loading it without asking the Hypothalamus would blow straight through that on a
machine that already has the 3B model resident, and running out of VRAM mid-inference
can take the GPU driver — and the user's whole desktop — with it.

So every model call is admitted here first, and the check is the Hypothalamus's own
rather than a copy of it. The verdict is answered, not raised, because the caller
needs the reason in order to say something useful: "no VRAM, free a model" is a
different sentence from "Ollama is not running".

A refusal is written to the audit log as :data:`~core.audit.VISUAL_MODEL_REFUSED`
rather than as ``blocked``. The permission gate never saw this request — the resource
layer reached the answer on its own, with no decision involved — and recording it as a
gate decision would attribute to the gate something it did not do.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from brain.occipital_lobe.exceptions import VisionModelUnavailable

#: The model name the region asks for. Matches the ``moondream`` profile in
#: ``config/models.json``, which is where the 1600 MB figure comes from.
MODEL = "moondream"


def admit(hypothalamus: Optional[Any], model: str = MODEL, audit: Optional[Any] = None) -> Dict[str, Any]:
    """Ask the Hypothalamus whether the vision model may load.

    Args:
        hypothalamus: The region's
            :class:`~brain.hypothalamus.hypothalamus.Hypothalamus`, or None.
        model: The model to check.
        audit: Audit log; None means a refusal is not recorded.

    Returns:
        The budget verdict, with a ``required`` flag saying whether a decision is
        still needed before :meth:`~brain.hypothalamus.model_manager.ModelManager.load`
        may be called. With no Hypothalamus the verdict is ``required`` and
        ``allowed``, because the model manager's own gate is what will actually
        enforce the decision — this function only answers the resource question.

    Raises:
        VisionModelUnavailable: The model will not fit in the VRAM budget, or the
            resource layer is in cloud-only mode.
    """
    if hypothalamus is None:
        return {
            "model": model,
            "required": True,
            "allowed": True,
            "reason": "no resource monitor wired; the model manager's own gate will decide",
        }
    verdict = hypothalamus.models.can_load(model)
    payload = {**verdict, "required": bool(verdict.get("allowed", False))}
    if payload.get("allowed"):
        return payload
    if audit is not None:
        audit.record(
            "visual_model_refused",
            f"{model} was not loaded: {payload.get('reason', 'budget')}",
            category="visual_capture",
            target=model,
            level="blocked",
            required_vram_mb=payload.get("required_vram_mb"),
            available_vram_mb=payload.get("available_vram_mb"),
        )
    raise VisionModelUnavailable(
        f"{model} cannot run right now: {payload.get('reason', 'it does not fit')}. "
        "Free a model, wait for the budget to clear, or route the task to the cloud.",
        reason="vram_budget",
    )