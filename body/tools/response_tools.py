"""Body/Tools — response enhancement.

Four read-only knobs for how ZUZZ's own replies are presented:
thinking steps, confidence scoring, proactive suggestions and
mood-adaptive tone. They are grouped in one module because they
answer one question — "how should ZUZZ sound" — and none of them
acts on the machine.

Every tool is judged as ``response_enhancement``, which is
``auto`` at every trust level, and the reason is the same one the
personality row argues: a prompt here would be "may I show you my
reasoning?" every single reply, and the answer is always yes. The
exposure is zero — no file, no window, no network, no model input
changes. What the tools *do* is bounded by the enhancer itself:
they flip flags the ResponseEnhancer already reads, so a tool call
cannot invent a behaviour the region does not have.

The tools refuse when the enhancer is not attached, following the
personality tools' rule: silently "succeeding" against nothing
would tell the model a knob was turned that nobody reads.
"""

from __future__ import annotations

from typing import Any, Dict

from body.tools.tool_spec import (
    ToolContext,
    ToolRefused,
    ToolSpec,
    ToolTarget,
    required_text,
)

#: The category every response-enhancement tool resolves to. See
#: ``config/trust_levels.json`` — ``auto`` at every level.
CATEGORY = "response_enhancement"

#: Bound on the confidence threshold, so a model cannot set a
#: threshold that would label every answer "high".
MIN_THRESHOLD = 0.05
MAX_THRESHOLD = 0.95

#: Bound on the suggestion count, so a model cannot ask for a
#: wall of cards after every reply.
MAX_SUGGESTIONS = 5


def _enhancer(context: ToolContext, tool: str):
    """Return the ResponseEnhancer, refusing when it is not attached.

    Args:
        context: The collaborators the tool may act through.
        tool: The tool name, for the message.

    Returns:
        The ResponseEnhancer.

    Raises:
        ToolRefused: When ``context.response_enhancer`` is None.
    """
    enhancer = context.response_enhancer
    if enhancer is None:
        raise ToolRefused(
            f"{tool}: response enhancement is not attached, so there is "
            "nothing to configure"
        )
    return enhancer


def _as_bool(params: Dict[str, Any], key: str, tool: str) -> bool:
    """Return a boolean parameter the schema declared as an enum.

    The schema declares ``enabled`` as ``"true"``/``"false"`` rather
    than a JSON boolean, matching how the rest of the catalogue spells
    binary choices for models that emit strings.

    Args:
        params: The validated parameters.
        key: The parameter name.
        tool: The tool name, for the message.

    Returns:
        The boolean value.

    Raises:
        ToolRefused: When the value is not ``"true"`` or ``"false"``.
    """
    value = required_text(params, key, tool).lower()
    if value not in ("true", "false"):
        raise ToolRefused(f"{tool}: {key} must be 'true' or 'false'")
    return value == "true"


def _settings(enhancer: Any) -> Dict[str, Any]:
    """Return the enhancer's current switches, for tool responses."""
    return {
        "thinking": bool(getattr(enhancer, "enable_thinking", False)),
        "confidence": bool(getattr(enhancer, "enable_confidence", False)),
        "continuation": bool(getattr(enhancer, "enable_continuation", False)),
        "suggestions": bool(getattr(enhancer, "enable_suggestions", False)),
        "critique": bool(getattr(enhancer, "enable_critique", False)),
        "variations": bool(getattr(enhancer, "enable_variations", False)),
    }


# -- response_show_thinking ------------------------------------------------


def _resolve_show_thinking(
    params: Dict[str, Any], context: ToolContext
) -> ToolTarget:
    """Resolve a thinking-display toggle."""
    _enhancer(context, "response_show_thinking")
    enabled = _as_bool(params, "enabled", "response_show_thinking")
    return ToolTarget(
        category=CATEGORY,
        target=f"thinking display={enabled}",
        preview={
            "description": (
                "Show the model's reasoning steps before each answer"
                if enabled
                else "Hide the model's reasoning steps"
            )
        },
        detail={"enabled": enabled},
    )


