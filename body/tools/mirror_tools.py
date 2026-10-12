"""Body/Tools — Mirror Neurons tools.

These tools expose the Mirror Neurons region's public API: start/stop
observation, get detected patterns, create macros from patterns, and
suggest automations.
"""

from __future__ import annotations

from typing import Any, Dict, List

from body.tools.tool_spec import ToolRefused, ToolSpec, ToolTarget

__all__ = ["MIRROR_TOOLS"]


def _require_mirror(ctx: Any, tool_name: str) -> Any:
    """Return the MirrorNeurons instance or refuse."""
    mirror = getattr(ctx, "mirror_neurons", None)
    if mirror is None:
        raise ToolRefused(f"{tool_name}: Mirror Neurons region is not attached")
    return mirror


async def _observation_start(params: Dict[str, Any], ctx: Any) -> ToolTarget:
    mirror = _require_mirror(ctx, "observation_start")
    return ToolTarget(
        category="observe_user",
        target="observation_session",
        preview={"action": "Start observing user actions"},
        detail={},
    )


async def _observation_start_execute(decision: Any, params: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    mirror = _require_mirror(ctx, "observation_start")
    session_id = mirror.start_observation()
    return {"session_id": session_id, "status": "observing"}


async def _observation_stop(params: Dict[str, Any], ctx: Any) -> ToolTarget:
    mirror = _require_mirror(ctx, "observation_stop")
    return ToolTarget(
        category="observe_user",
        target="observation_session",
        preview={"action": "Stop observing and detect patterns"},
        detail={},
    )


async def _observation_stop_execute(decision: Any, params: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    mirror = _require_mirror(ctx, "observation_stop")
    session = mirror.stop_observation()
    return session.to_dict()


async def _observation_patterns(params: Dict[str, Any], ctx: Any) -> ToolTarget:
    mirror = _require_mirror(ctx, "observation_patterns")
    return ToolTarget(
        category="observe_user",
        target="patterns",
        preview={"action": "Get detected patterns"},
        detail={},
    )


async def _observation_patterns_execute(decision: Any, params: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    mirror = _require_mirror(ctx, "observation_patterns")
    patterns = mirror.get_detected_patterns()
    return {"patterns": [p.to_dict() for p in patterns]}


async def _observation_create_macro(params: Dict[str, Any], ctx: Any) -> ToolTarget:
    mirror = _require_mirror(ctx, "observation_create_macro")
    pattern_id = params.get("pattern_id", "")
    name = params.get("name", "")
    return ToolTarget(
        category="create_macro",
        target=f"macro:{pattern_id}",
        preview={"action": "Create macro from pattern", "pattern_id": pattern_id, "name": name},
        detail={"pattern_id": pattern_id, "name": name},
    )


async def _observation_create_macro_execute(decision: Any, params: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    mirror = _require_mirror(ctx, "observation_create_macro")
    pattern_id = params["pattern_id"]
    name = params["name"]
    macro = mirror.create_macro_from_pattern(pattern_id)
    return macro.to_dict()


async def _observation_suggest(params: Dict[str, Any], ctx: Any) -> ToolTarget:
    mirror = _require_mirror(ctx, "observation_suggest")
    threshold = params.get("threshold", 5)
    return ToolTarget(
        category="observe_user",
        target="suggestions",
        preview={"action": "Suggest automations", "threshold": threshold},
        detail={"threshold": threshold},
    )


async def _observation_suggest_execute(decision: Any, params: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    mirror = _require_mirror(ctx, "observation_suggest")
    threshold = params.get("threshold", 5)
    macro = mirror.suggest_automation()
    if macro is None:
        return {"suggestions": []}
    return {"suggestions": [macro.to_dict()]}


MIRROR_TOOLS: List[ToolSpec] = [
    ToolSpec(
        name="observation_start",
        summary="Begin observing user actions to detect repeatable patterns",
        resolve=_observation_start,
        execute=_observation_start_execute,
        returns="session_id and status",
    ),
    ToolSpec(
        name="observation_stop",
        summary="Stop observing and detect patterns",
        resolve=_observation_stop,
        execute=_observation_stop_execute,
        returns="session summary with patterns",
    ),
    ToolSpec(
        name="observation_patterns",
        summary="Get patterns detected from the last observation session",
        resolve=_observation_patterns,
        execute=_observation_patterns_execute,
        returns="list of patterns with repeat counts",
    ),
    ToolSpec(
        name="observation_create_macro",
        summary="Create a disabled macro from a detected pattern",
        resolve=_observation_create_macro,
        execute=_observation_create_macro_execute,
        returns="macro definition",
    ),
    ToolSpec(
        name="observation_suggest",
        summary="Suggest automations for patterns meeting the repetition threshold",
        resolve=_observation_suggest,
        execute=_observation_suggest_execute,
        returns="list of suggested macros",
    ),
]