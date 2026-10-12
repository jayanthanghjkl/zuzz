"""Cerebellum — ZUZZ personality, tone, mood, and response enhancement.

Everything about how ZUZZ sounds rather than what it does. Core modules:

* :mod:`brain.cerebellum.personality_config` — reads and writes
  ``config/personality.json``, with a default for every field.
* :mod:`brain.cerebellum.character_prompts` — turns that into the paragraph the
  system prompt carries.
* :mod:`brain.cerebellum.tone_detector` — reads the user's register so the reply
  matches it.
* :mod:`brain.cerebellum.emotion_responder` — reads the user's mood and reshapes
  the reply's frame.

Jarvis-style enhancement modules (Phase 4.5):

* :mod:`brain.cerebellum.thinking_display` — extracts and labels reasoning steps.
* :mod:`brain.cerebellum.confidence_scorer` — scores reply confidence 0.0–1.0.
* :mod:`brain.cerebellum.context_continuer` — injects relevant memory context.
* :mod:`brain.cerebellum.proactive_suggester` — generates follow-up suggestions.
* :mod:`brain.cerebellum.self_critic` — flags issues before the user sees them.
* :mod:`brain.cerebellum.personality_variations` — mood-adaptive response modes.
* :mod:`brain.cerebellum.response_enhancer` — orchestrator tying all above together.

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
from brain.cerebellum.confidence_scorer import (
    ConfidenceResult,
    ConfidenceScorer,
    score_reply,
)
from brain.cerebellum.context_continuer import (
    ContextContinuer,
    ContinuationContext,
    get_continuation_context,
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
from brain.cerebellum.personality_variations import (
    PersonalityVariations,
    VariationSet,
    apply_variation,
    select_variation,
)
from brain.cerebellum.proactive_suggester import (
    ProactiveSuggester,
    Suggestion,
    generate_suggestions,
)
from brain.cerebellum.response_enhancer import (
    EnhancedResponse,
    ResponseEnhancer,
    enhance_response,
)
from brain.cerebellum.self_critic import (
    CritiqueResult,
    CritiqueFlag,
    SelfCritic,
    critique_reply,
)
from brain.cerebellum.thinking_display import (
    ThinkingDisplay,
    ThinkingStep,
    THINKING_PATTERNS,
    extract_thinking_steps,
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
    "ConfidenceResult",
    "ConfidenceScorer",
    "ContextContinuer",
    "ContinuationContext",
    "CritiqueFlag",
    "CritiqueResult",
    "EDITABLE_KEYS",
    "EnhancedResponse",
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
    "PersonalityVariations",
    "ProactiveSuggester",
    "ResponseEnhancer",
    "SelfCritic",
    "STYLES",
    "Suggestion",
    "THINKING_PATTERNS",
    "TIRED",
    "ThinkingDisplay",
    "ThinkingStep",
    "VariationSet",
    "VERBOSITY",
    "adjust_response_tone",
    "apply_personality",
    "apply_variation",
    "build_personality_directive",
    "critique_reply",
    "detect_formality",
    "detect_user_mood",
    "enhance_response",
    "extract_thinking_steps",
    "formality_hint",
    "generate_suggestions",
    "get_continuation_context",
    "get_cerebellum",
    "get_greeting",
    "get_signoff",
    "load",
    "mood_hint",
    "score_reply",
    "select_variation",
    "write_changes",
]