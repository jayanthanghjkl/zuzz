"""Cerebellum — ZUZZ personality, tone and mood.

Everything about how ZUZZ sounds rather than what it does. Four modules and one
orchestrator:

* :mod:`brain.cerebellum.personality_config` — reads and writes
  ``config/personality.json``, with a default for every field.
* :mod:`brain.cerebellum.character_prompts` — turns that into the paragraph the
  system prompt carries.
* :mod:`brain.cerebellum.tone_detector` — reads the user's register so the reply
  matches it.
* :mod:`brain.cerebellum.emotion_responder` — reads the user's mood and reshapes
  the reply's frame.

:mod:`brain.cerebrum.prompts` calls
:meth:`~brain.cerebellum.cerebellum.Cerebellum.apply_personality` on every system
prompt, which is what stops the voice living in two places.

**The owner's name never reaches the system prompt.** Local models turn a human
name into a matching capitalised folder, so a prompt naming "Jayanthan" produces
``C:\\Users\\Jayanthan`` and every path built from it fails. The literal Windows
account name is in the prompt; the person's name is for greetings, which are
generated per reply and never sent to a model.
``tests/test_cerebrum.py::test_system_prompt_names_the_real_windows_account``
enforces this, and :mod:`brain.cerebellum.character_prompts` is the only place in
the region that could break it.

**The Cerebellum cannot loosen safety.** It has no gate, no trust level and no
kill switch, and the paragraph it produces ends by saying so. The settings it will
accept are a closed set of keys and closed vocabularies, so an unrecognised value
cannot reach a model as an instruction to interpret — see
:mod:`brain.cerebellum.personality_config`.
"""

from brain.cerebellum.cerebellum import Cerebellum, get_cerebellum
from brain.cerebellum.character_prompts import (
    PERSONALITY_MARKER,
    apply_personality,
    build_personality_directive,
    get_greeting,
    get_signoff,
)
from brain.cerebellum.emotion_responder import (
    EXCITED,
    FRUSTRATED,
    MOODS,
    NEUTRAL,
    TIRED,
    adjust_response_tone,
    detect_user_mood,
    mood_hint,
)
from brain.cerebellum.personality_config import (
    ALLOWED_VALUES,
    EDITABLE_KEYS,
    HUMOR_LEVELS,
    PERSONALITY_FILE,
    Personality,
    PersonalityRefused,
    PersonalitySettings,
    STYLES,
    VERBOSITY,
    load,
    write_changes,
)
from brain.cerebellum.tone_detector import (
    CASUAL,
    FORMAL,
    detect_formality,
    formality_hint,
)

__all__ = [
    "ALLOWED_VALUES",
    "CASUAL",
    "Cerebellum",
    "EDITABLE_KEYS",
    "EXCITED",
    "FORMAL",
    "FRUSTRATED",
    "HUMOR_LEVELS",
    "MOODS",
    "NEUTRAL",
    "PERSONALITY_FILE",
    "PERSONALITY_MARKER",
    "Personality",
    "PersonalityRefused",
    "PersonalitySettings",
    "STYLES",
    "TIRED",
    "VERBOSITY",
    "adjust_response_tone",
    "apply_personality",
    "build_personality_directive",
    "detect_formality",
    "detect_user_mood",
    "formality_hint",
    "get_greeting",
    "get_signoff",
    "get_cerebellum",
    "load",
    "mood_hint",
    "write_changes",
]