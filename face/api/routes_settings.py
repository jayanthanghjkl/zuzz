"""Face API — settings routes.

Read-only access to the effective configuration. Settings that could weaken
safety (trust level, immutable rules) are reported but never written through
the API.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from brain.brainstem.activity_logger import get_logger
from core.config import (
    get_danger_patterns,
    get_models,
    get_personality,
    get_settings,
)

router = APIRouter()

EDITABLE_KEYS = {"host", "port", "log_level", "free_only", "streaming", "max_history_messages"}


@router.get("/api/settings")
async def read_settings(request: Request) -> Dict[str, Any]:
    """Return the effective configuration.

    Args:
        request: Used to reach the shared agent components.

    Returns:
        Settings, model routing table, personality and trust level.
    """
    settings = get_settings()
    return {
        "settings": settings,
        "models": get_models(),
        "personality": get_personality(),
        "trust_level": settings.get("trust_level", 1),
        "editable_keys": sorted(EDITABLE_KEYS),
        "live": {
            "history_size": len(request.app.state.thalamus.cerebrum.history),
            "providers": request.app.state.thalamus.status()["providers"],
        },
    }


@router.post("/api/settings")
async def update_settings(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """Update non-safety settings.

    Args:
        payload: Flat mapping of setting name to value.
        request: Used to reach the shared agent components.

    Returns:
        The settings that were applied.

    Raises:
        HTTPException: 400 if an immutable or unknown key is present.
    """
    settings = get_settings()
    rejected = [key for key in payload if key not in EDITABLE_KEYS]
    if rejected:
        raise HTTPException(
            status_code=400,
            detail=f"These keys are not editable through the API: {', '.join(sorted(rejected))}",
        )
    settings.update(payload)
    get_logger().log("face", "settings_update", keys=sorted(payload))
    return {"applied": payload, "settings": settings}


@router.get("/api/safety/patterns")
async def safety_patterns() -> Dict[str, Any]:
    """Return the dangerous command/pattern list the Amygdala scans against."""
    return {"patterns": get_danger_patterns()}
