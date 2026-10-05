"""Cerebellum — the personality orchestrator.

The one object the rest of ZUZZ asks about how ZUZZ sounds. Five capabilities,
each delegating to the module that owns it:

* :meth:`~brain.cerebellum.cerebellum.Cerebellum.apply_personality` — fold the
  configured voice into a system prompt.
* :meth:`~brain.cerebellum.cerebellum.Cerebellum.detect_user_mood` — read the
  user's mood from a message.
* :meth:`~brain.cerebellum.cerebellum.Cerebellum.adjust_response_tone` — reshape
  a reply to that mood.
* :meth:`~brain.cerebellum.cerebellum.Cerebellum.get_greeting` and
  :meth:`~brain.cerebellum.cerebellum.Cerebellum.get_signoff` — the two lines of
  text ZUZZ actually says.
* :meth:`~brain.cerebellum.cerebellum.Cerebellum.update_setting` — let the user
  tune the voice at runtime.

**Nothing here decides permission.** Adjusting how ZUZZ speaks is not an act on
the machine. The one tool that writes, ``personality_update_setting``, is routed
through the gate as a ``write_files`` action because it does change a file on
disk; the two read-only tools are ``chat``, which is ``auto`` everywhere for the
same reason inference is — asking a question is not a side effect.

**Nothing here can loosen safety.** :func:`~brain.cerebellum.character_prompts.apply_personality`
appends a paragraph that says the safety rules come first. The Cerebellum has no
gate, no kill switch and no trust level, and the paragraph it produces is bounded
by the closed vocabularies in
:mod:`~brain.cerebellum.personality_config` so an unrecognised value cannot reach
a model as an instruction to interpret.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from brain.cerebellum.character_prompts import (
    apply_personality as _apply_personality,
)
from brain.cerebellum.character_prompts import build_personality_directive
from brain.cerebellum.character_prompts import get_greeting as _get_greeting
from brain.cerebellum.character_prompts import get_signoff as _get_signoff
from brain.cerebellum.emotion_responder import (
    MOODS,
    adjust_response_tone as _adjust_response_tone,
    describe_moods,
    detect_user_mood as _detect_user_mood,
    mood_hint,
)
from brain.cerebellum.personality_config import (
    ALLOWED_VALUES,
    EDITABLE_KEYS,
    PERSONALITY_FILE,
    Personality,
    PersonalityRefused,
    PersonalitySettings,
)
from brain.cerebellum.tone_detector import detect_formality, formality_hint

__all__ = [
    "Cerebellum",
    "PersonalityRefused",
    "build_personality_directive",
    "get_cerebellum",
]


class Cerebellum:
    """ZUZZ's personality.

    Attributes:
        settings: The configuration surface, so an update goes through the same
            validation the API uses rather than a second path.
        path: The configuration file, reported for the API.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        """Bind to a personality configuration.

        Args:
            path: The file to read and write. Defaults to
                ``config/personality.json``.
        """
        self.settings = PersonalitySettings(path or PERSONALITY_FILE)
        self.path = self.settings.path

    # -----------------------------------------------------------------
    # The five capabilities
    # -----------------------------------------------------------------

    def apply_personality(
        self,
        system_prompt: str,
        message: str = "",
        formality: Optional[str] = None,
        mood: Optional[str] = None,
    ) -> str:
        """Fold ZUZZ's voice into a system prompt.

        Reads the register and the mood from ``message`` when they are not passed
        in, so a caller composing a reply gets one call rather than three.

        Args:
            system_prompt: The prompt to enhance.
            message: The user's message, used to read register and mood. Empty
                means "apply the configured voice and nothing else", which is what
                the non-chat prompts want.
            formality: An explicit register, overriding detection.
            mood: An explicit mood, overriding detection.

        Returns:
            The enhanced prompt. Idempotent, so a per-reply rebuild does not
            accumulate paragraphs.
        """
        voice = self.settings.current()
        if message:
            detected_formality = formality or detect_formality(message, voice.style)
            detected_mood = mood or _detect_user_mood(message)
        else:
            detected_formality = formality
            detected_mood = mood
        return _apply_personality(
            system_prompt,
            voice,
            formality=detected_formality,
            mood=detected_mood,
        )

    def detect_user_mood(self, message: str) -> str:
        """Return the mood a message reads as.

        Args:
            message: What the user said.

        Returns:
            One of ``neutral``, ``frustrated``, ``excited`` or ``tired``.
        """
        return _detect_user_mood(message)

    def adjust_response_tone(self, response: str, mood: str) -> str:
        """Reshape a reply to the user's mood.

        Args:
            response: What ZUZZ said.
            mood: One of the four moods.

        Returns:
            The adjusted reply. The original when there is nothing to do.
        """
        return _adjust_response_tone(response, mood)

    def get_greeting(self) -> str:
        """Return the configured greeting, with the user's name filled in.

        Returns:
            The greeting, or an empty string when none is configured.
        """
        return _get_greeting(self.settings.current())

    def get_signoff(self) -> str:
        """Return the configured sign-off.

        Returns:
            The sign-off, or an empty string when none is configured.
        """
        return _get_signoff(self.settings.current())

    # -----------------------------------------------------------------
    # Settings
    # -----------------------------------------------------------------

    def get_settings(self) -> Personality:
        """Return the configured personality."""
        return self.settings.current()

    def update_setting(self, changes: Dict[str, Any]) -> Personality:
        """Change one or more personality settings.

        Args:
            changes: The settings to change, keyed by name.

        Returns:
            The personality as it now stands.

        Raises:
            PersonalityRefused: When a key is not editable or a value is outside
                its closed vocabulary. Nothing is written in that case — every
                key is validated before any of them is applied.
        """
        return self.settings.update(changes)

    def describe(self) -> Dict[str, Any]:
        """Return the configuration plus what may be changed and to what."""
        payload = self.settings.describe()
        payload["moods"] = describe_moods()
        payload["base_style"] = self.settings.current().style
        return payload

    def respond(self, message: str, reply: str) -> Dict[str, Any]:
        """Adjust a reply to a message and report what was decided.

        The single call a chat route wants: one message in, one adjusted reply out
        plus the mood and register behind it, so the route can log them without
        running detection a second time.

        Args:
            message: What the user said.
            reply: What ZUZZ said.

        Returns:
            ``reply``, ``mood``, ``formality``, and whether anything changed.
        """
        voice = self.settings.current()
        mood = self.detect_user_mood(message)
        formality = detect_formality(message, voice.style)
        adjusted = self.adjust_response_tone(reply, mood)
        return {
            "reply": adjusted,
            "mood": mood,
            "formality": formality,
            "changed": adjusted != reply,
            "instruction": mood_hint(mood),
            "formality_instruction": formality_hint(formality),
        }

    # -----------------------------------------------------------------
    # Reporting
    # -----------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Return a short description of the voice, for the dashboard."""
        voice = self.settings.current()
        return {
            "name": voice.name,
            "style": voice.style,
            "humor_level": voice.humor_level,
            "verbosity": voice.verbosity,
            "tone": list(voice.tone),
            "max_sentences": voice.max_sentences,
            "quirks": len(voice.quirks),
            "has_greeting": bool(voice.addressed_greeting()),
            "has_signoff": bool(voice.signed_off()),
            "path": str(self.path),
            "editable_keys": list(EDITABLE_KEYS),
            "allowed_values": {key: list(values) for key, values in ALLOWED_VALUES.items()},
            "moods": list(MOODS),
            "refused": "PersonalityRefused",
        }


#: The process-wide personality, built on first use.
#:
#: Lazy rather than built at import because :mod:`brain.cerebrum.prompts` calls
#: into it on every system prompt, and that module is imported by everything from
#: the Thalamus down. One instance rather than one per caller, so the voice is
#: read from one place — it holds no mutable state beyond the settings path, so
#: two instances would be two answers to the same question.
_CEREBELLUM: Optional[Cerebellum] = None


def get_cerebellum() -> Cerebellum:
    """Return the process-wide personality, building it on first use.

    Returns:
        The shared instance.
    """
    global _CEREBELLUM
    if _CEREBELLUM is None:
        _CEREBELLUM = Cerebellum()
    return _CEREBELLUM