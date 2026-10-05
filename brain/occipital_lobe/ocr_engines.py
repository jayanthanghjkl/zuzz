"""Occipital Lobe — the OCR engines and the ladder between them.

Separated from :mod:`brain.occipital_lobe.ocr_reader` so that "how confident is this
answer" is answerable in one place. The question is the awkward one: Moondream does
not return a calibrated confidence, and Tesseract does not either through
``pytesseract.image_to_data`` on most builds. Rather than pick a number and call it a
measurement, both answers get a **stated** confidence whose provenance is in the name.

* :func:`plausible_confidence` — used for a Moondream transcription that is
  non-empty, is not a refusal, and contains something that looks like words. This is
  a heuristic floor, not a measurement, and it is deliberately set exactly at the
  ladder's default trigger: a normal read therefore costs one model call, and only a
  reply that looks wrong pays for the second opinion.
* :data:`TESSERACT_CONFIDENCE` — used for a Tesseract read, for the same reason and
  with the same caveat.

The consequence is worth stating plainly: the ladder compares a heuristic against a
configured trigger, so raising the trigger above the floor in
``config/vision_thresholds.json`` is how the operator asks for two engines every
time. Lowering it below the floor makes the second read advisory.
"""

from __future__ import annotations

import re
from typing import Optional

from brain.occipital_lobe.untrusted_text import UntrustedText

#: A Tesseract read is credited with this, stated as a constant because it is one.
TESSERACT_CONFIDENCE = 0.80

#: The floor for a Moondream transcription that looks like text. Equals the
#: configured ladder trigger by default, which is what keeps the common case to one
#: model call.
PLAUSIBLE_CONFIDENCE = 0.85

#: Replies that mean the model declined rather than transcribed. Without this, a
#: refusal sentence would be carried onward as though it were what the screen said.
REFUSAL = re.compile(
    r"\b(i (cannot|can't|am unable|couldn't)|no text (is )?visible|nothing to read|"
    r"i do not see|not sure|unable to)\b",
    re.IGNORECASE,
)

#: Something long enough to be a word. A transcription of a blank region comes back
#: as punctuation and stray characters, and that is not a read worth quoting.
WORD = re.compile(r"[a-z0-9]{2,}", re.IGNORECASE)


def plausible_confidence(text: str) -> float:
    """Return the confidence to credit a Moondream transcription with.

    Args:
        text: What the model said.

    Returns:
        :data:`PLAUSIBLE_CONFIDENCE` when the reply is non-empty, is not a refusal,
        and contains something word-like; otherwise 0.0, which sends the caller to
        the second engine.
    """
    candidate = str(text or "").strip()
    if not candidate or REFUSAL.search(candidate) or not WORD.search(candidate):
        return 0.0
    return PLAUSIBLE_CONFIDENCE


def agree(first: str, second: str) -> bool:
    """Return True when two engines read the same pixels the same way.

    Args:
        first: The first read.
        second: The second read.

    Returns:
        True when the normalised texts are equal. Comparison ignores case,
        whitespace and internal line breaks, because those are the differences two
        engines produce through no disagreement at all — flagging them would mean the
        disagreement report fires on every single read.
    """
    return normalise(first) == normalise(second)


def normalise(text: str) -> str:
    """Return text reduced to the form two engines should agree on.

    Args:
        text: An engine's answer.

    Returns:
        Lower-cased, whitespace-collapsed text.
    """
    return " ".join(str(text).lower().split())


def tesseract(image: bytes) -> Optional[UntrustedText]:
    """Read an image with Tesseract, if it is installed and working.

    Returns None rather than raising when it is unavailable. Absence is expected on a
    fresh machine — Tesseract is a second opinion, not a dependency — and turning
    its absence into an exception would make the fallback mandatory, which is the
    opposite of what the ladder is for.

    Args:
        image: Raw image bytes.

    Returns:
        The read, or None when Tesseract is absent, broken, or returned nothing.
    """
    try:
        import io

        import pytesseract
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(image)) as source:
            text = pytesseract.image_to_string(source).strip()
    except Exception:
        # pytesseract raises its own errors, including the one raised when the
        # Tesseract binary is installed as a Python package but not on the machine.
        # All of them mean the same thing here: no second read was obtained.
        return None
    if not text:
        return None
    return UntrustedText(text, "tesseract", TESSERACT_CONFIDENCE, "ocr")