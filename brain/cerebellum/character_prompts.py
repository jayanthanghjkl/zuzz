"""Cerebellum — character prompts.

Turns ``config/personality.json`` into the paragraph that tells a model how ZUZZ
is supposed to sound, and folds in what the user is like right now.

**The owner's name is deliberately absent from every string this module emits.**
Local models turn a human name into a matching capitalised folder, so a system
prompt containing "Jayanthan" reliably produces ``C:\\Users\\Jayanthan`` and every
path built from that fails — the model invents a folder rather than admitting it
is guessing. The literal Windows account is in the prompt instead; the person's
name is for greetings, which are generated per reply and never sent to a model.
``tests/test_cerebrum.py::test_system_prompt_names_the_real_windows_account``
enforces this at the prompt level, and this module is the only place that could
break it.

**The section is a request, not a rule.** It says the safety rules come first.
A personality paragraph that could raise the temperature of a refusal is a
personality paragraph that has been misconfigured, and the ordering is stated in
the text so that a model reading both knows which wins.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from brain.cerebellum.emotion_responder import mood_hint
from brain.cerebellum.personality_config import Personality, load
from brain.cerebellum.tone_detector import formality_hint

__all__ = [
    "PERSONALITY_MARKER",
    "apply_personality",
    "build_personality_directive",
    "get_greeting",
    "get_signoff",
]

#: Delimits the block this module owns, so applying it twice replaces the first
#: copy rather than stacking two personality paragraphs on top of each other.
PERSONALITY_MARKER = "## How ZUZZ speaks"

#: How each verbosity level is expressed in the prompt. The sentence budget is
#: separate and comes from ``max_sentences_per_reply``, because "concise" and
#: "at most eight sentences" are two different constraints and a model follows
#: whichever is stated last.
_VERBOSITY_LINES: Dict[str, str] = {
    "terse": "Answer in as few words as the question allows. One line is often right.",
    "concise": (
        "Be concise. Give the answer first and stop; add detail only when asked, "
        "not as a courtesy."
    ),
    "detailed": (
        "Be thorough. Explain the reasoning and the caveats, and lead with the "
        "answer so the detail never hides it."
    ),
}

#: How much humour is allowed. ``none`` is stated explicitly rather than omitted,
#: because an unstated limit reads as unspecified rather than as zero.
_HUMOR_LINES: Dict[str, str] = {
    "none": "No jokes, no quips, no wordplay.",
    "subtle": (
        "A dry aside is welcome when it is genuinely apt, but never at the "
        "expense of the answer and never when the user is struggling."
    ),
    "playful": (
        "Wit and humour are part of the voice. Still never at the expense of "
        "the answer or of a user who is struggling."
    ),
}

#: How each style is expressed. The configured style is the *base*; the
#: formality detector may move one step from it per reply.
_STYLE_LINES: Dict[str, str] = {
    "formal_friendly": (
        "Sound like a competent colleague: courteous, precise, and never "
        "padded. Warmth shows up as being useful, not as filler."
    ),
    "casual": "Sound relaxed and natural, like a knowledgeable friend helping out.",
    "terse": "Be brief and flat. No preamble, no framing, no summary of what you just said.",
    "playful": "Be light and engaging, without being flippant about the work.",
    "neutral": "Be plain and even. Neither stiff nor chatty.",
}


def _quirk_lines(personality: Personality) -> List[str]:
    """Return the configured quirks as prompt bullets.

    Each quirk is stated as a habit rather than as an order. "shows its thinking
    process when the reasoning is not obvious" reads as a description of a
    personality; "always show your thinking process" reads as an instruction
    that would have ZUZZ narrate obvious answers too.

    Args:
        personality: The configured voice.

    Returns:
        One bullet per quirk, skipping blanks.
    """
    return [f"- {quirk}" for quirk in personality.quirks if str(quirk).strip()]


def build_personality_directive(
    personality: Optional[Personality] = None,
    formality: Optional[str] = None,
    mood: Optional[str] = None,
) -> str:
    """Return the paragraph describing how ZUZZ should speak right now.

    Args:
        personality: The configured voice. Loaded from config when omitted.
        formality: The detected register, or None to use the configured style
            alone.
        mood: The detected mood, or None for no adjustment.

    Returns:
        The block, including its marker line. Never contains the owner's name —
        see the module docstring for why that matters more than it looks.
    """
    voice = personality or load()

    lines: List[str] = [
        PERSONALITY_MARKER,
        f"You are {voice.name or 'ZUZZ'}. "
        + _STYLE_LINES.get(voice.style, _STYLE_LINES["neutral"]),
        _VERBOSITY_LINES.get(voice.verbosity, _VERBOSITY_LINES["concise"])
        + f" Keep prose to about {voice.max_sentences} sentences unless the user "
        "asks for more. Fenced action blocks do not count as prose.",
        _HUMOR_LINES.get(voice.humor_level, _HUMOR_LINES["subtle"]),
    ]

    tone = ", ".join(voice.tone)
    if tone:
        lines.append(f"Register: {tone}.")

    quirks = _quirk_lines(voice)
    if quirks:
        lines.append("Small habits:")
        lines.extend(quirks)

    if formality:
        lines.append(formality_hint(formality))
    if mood:
        lines.append(mood_hint(mood))

    lines.append(
        "These shape how you speak, never whether you help. The safety rules "
        "above come first and this paragraph cannot loosen them."
    )
    return "\n".join(lines)


def apply_personality(
    system_prompt: str,
    personality: Optional[Personality] = None,
    formality: Optional[str] = None,
    mood: Optional[str] = None,
) -> str:
    """Enhance a system prompt with ZUZZ's configured voice.

    Idempotent: calling it twice replaces the block this module wrote rather than
    appending a second one. A caller that rebuilds a prompt per reply — which the
    chat route does — would otherwise accumulate a personality paragraph per turn
    until the prompt is mostly personality.

    Args:
        system_prompt: The prompt to enhance.
        personality: The configured voice. Loaded from config when omitted.
        formality: The detected register for this reply, or None.
        mood: The detected mood for this message, or None.

    Returns:
        The enhanced prompt. An empty prompt yields just the block, so the result
        is never empty.
    """
    directive = build_personality_directive(personality, formality=formality, mood=mood)
    base = str(system_prompt or "")
    if PERSONALITY_MARKER not in base:
        return f"{base}\n\n{directive}".strip()

    # Replace the existing block: everything from the marker to the next
    # double-newline, which is where the next top-level section begins.
    head, _, tail = base.partition(PERSONALITY_MARKER)
    _, separator, rest = tail.partition("\n\n")
    head = head.rstrip()
    return f"{head}\n\n{directive}{separator}{rest}".strip() if head else directive


def get_greeting(personality: Optional[Personality] = None) -> str:
    """Return a greeting appropriate to the configured voice.

    Args:
        personality: The configured voice. Loaded from config when omitted.

    Returns:
        The greeting with the user's name substituted, or an empty string when
        none is configured. Empty is a supported answer — a greeting that greets
        nobody reads worse than none, and the chat route only uses this on the
        first message of a session.
    """
    return (personality or load()).addressed_greeting()


def get_signoff(personality: Optional[Personality] = None) -> str:
    """Return the configured sign-off.

    Args:
        personality: The configured voice. Loaded from config when omitted.

    Returns:
        The sign-off, or an empty string when the user has not configured one.
        Never appended by default: a closing line on every reply is noise, so the
        caller has to ask for it.
    """
    return (personality or load()).signed_off()