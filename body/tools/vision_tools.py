"""Body/Tools — screen vision.

Five tools wrapping the Occipital Lobe's capabilities:
- vision_capture_screen: take a screenshot
- vision_describe_screen: natural-language description of the screen
- vision_find_element: locate a UI element by description
- vision_read_text: OCR text extraction from a region
- vision_verify_changed: compare two captures for a state change

All are categorised ``visual_capture`` because a screen routinely holds a logged-in
banking session, a password manager or a password field with nothing in the
caller's request to say so. The same argument that made the Occipital Lobe's
category separate from ``read_files`` applies here: the user must approve the
capture, not merely the read.
"""

from __future__ import annotations

import base64
from typing import Any, Dict, Optional, Sequence

from brain.occipital_lobe.capture import Capture
from brain.occipital_lobe.exceptions import ElementNotFound
from brain.occipital_lobe.occipital_lobe import OccipitalLobe
from brain.occipital_lobe.visual_verifier import verify
from brain.prefrontal_cortex.action import ActionCategory
from body.tools.tool_spec import ToolContext, ToolRefused, ToolSpec, ToolTarget, required_text


def _get_lobe(context: ToolContext) -> OccipitalLobe:
    """Return the Occipital Lobe from context, refusing when absent."""
    lobe = getattr(context, "occipital_lobe", None)
    if lobe is None:
        raise ToolRefused("vision tools require the Occipital Lobe, which is not attached")
    if not isinstance(lobe, OccipitalLobe):
        raise ToolRefused("context.occipital_lobe is not an OccipitalLobe instance")
    return lobe


