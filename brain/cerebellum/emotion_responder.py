"""Cerebellum — emotion response.

Reads the user's mood from the message and adjusts the reply to it.

Two decisions here are the ones worth arguing about.

**The reply is prefixed, not rewritten.** :func:`adjust_response_tone` returns the
original response with a mood-appropriate lead-in or a trimmed version — it does
not paraphrase or reorder what ZUZZ said. Rewriting the answer would mean the
emotion layer can change a *conclusion*, and the layer that notices someone is
tired is the wrong layer to be deciding what the answer is.

**Mood is not sentiment.** ``frustrated`` here means "be brief, skip the
apology", not "apologise more warmly". ZUZZ is an assistant on the user's own
machine, and the most useful response to someone hitting an error for the fourth
time is not sympathy — it is the fix, immediately, without a preamble about how
frustrating that must be.

Detection is lexical and deliberately conservative: four moods, a weighted cue
table, and a threshold. A classifier would catch more and would also cheerfully
report ``excited`` for "great, another crash", which is the failure that would
make this feature worse than useless.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

__all__ = [
    "EXCITED",
    "FRUSTRATED",
    "MOODS",
    "NEUTRAL",
    "TIRED",
    "adjust_response_tone",
    "detect_user_mood",
    "lead_in",
    "mood_hint",
]

#: The four moods ZUZZ answers differently. ``neutral`` is the default and is not
#: a fallback — a message with no mood signal in it genuinely is neutral, and
#: treating that as "unknown" would have every ordinary question flagged.
NEUTRAL = "neutral"
FRUSTRATED = "frustrated"
EXCITED = "excited"
TIRED = "tired"

MOODS: Tuple[str, ...] = (NEUTRAL, FRUSTRATED, EXCITED, TIRED)

#: Cue words and their weights. Multi-word phrases carry more weight than single
#: words because "not working" is a stronger frustration signal than "broken".
_CUES: Dict[str, Tuple[Tuple[str, float], ...]] = {
    FRUSTRATED: (
        (r"\bfuck(?:ing|ed)?\b", 3.0),
        (r"\bdamn\b", 2.0),
        (r"\bstupid\b", 2.0),
        (r"\bnot working\b", 2.5),
        (r"\bdoes(?:n't| not) work\b", 2.5),
        (r"\bagain\b", 1.0),
        (r"\bstill\b", 1.0),
        (r"\bwhy (?:is|are|does|isn't|aren't)\b", 1.5),
        (r"\bseriously\b", 1.5),
        (r"\bwhat(?:'s| is) wrong\b", 1.5),
        (r"\bfrustrat\w+", 2.5),
        (r"\bannoy\w+", 2.0),
        (r"\birritat\w+", 2.0),
        (r"\bfed up\b", 2.5),
        (r"\bgive up\b", 2.0),
        (r"\bwrong\b", 1.0),
        (r"\bbroke\b", 1.5),
        # The inflected forms matter more than they look: "broken" is how most
        # people actually say it, and a cue list holding only "broke" scores a
        # shouted "THIS IS BROKEN AGAIN" below the threshold and reads it as
        # neutral.
        (r"\bbroken\b", 1.5),
        (r"\bbreaks?\b", 1.0),
        (r"\bwont\b|\bwon't\b|\bwouldn't\b", 1.0),
        (r"\bstuck\b", 1.5),
        (r"\bsame (?:thing|error|problem)\b", 2.0),
        (r"\bstill (?:not|happening|failing|broken)\b", 2.0),
        (r"\berror\b", 0.75),
        (r"\bfail(?:ed|s|ing)?\b", 1.0),
        (r"\bcrash(?:ed|es|ing)?\b", 1.0),
        (r"\bbug\b", 0.5),
        # Impatience, which is frustration said quietly. The bare form scores half
        # the threshold on purpose: "this is taking too long" is an ordinary
        # sentence and should stay neutral, while "far too long" clears it. Two
        # entries rather than one weighted cue is what keeps that distinction.
        (r"\btoo (?:long|slow)\b", 1.0),
        (r"\bfar too (?:long|slow)\b", 2.0),
        (r"\bhate\b", 2.0),
        (r"\bnothing works\b", 2.5),
        (r"\bkeeps? (?:failing|crashing|breaking)\b", 2.0),
        (r"\bdrives? me (?:crazy|nuts|insane)\b", 2.5),
    ),
    EXCITED: (
        (r"\byay\b", 2.0),
        (r"\byes\b", 1.0),
        (r"\bawesome\b", 2.5),
        (r"\bamazing\b", 2.5),
        (r"\bperfect\b", 2.0),
        (r"\bbrilliant\b", 2.5),
        (r"\bexcellent\b", 2.0),
        (r"\bworked\b", 1.0),
        (r"\bit works\b", 2.0),
        (r"\bfinally\b", 1.5),
        (r"\bthank(?:s| you)\b", 1.5),
        (r"\bwell done\b", 2.0),
        (r"\bnailed it\b", 2.5),
        (r"\bso happy\b", 2.5),
        (r"\bexcited\b", 2.0),
        (r"\blets? go\b", 1.5),
        (r"\blove\b", 1.0),
    ),
    TIRED: (
        (r"\btired\b", 2.5),
        (r"\bexhaust\w+", 2.5),
        (r"\bsleepy\b", 2.0),
        (r"\bweary\b", 2.0),
        (r"\blong day\b", 2.5),
        (r"\bbeen up\b", 2.0),
        (r"\bcan'?t (?:keep|go on)\b", 2.0),
        (r"\bneed a break\b", 2.5),
        (r"\bshut (?:up|down)\b", 2.0),
        (r"\blate\b", 0.75),
        (r"\bquick(?:ly)?\b", 0.75),
        (r"\bjust (?:tired|exhausted)\b", 3.0),
        (r"\bdone for the day\b", 3.0),
        (r"\bbrain (?:is )?(?:dead|fried)\b", 2.0),
    ),
}

#: How much evidence a mood needs before it is reported. Above the line it is a
#: mood; below it is an ordinary sentence that happens to contain the word.
_THRESHOLD = 2.0

#: Punctuation that raises a reading, applied per mark and capped.
_EXCLAIM_WEIGHT = 0.6
_MAX_PUNCTUATION_WEIGHT = 1.8

_SHOUT_RE = re.compile(r"\b[A-Z]{3,}\b")

#: What each mood leads with. Empty means the reply is left alone.
_LEAD_INS: Dict[str, str] = {
    FRUSTRATED: "",
    EXCITED: "",
    TIRED: "",
    NEUTRAL: "",
}

#: How each mood changes the reply's shape. Keys are instruction strings for the
#: prompt; the two that are acted on mechanically are applied by
#: :func:`adjust_response_tone`.
_MOOD_HINTS: Dict[str, str] = {
    FRUSTRATED: (
        "The user is frustrated. Answer immediately and concretely. No apology, "
        "no reassurance preamble, no restating the problem — the answer is the "
        "whole reply. Keep it short."
    ),
    EXCITED: (
        "The user is pleased about something. Match the energy briefly, then "
        "move on to what they asked for. Do not celebrate for a second sentence."
    ),
    TIRED: (
        "The user is tired. Be brief and low-friction. One clear answer, no "
        "options to weigh up, no suggestions for later."
    ),
    NEUTRAL: (
        "The user is in an ordinary frame of mind. Answer as configured, "
        "without reading anything into the tone."
    ),
}

#: Sentences dropped from the front of a reply when the user is frustrated or
#: tired. Deliberately a short fixed list: these are the filler openers that
#: precede no information, and matching on them is safer than a heuristic.
#: Explanations in parentheses are kept — they carry content — so only the
#: framing is removed.
#:
#: The apology family is matched as one alternation with an optional "to hear
#: that" tail, because "I'm sorry to hear that." is the single most common way a
#: model opens after a user reports a failure — and matching only the bare "sorry"
#: left the most common one of all sitting there in full.
_PREAMBLE_RE = re.compile(
    r"^(?:"
    r"(?:sure|certainly|of course|absolutely|no problem|got it|understood)\b[.!]?\s*"
    r"|(?:i\s+am|i'?m)?\s*(?:sorry|apologies)"
    r"(?:\s+to\s+hear\s+(?:that|this))?\b[.!]?\s*"
    r"|(?:i\s+understand|i\s+hear\s+you|no worries"
    r"|that(?:'s| is)\s+(?:frustrating|tough))\b[.!]?\s*"
    r")+",
    re.IGNORECASE,
)

#: Debris a removed preamble can leave at the front of the reply. A preamble is
#: allowed to end in a comma — "Sorry to hear that, the log shows a timeout" —
#: and once the preamble is gone that comma is the first thing the user reads.
_LEADING_DEBRIS = " \t\r\n.,:;!-–—"

#: How many sentences a tired reply is trimmed to.
_TIRED_SENTENCE_CAP = 3


def detect_user_mood(message: str) -> str:
    """Return the mood a message reads as.

    Args:
        message: What the user said.

    Returns:
        One of ``neutral``, ``frustrated``, ``excited`` or ``tired``. ``neutral``
        when the evidence does not clear the threshold — an assistant guessing at
        a mood it cannot support is worse than one that says nothing, because
        every wrong guess is a reply shaped around the wrong emotion.
    """
    text = str(message or "")
    if not text.strip():
        return NEUTRAL
    lowered = text.lower()

    scores: Dict[str, float] = {mood: 0.0 for mood in MOODS if mood != NEUTRAL}
    for mood, cues in _CUES.items():
        for pattern, weight in cues:
            if re.search(pattern, lowered):
                scores[mood] += weight

    # Punctuation and shouting are read as a multiplier on the leading mood rather
    # than as their own signal. "THIS IS BROKEN" and "this is broken" are the same
    # sentence with different emphasis, and treating the first as evidence of a
    # different emotion would misread the one that is louder.
    punctuation = min(
        _MAX_PUNCTUATION_WEIGHT, _EXCLAIM_WEIGHT * text.count("!")
    )
    if _SHOUT_RE.search(text):
        punctuation = min(_MAX_PUNCTUATION_WEIGHT, punctuation + 0.6)
    if punctuation:
        top = max(scores, key=lambda mood: scores[mood])
        scores[top] += punctuation

    best = max(scores, key=lambda mood: scores[mood])
    if scores[best] < _THRESHOLD:
        return NEUTRAL
    return best


def mood_hint(mood: str) -> str:
    """Return the instruction for a mood.

    Args:
        mood: One of the four moods.

    Returns:
        A sentence for the system prompt. An unrecognised mood yields the
        neutral instruction rather than raising, because this runs while
        composing a reply.
    """
    return _MOOD_HINTS.get(str(mood), _MOOD_HINTS[NEUTRAL])


def lead_in(mood: str) -> str:
    """Return the words a reply should open with, if any.

    Deliberately empty for every mood. An emotional lead-in is the easiest part
    of a reply to get wrong and the part with the least value: "I hear you" before
    the fix is the exact sentence a frustrated user does not want to read. The
    moods are handled by trimming and by the prompt instruction instead.
    """
    return _LEAD_INS.get(str(mood), "")


def adjust_response_tone(response: str, mood: str) -> str:
    """Adjust a reply's shape to the user's mood.

    Only the shape is touched — leading filler is dropped and a tired reply is
    trimmed. The substance is never rewritten, because this layer noticed a mood
    and is not entitled to have an opinion about the answer.

    Args:
        response: What ZUZZ said.
        mood: One of the four moods.

    Returns:
        The adjusted reply. The original object when there is nothing to do, so
        a caller can tell an unchanged reply from a reworded one.
    """
    text = str(response or "")
    if not text.strip():
        return text
    state = str(mood)

    if state in (FRUSTRATED, TIRED):
        trimmed = _PREAMBLE_RE.sub("", text, count=1)
        if trimmed != text:
            text = trimmed.strip().lstrip(_LEADING_DEBRIS).strip()

    if state == TIRED:
        sentences = [part for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
        if len(sentences) > _TIRED_SENTENCE_CAP:
            text = " ".join(sentences[:_TIRED_SENTENCE_CAP]).strip()

    prefix = lead_in(state)
    if prefix and not text.startswith(prefix):
        text = f"{prefix} {text}".strip()
    return text


def describe_moods() -> List[Dict[str, str]]:
    """Return every mood and its instruction, for the API and a settings page."""
    return [{"mood": mood, "instruction": mood_hint(mood)} for mood in MOODS]