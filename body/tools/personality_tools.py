"""Body/Tools â€” personality.

Reading and changing how ZUZZ sounds.

Three tools, and they are judged under three different categories on purpose.

``personality_get_settings`` and ``personality_detect_mood``
    ``chat``, which is ``auto`` at every trust level. Both are pure reads with no
    effect on the machine and no file written, and that is exactly why
    :meth:`brain.cerebrum.cerebrum.Cerebrum._check_may_infer` treats ordinary chat
    as auto too â€” asking a question is not a side effect.

``personality_update_setting``
    ``write_files``, which asks at level 1 and auto-approves from 2. It changes a
    file on disk, so it is judged as a file write rather than as self-modification
    with its own softer row. The practical effect is that ZUZZ cannot revoice
    itself on a machine whose user has chosen to be asked about everything, which is
    the behaviour someone running at level 1 wants.

**The editable keys are a closed set of six.** ``style``, ``humor_level``,
``verbosity``, ``greeting``, ``signoff`` and ``quirks``. Not ``name``,
``user_name``, ``owner``, ``wake_word`` or ``max_sentences_per_reply`` â€” those are
structural, and a model able to rewrite its own identity or its own output budget
makes every prompt assertion downstream a lie. The set is enforced in
:mod:`brain.cerebellum.personality_config`, not here, so the API and this tool
cannot disagree about what may change.

**``quirks`` arrives as a comma-separated string** because the schema declares it
as one and the coercion layer only accepts real lists for array-typed parameters.
It is split here rather than in the Cerebellum, which means the Cerebellum's own
API â€” where a real JSON array arrives â€” is unaffected by a model's typing habits.
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
from brain.cerebellum.personality_config import PersonalityRefused

#: The category every personality tool resolves to, reads and writes alike.
#:
#: One category rather than the previous ``chat``/``write_files`` pair, and the
#: change is a correction rather than a preference. Splitting them borrowed
#: ``write_files``'s row for a setting that is ``{"tone": "warm"}`` in a config
#: file ZUZZ wrote itself â€” which made the most innocuous capability in this
#: package ask for permission at trust level 1 while claiming, in the row it
#: borrowed, to be about writing the user's files. The editable-key allowlist
#: is what bounds the write half; the row is only the prompt.
#:
#: ``personality_access`` is auto at every level. See ``config/trust_levels.json``.
CATEGORY = "personality_access"

#: Longest a single quirk may be, mirrored from the Cerebellum so a model is told
#: the limit rather than discovering it as a refusal.
MAX_QUIRK_CHARS = 300


def _cerebellum(context: ToolContext, tool: str):
    """Return the Cerebellum, refusing when the region is not attached.

    Args:
        context: The collaborators the tool may act through.
        tool: The tool name, for the message.

    Returns:
        The Cerebellum.

    Raises:
        ToolRefused: When ``context.cerebellum`` is None. Falling back to defaults
            here would let a model "change" a voice it never actually reached,
            and the user would see the setting as saved.
    """
    cerebellum = context.cerebellum
    if cerebellum is None:
        raise ToolRefused(f"{tool}: personality is not attached, so there is nothing to read")
    return cerebellum


def _value_for(key: str, value: str) -> Any:
    """Return the value to store for a personality key.

    Args:
        key: The setting name.
        value: The value as the model wrote it.

    Returns:
        The value in the shape the config expects: a list for ``quirks``, the
        plain string otherwise.

    Raises:
        ToolRefused: When a quirk is longer than the limit.
    """
    if key == "quirks":
        parts = [part.strip() for part in str(value).split(",")]
        for part in parts:
            if len(part) > MAX_QUIRK_CHARS:
                raise ToolRefused(
                    f"a quirk may be at most {MAX_QUIRK_CHARS} characters, "
                    f"not {len(part)}"
                )
        return [part for part in parts if part]
    return str(value).strip()


# -- personality_get_settings ------------------------------------------------


def _resolve_settings(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a settings read."""
    _cerebellum(context, "personality_get_settings")
    return ToolTarget(
        category=CATEGORY,
        target="the personality configuration",
        preview={"description": "Read how ZUZZ is configured to speak"},
        detail={},
    )


def _execute_settings(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Return the personality configuration."""
    return _cerebellum(context, "personality_get_settings").describe()


# -- personality_update_setting ----------------------------------------------


def _resolve_update(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a personality change into the key the gate judges."""
    key = required_text(params, "key", "personality_update_setting")
    value = required_text(params, "value", "personality_update_setting")
    cerebellum = _cerebellum(context, "personality_update_setting")
    settings = cerebellum.describe()
    if key not in settings.get("editable_keys", []):
        raise ToolRefused(
            f"personality_update_setting: {key!r} is not changeable at runtime. "
            f"Editable: {', '.join(settings.get('editable_keys', []))}"
        )
    allowed = settings.get("allowed_values", {}).get(key)
    if allowed and value.strip().lower() not in allowed:
        raise ToolRefused(
            f"personality_update_setting: {key} must be one of "
            f"{', '.join(allowed)} â€” not {value!r}"
        )
    return ToolTarget(
        category=CATEGORY,
        target=f"{key}={value}"[:120],
        preview={
            "description": f"Change ZUZZ's {key} to {value!r}",
            "file": str(cerebellum.path),
        },
        detail={"key": key, "allowed": list(allowed) if allowed else []},
    )


def _execute_update(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Apply the change and report the voice as it now stands."""
    key = str(params["key"])
    try:
        personality = _cerebellum(context, "personality_update_setting").update_setting(
            {key: _value_for(key, str(params["value"]))}
        )
    except PersonalityRefused as exc:
        raise ToolRefused(f"personality_update_setting: {exc}") from exc
    return {"changed": key, "personality": personality.to_dict()}


# -- personality_detect_mood -------------------------------------------------


def _resolve_mood(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a mood read. The target is the text being read."""
    message = required_text(params, "message", "personality_detect_mood")
    _cerebellum(context, "personality_detect_mood")
    return ToolTarget(
        category=CATEGORY,
        target=message[:120],
        preview={"description": "Read the mood of a message"},
        detail={},
    )


def _execute_mood(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Return the mood and how a reply should be shaped by it."""
    from brain.cerebellum.emotion_responder import MOODS, mood_hint
    from brain.cerebellum.tone_detector import detect_formality, formality_hint

    cerebellum = _cerebellum(context, "personality_detect_mood")
    message = str(params["message"])
    mood = cerebellum.detect_user_mood(message)
    formality = detect_formality(message, cerebellum.get_settings().style)
    return {
        "mood": mood,
        "formality": formality,
        "instruction": mood_hint(mood),
        "formality_instruction": formality_hint(formality),
        "moods_available": list(MOODS),
    }


#: The personality tools.
PERSONALITY_TOOLS: tuple = (
    ToolSpec(
        name="personality_get_settings",
        summary="Read how ZUZZ is configured to speak",
        resolve=_resolve_settings,
        execute=_execute_settings,
        returns="Style, humour, verbosity, greeting, signoff and quirks",
    ),
    ToolSpec(
        name="personality_update_setting",
        summary="Change one personality setting",
        resolve=_resolve_update,
        execute=_execute_update,
        returns="The personality as it now stands",
    ),
    ToolSpec(
        name="personality_detect_mood",
        summary="Read the mood of a message",
        resolve=_resolve_mood,
        execute=_execute_mood,
        returns="The mood and how a reply should be shaped",
    ),
)