def _region_to_sequence(region: Optional[Dict[str, int]]) -> Optional[Sequence[int]]:
    """Convert a region dict to the (x, y, width, height) sequence the lobe expects."""
    if region is None:
        return None
    try:
        return (
            int(region["x"]),
            int(region["y"]),
            int(region["width"]),
            int(region["height"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolRefused(f"region must be {{x, y, width, height}} integers: {exc}") from exc


def _action_for(
    lobe: OccipitalLobe,
    operation: str,
    monitor: int,
    region: Optional[Sequence[int]],
) -> Any:
    """Return the Occipital Lobe's own proposed action for one operation.

    Taken from the Lobe rather than spelled out here for one reason that matters
    more than it looks: :func:`vision_guards.same_target` matches **exactly**, so
    the target the gate judges has to be character-for-character the target the
    Lobe re-checks when it executes. Two spellings of the same thing — and
    ``monitor:0 describe`` against ``describe:monitor=0`` are two spellings —
    means every approved vision call is refused at the last line with a replay
    error, which reads to the user as a broken camera rather than a typo.

    It also means resolve must *not* call :meth:`OccipitalLobe._verify`. Resolve
    runs before the gate, so no decision exists yet; passing ``None`` there is a
    ``DecisionRefused`` on every call, forever.
    """
    return lobe.propose(operation, monitor, region)


def _capture_to_base64(capture: Capture) -> str:
    """Encode a capture's image bytes as base64 for return to the model."""
    return base64.b64encode(capture.image).decode("ascii")


def _resolve_vision_capture(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a vision capture operation."""
    monitor = int(params.get("monitor", 0))
    region = _region_to_sequence(params.get("region"))
    lobe = _get_lobe(context)
    action = _action_for(lobe, "capture", monitor, region)
    return ToolTarget(
        category=str(ActionCategory.VISUAL_CAPTURE),
        target=action.target,
        preview={
            "description": f"Capture screen monitor {monitor}" + (f" region {region}" if region else ""),
            "monitor": monitor,
            "region": region,
        },
    )


def _execute_vision_capture(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Execute a vision capture."""
    lobe = _get_lobe(context)
    monitor = int(params.get("monitor", 0))
    region = _region_to_sequence(params.get("region"))
    capture = lobe.capture(decision, monitor=monitor, region=region, label="tool")
    return {
        "image_base64": _capture_to_base64(capture),
        # Read through the capture's own summary rather than reassembling the
        # fields here. Two spellings of one capture is how "timestamp" became
        # an AttributeError and a working screenshot came back as an internal
        # error.
        **capture.summary(),
    }


def _resolve_vision_describe(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a vision describe operation."""
    monitor = int(params.get("monitor", 0))
    region = _region_to_sequence(params.get("region"))
    lobe = _get_lobe(context)
    action = _action_for(lobe, "describe", monitor, region)
    prompt = params.get("prompt")
    return ToolTarget(
        category=str(ActionCategory.VISUAL_CAPTURE),
        target=action.target,
        preview={
            "description": f"Describe screen monitor {monitor}" + (f" region {region}" if region else "") + (f": {prompt}" if prompt else ""),
            "monitor": monitor,
            "region": region,
            "prompt": prompt,
        },
    )


async def _execute_vision_describe(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Execute a vision describe."""
    lobe = _get_lobe(context)
    monitor = int(params.get("monitor", 0))
    region = _region_to_sequence(params.get("region"))
    question = params.get("prompt", "")
    result = await lobe.describe(decision, monitor=monitor, region=region, question=question)
    desc = result.get("description", {})
    return {
        "description": desc.get("text") if isinstance(desc, dict) else str(desc),
        "confidence": result.get("confidence"),
        "status": result.get("status"),
        "needs_confirmation": result.get("needs_confirmation"),
        "capture": result.get("capture"),
    }


def _resolve_vision_find(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a vision find element operation."""
    description = required_text(params, "description", "vision_find_element")
    monitor = int(params.get("monitor", 0))
    region = _region_to_sequence(params.get("region"))
    lobe = _get_lobe(context)
    action = _action_for(lobe, "find", monitor, region)
    return ToolTarget(
        category=str(ActionCategory.VISUAL_CAPTURE),
        # The user's description rides along in the *preview*, never in the target:
        # ``same_target`` compares the target, so folding free text into it would
        # make every approval single-use.
        target=action.target,
        preview={
            "description": f"Find '{description}' on monitor {monitor}" + (f" region {region}" if region else ""),
            "monitor": monitor,
            "region": region,
            "description": description,
        },
    )


async def _execute_vision_find(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Execute a vision find element."""
    lobe = _get_lobe(context)
    description = required_text(params, "description", "vision_find_element")
    monitor = int(params.get("monitor", 0))
    region = _region_to_sequence(params.get("region"))
    try:
        result = await lobe.find_element(decision, description=description, monitor=monitor, region=region)
        return {
            "status": result.get("status"),
            "confidence": result.get("confidence"),
            "box_image": result.get("box_image"),
            "centre_image": result.get("centre_image"),
            "centre_desktop": result.get("centre_desktop"),
            "normalised_box": result.get("normalised_box"),
            "capture": result.get("capture"),
        }
    except ElementNotFound as exc:
        raise ToolRefused(str(exc)) from exc


def _resolve_vision_read_text(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a vision read text operation."""
    region = _region_to_sequence(params.get("region"))
    if region is None:
        raise ToolRefused("vision_read_text requires a region parameter")
    monitor = int(params.get("monitor", 0))
    source = str(params.get("source", "current"))
    lobe = _get_lobe(context)
    action = _action_for(lobe, "ocr", monitor, region)
    return ToolTarget(
        category=str(ActionCategory.VISUAL_CAPTURE),
        target=action.target,
        preview={
            "description": f"Read text from monitor {monitor} region {region} (source: {source})",
            "monitor": monitor,
            "region": region,
            "source": source,
        },
    )


async def _execute_vision_read_text(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Execute a vision read text."""
    lobe = _get_lobe(context)
    region = _region_to_sequence(params.get("region"))
    if region is None:
        raise ToolRefused("vision_read_text requires a region parameter")
    monitor = int(params.get("monitor", 0))
    source = str(params.get("source", "current"))
    if source == "stored":
        raise ToolRefused("stored source not yet implemented; use 'current'")
    result = await lobe.read_text(decision, region=region, monitor=monitor)
    text_obj = result.get("text", {})
    return {
        "text": text_obj.get("text") if isinstance(text_obj, dict) else str(text_obj),
        "confidence": result.get("confidence"),
        "status": result.get("status"),
        "capture": result.get("capture"),
    }


def _resolve_vision_verify(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a vision verify changed operation."""
    before = required_text(params, "before", "vision_verify_changed")
    after = required_text(params, "after", "vision_verify_changed")
    monitor = int(params.get("monitor", 0))
    lobe = _get_lobe(context)
    action = _action_for(lobe, "verify", monitor, None)
    return ToolTarget(
        category=str(ActionCategory.VISUAL_CAPTURE),
        target=action.target,
        preview={
            "description": f"Compare captures: {before} vs {after}",
            "before": before,
            "after": after,
        },
    )


def _execute_vision_verify(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Execute a vision verify changed."""
    before = required_text(params, "before", "vision_verify_changed")
    after = required_text(params, "after", "vision_verify_changed")
    monitor = int(params.get("monitor", 0))
    lobe = _get_lobe(context)
    result = lobe.compare(decision, before=before, after=after, label="tool-verify", monitor=monitor)
    return {
        "changed": result.get("changed"),
        "reason": result.get("reason"),
        "score": result.get("score"),
        "label": result.get("label"),
    }


#: The vision tools.
VISION_TOOLS: tuple = (
    ToolSpec(
        name="vision_capture_screen",
        summary="Take a screenshot of a monitor or region",
        resolve=_resolve_vision_capture,
        execute=_execute_vision_capture,
        returns="Base64-encoded PNG with metadata",
    ),
    ToolSpec(
        name="vision_describe_screen",
        summary="Describe what is on the screen in natural language",
        resolve=_resolve_vision_describe,
        execute=_execute_vision_describe,
        returns="Description, confidence, and threshold verdict",
    ),
    ToolSpec(
        name="vision_find_element",
        summary="Locate a UI element by description and return coordinates",
        resolve=_resolve_vision_find,
        execute=_execute_vision_find,
        returns="Element record with box, centre, and confidence",
    ),
    ToolSpec(
        name="vision_read_text",
        summary="Extract text from a screen region using OCR",
        resolve=_resolve_vision_read_text,
        execute=_execute_vision_read_text,
        returns="OCR text marked untrusted, with confidence",
    ),
    ToolSpec(
        name="vision_verify_changed",
        summary="Compare two screenshots to verify a state change",
        resolve=_resolve_vision_verify,
        execute=_execute_vision_verify,
        returns="Comparison report with changed flag, reason, and score",
    ),
)