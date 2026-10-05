"""Face API — personality routes.

How ZUZZ is configured to speak, and the one mood endpoint the UI uses.

**``POST /api/personality/settings`` writes a file**, so unlike the memory and
habit routes these are the only Phase 6 endpoints that change anything on disk.
Six keys are writable — ``style``, ``humor_level``, ``verbosity``, ``greeting``,
``signoff`` and ``quirks`` — and the set is a closed one rather than a denylist, so
a key added to the file next year is unwritable until somebody says otherwise.
Every key is validated before any is written, so a request naming one good setting
and one bad one changes nothing.

**The route does not gate, and that is worth stating plainly.** Changing ZUZZ's
voice is a file write, so the *tool* that does it
(``personality_update_setting``) goes through the gate as ``personality_access``.
This HTTP route is the user acting on their own machine through their own
dashboard — the same reason ``PUT`` on a settings file is not behind a permission
prompt. If it were gated, the Settings page could not save anything at level 1, and
the user's own explicit request would need approving by the machine.

The allowlist is what keeps that safe at ``auto``. It is a closed set, so the one
key that must never move from here — ``owner_name`` — is unreachable by
construction rather than by a rule that could be edited.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from brain.cerebellum.personality_config import EDITABLE_KEYS, PersonalityRefused

router = APIRouter()


class SettingsRequest(BaseModel):
    """A request to change how ZUZZ speaks."""

    settings: Dict[str, Any] = Field(
        ..., description="The settings to change; only the six editable keys are accepted"
    )


class MoodRequest(BaseModel):
    """A request to read the mood of a message."""

    message: str = Field(..., description="The user's message")


def _cerebellum(request: Request):
    """Return the Cerebellum wired into the app."""
    cerebellum = getattr(request.app.state, "cerebellum", None)
    if cerebellum is None:
        raise HTTPException(status_code=503, detail="personality is not attached to this app")
    return cerebellum


@router.get("/api/personality/settings")
async def read_settings(request: Request) -> Dict[str, Any]:
    """Return how ZUZZ is configured to speak.

    Args:
        request: Used to reach the Cerebellum.

    Returns:
        The configuration, which keys may be changed, which values each accepts, and
        the greeting and sign-off with the user's name filled in. Also
        ``invalid_fields`` and ``warnings``: any value in the file that is not in
        its key's vocabulary, alongside the sentence explaining which default
        replaced it. A setting the operator wrote and the page cannot honour is
        reported here rather than quietly corrected, because a read that differs
        from the file with no explanation is indistinguishable from a bug.
    """
    cerebellum = _cerebellum(request)
    voice = cerebellum.get_settings()
    return {
        **cerebellum.describe(),
        "greeting": voice.addressed_greeting(),
        "signoff": voice.signed_off(),
        "status": cerebellum.status(),
    }


@router.post("/api/personality/settings")
async def update_settings(request: Request, body: SettingsRequest) -> Dict[str, Any]:
    """Change how ZUZZ speaks.

    Args:
        request: Used to reach the Cerebellum and the activity log.
        body: The settings to change.

    Returns:
        The personality as it now stands, with the keys that were applied.

    Raises:
        HTTPException: 400 when a key is not editable or a value is outside its
            closed vocabulary. Nothing is written in that case.
    """
    cerebellum = _cerebellum(request)
    applied: List[str] = sorted(body.settings)
    rejected = sorted(set(body.settings) - set(EDITABLE_KEYS))
    if rejected:
        raise HTTPException(
            status_code=400,
            detail=(
                f"These personality settings are not changeable here: "
                f"{', '.join(rejected)}. Changeable: {', '.join(EDITABLE_KEYS)}"
            ),
        )
    try:
        voice = cerebellum.update_setting(body.settings)
    except PersonalityRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    request.app.state.logger.log(
        "cerebellum", "settings_update", keys=applied, values=list(body.settings.values())
    )
    return {
        "applied": applied,
        "settings": voice.to_dict(),
        "greeting": voice.addressed_greeting(),
        "signoff": voice.signed_off(),
        "note": "the change applies to the next reply; no restart is needed",
    }


@router.post("/api/personality/detect-mood")
async def detect_mood(request: Request, body: MoodRequest) -> Dict[str, Any]:
    """Read the mood of a message and how a reply should be shaped by it.

    Args:
        request: Used to reach the Cerebellum.
        body: The message to read.

    Returns:
        The mood, the register, and the instruction for each. Also reports what the
        reply would look like both ways, so the page can show the difference rather
        than describe it.
    """
    cerebellum = _cerebellum(request)
    message = body.message
    reply = "Here is what I found."
    shaped = cerebellum.respond(message, reply)
    return {
        **shaped,
        "moods": [entry["mood"] for entry in cerebellum.describe()["moods"]],
        "input_length": len(message),
    }