def _execute_show_thinking(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Toggle the thinking display and report the switches."""
    enhancer = _enhancer(context, "response_show_thinking")
    enhancer.enable_thinking = _as_bool(params, "enabled", "response_show_thinking")
    return {
        "thinking": enhancer.enable_thinking,
        "settings": _settings(enhancer),
    }


# -- response_set_confidence -----------------------------------------------


def _resolve_set_confidence(
    params: Dict[str, Any], context: ToolContext
) -> ToolTarget:
    """Resolve a confidence-scoring toggle."""
    _enhancer(context, "response_set_confidence")
    enabled = _as_bool(params, "enabled", "response_set_confidence")
    threshold = str(params.get("threshold", "")).strip()
    if threshold:
        try:
            value = float(threshold)
        except ValueError:
            raise ToolRefused(
                f"response_set_confidence: threshold {threshold!r} is not a number"
            )
        if not (MIN_THRESHOLD <= value <= MAX_THRESHOLD):
            raise ToolRefused(
                f"response_set_confidence: threshold must be between "
                f"{MIN_THRESHOLD} and {MAX_THRESHOLD}"
            )
    return ToolTarget(
        category=CATEGORY,
        target=f"confidence scoring={enabled}",
        preview={
            "description": (
                "Attach a confidence badge to each reply"
                if enabled
                else "Stop scoring reply confidence"
            )
        },
        detail={"enabled": enabled, "threshold": threshold or None},
    )


def _execute_set_confidence(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Toggle confidence scoring, optionally moving the "high" bar."""
    enhancer = _enhancer(context, "response_set_confidence")
    enabled = _as_bool(params, "enabled", "response_set_confidence")
    enhancer.enable_confidence = enabled
    threshold = str(params.get("threshold", "")).strip()
    moved = None
    if threshold:
        value = float(threshold)
        scorer = getattr(enhancer, "_confidence", None)
        if scorer is not None:
            scorer.high_threshold = value
            moved = value
    result = {"confidence": enhancer.enable_confidence, "settings": _settings(enhancer)}
    if moved is not None:
        result["high_threshold"] = moved
    return result


# -- response_suggest_proactive --------------------------------------------


def _resolve_suggest_proactive(
    params: Dict[str, Any], context: ToolContext
) -> ToolTarget:
    """Resolve a proactive-suggestions toggle."""
    _enhancer(context, "response_suggest_proactive")
    enabled = _as_bool(params, "enabled", "response_suggest_proactive")
    maximum = params.get("max_suggestions")
    if maximum is not None:
        maximum = int(maximum)
        if not (1 <= maximum <= MAX_SUGGESTIONS):
            raise ToolRefused(
                f"response_suggest_proactive: max_suggestions must be "
                f"between 1 and {MAX_SUGGESTIONS}"
            )
    return ToolTarget(
        category=CATEGORY,
        target=f"proactive suggestions={enabled}",
        preview={
            "description": (
                "Offer follow-up suggestion cards after each reply"
                if enabled
                else "Stop offering follow-up suggestions"
            )
        },
        detail={"enabled": enabled, "max_suggestions": maximum},
    )


def _execute_suggest_proactive(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Toggle suggestions, optionally moving the count cap."""
    enhancer = _enhancer(context, "response_suggest_proactive")
    enhancer.enable_suggestions = _as_bool(
        params, "enabled", "response_suggest_proactive"
    )
    maximum = params.get("max_suggestions")
    moved = None
    if maximum is not None:
        moved = int(maximum)
        suggester = getattr(enhancer, "_suggester", None)
        if suggester is not None:
            suggester.max_suggestions = moved
    result = {"suggestions": enhancer.enable_suggestions, "settings": _settings(enhancer)}
    if moved is not None:
        result["max_suggestions"] = moved
    return result


# -- response_adapt_tone ---------------------------------------------------


def _resolve_adapt_tone(
    params: Dict[str, Any], context: ToolContext
) -> ToolTarget:
    """Resolve a mood-adaptation toggle."""
    _enhancer(context, "response_adapt_tone")
    enabled = _as_bool(params, "enabled", "response_adapt_tone")
    return ToolTarget(
        category=CATEGORY,
        target=f"tone adaptation={enabled}",
        preview={
            "description": (
                "Reshape replies to the detected mood"
                if enabled
                else "Keep the configured tone regardless of mood"
            )
        },
        detail={"enabled": enabled},
    )


def _execute_adapt_tone(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Toggle mood-adaptive tone and report the switches."""
    enhancer = _enhancer(context, "response_adapt_tone")
    enhancer.enable_variations = _as_bool(params, "enabled", "response_adapt_tone")
    return {
        "variations": enhancer.enable_variations,
        "settings": _settings(enhancer),
    }


#: The response-enhancement tools.
RESPONSE_TOOLS: tuple = (
    ToolSpec(
        name="response_show_thinking",
        summary="Show or hide the model's thinking steps",
        resolve=_resolve_show_thinking,
        execute=_execute_show_thinking,
        returns="The new thinking-display state",
    ),
    ToolSpec(
        name="response_set_confidence",
        summary="Configure confidence scoring for replies",
        resolve=_resolve_set_confidence,
        execute=_execute_set_confidence,
        returns="The new confidence-scoring state",
    ),
    ToolSpec(
        name="response_suggest_proactive",
        summary="Show or hide proactive follow-up suggestions",
        resolve=_resolve_suggest_proactive,
        execute=_execute_suggest_proactive,
        returns="The new suggestion state",
    ),
    ToolSpec(
        name="response_adapt_tone",
        summary="Show or hide mood-adaptive tone variation",
        resolve=_resolve_adapt_tone,
        execute=_execute_adapt_tone,
        returns="The new tone-adaptation state",
    ),
)
