"""Cerebellum — tone detection.

Works out how formal ZUZZ should be for the message it is about to answer, by
reading the user's own register rather than by guessing from context.

The base formality is ``config/personality.json``'s ``style``. This module only
adjusts *from* it, and only downwards for casualness and upwards for distance —
because the failure modes are not symmetric. Answering a "yo what's up" in a
formal register reads as a machine being difficult; answering a formal request
casually reads as a colleague who does not take the work seriously.

The adjustment is bounded to one step either way. A single exclamation mark does
not make ZUZZ shout back, and three curse words do not make it cold — a tone that
swings with the input is worse than a tone that stays put, because the user cannot
predict which ZUZZ is answering them.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

__all__ = [
    "CASUAL",
    "FORMAL",
    "NEUTRAL",
    "detect_formality",
    "formality_hint",
]

#: The three registers ZUZZ can speak in, ordered by formality.
NEUTRAL = "neutral"
CASUAL = "casual"
FORMAL = "formal"

_FORMALITY_ORDER: Tuple[str, ...] = (CASUAL, NEUTRAL, FORMAL)

#: How far each configured style starts from the middle.
_STYLE_BASIS: Dict[str, str] = {
    "formal_friendly": FORMAL,
    "terse": NEUTRAL,
    "neutral": NEUTRAL,
    "casual": CASUAL,
    "playful": CASUAL,
}

#: Openers that mean the user is being loose. Matched on the whole token so
#: "hi" in "this" cannot count.
_CASUAL_OPENERS = (
    r"hey", r"hi\b", r"yo\b", r"sup\b", r"heya", r"howdy", r"morning", r"evening",
    r"whats?\s?up", r"wassup", r"gonna", r"wanna", r"gotta", r"lemme", r"kinda",
    r"sorta", r"dunno", r"yeah", r"yep", r"nah", r"nope", r"cool\b", r"nice\b",
)

#: Request words that pull the register down.
_CASUAL_WORDS = (
    r"hah+", r"\blol\b", r"lmao", r"hmm+", r"\bok\b", r"\bokay\b", r"\bkk\b",
    r"thx", r"\bty\b", r"\bpls\b", r"plz", r"thnx", r"\bnp\b", r"idk", r"tbh",
    r"imo", r"\bbc\b",
)

#: Openers and closers that mean the user has chosen a formal register.
_FORMAL_OPENERS = (
    r"good (?:morning|afternoon|evening)", r"hello", r"dear", r"please",
    r"kindly", r"could you", r"would you", r"would you mind", r"i would like",
    r"i'd like", r"i am writing", r"thank you", r"thanks", r"regards",
    r"sincerely", r"kind regards", r"best regards", r"cheers",
)

#: Register-neutral technical vocabulary. A user writing about a segfault is not
#: being terse or formal; they are being a programmer, and the tone should not
#: move at all. Matching this is what stops ``fix the null pointer`` reading as
#: aggressive.
_TECHNICAL = (
    r"stack ?trace", r"traceback", r"exception", r"null ?pointer", r"segfault",
    r"compile", r"compiler", r"runtime", r"function", r"variable", r"api",
    r"endpoint", r"database", r"query", r"schema", r"migration", r"deploy",
    r"config(?:uration)?", r"repository", r"commit", r"branch", r"merge",
    r"async", r"thread", r"socket", r"regex", r"module", r"dependency",
    r"typescript", r"python", r"javascript", r"rust", r"docker", r"kubernetes",
    r"localhost", r"http", r"json", r"yaml", r"permission", r"refactor",
    r"test", r"tests", r"debug", r"bug", r"crash", r"hangs?",
)

_WORD_RE = re.compile(r"[a-z']+")


def _tokens(message: str) -> List[str]:
    """Return the lowercase word tokens of a message."""
    return _WORD_RE.findall(str(message or "").lower())


def _has_phrase(tokens: List[str], patterns: Tuple[str, ...]) -> bool:
    """Return True when any pattern matches at the head of the token stream.

    Matching against ``" ".join(tokens)`` rather than token-by-token so a
    multi-word phrase like "could you" is matched whole.

    Args:
        tokens: The message's tokens.
        patterns: Regex alternatives to look for.

    Returns:
        True when something matched.
    """
    joined = " ".join(tokens)
    return any(re.search(pattern, joined) for pattern in patterns)


def detect_formality(message: str, style: str = "formal_friendly") -> str:
    """Return how formal the reply should be.

    Args:
        message: The user's message.
        style: The configured base style, from ``config/personality.json``.

    Returns:
        ``casual``, ``neutral`` or ``formal``. Never further than one step from
        the configured style, whatever the message contains.
    """
    basis = _STYLE_BASIS.get(str(style).strip().lower(), NEUTRAL)
    position = _FORMALITY_ORDER.index(basis)

    tokens = _tokens(message)
    if not tokens:
        return basis

    # Technical vocabulary is checked first and can only cancel a shift towards
    # coldness, never one towards warmth. A developer writing "ok, fix the segfault"
    # has said nothing about register, and reading the "ok" as informality would
    # have ZUZZ congratulate them on their bug.
    technical = _has_phrase(tokens, _TECHNICAL)
    casual = _has_phrase(tokens, _CASUAL_OPENERS) or _has_phrase(tokens, _CASUAL_WORDS)
    formal = _has_phrase(tokens, _FORMAL_OPENERS)

    if formal and not casual:
        position += 1
    elif casual and not formal:
        position -= 1
    if technical and position > _FORMALITY_ORDER.index(basis):
        position = _FORMALITY_ORDER.index(basis)
    return _FORMALITY_ORDER[max(0, min(len(_FORMALITY_ORDER) - 1, position))]


def formality_hint(formality: str) -> str:
    """Return the one-line instruction for a register.

    Args:
        formality: One of ``casual``, ``neutral`` or ``formal``.

    Returns:
        A sentence for the system prompt. An unrecognised value yields the
        neutral instruction rather than raising: this runs while composing a
        reply, and a bad register name must not be the reason nothing gets said.
    """
    hints = {
        CASUAL: "Match a relaxed register: plain words, no ceremony, no preamble.",
        NEUTRAL: "Use a plain, even register. Neither stiff nor chatty.",
        FORMAL: (
            "Use a courteous, professional register. Still direct — formality "
            "is not padding."
        ),
    }
    return hints.get(str(formality), hints[NEUTRAL])