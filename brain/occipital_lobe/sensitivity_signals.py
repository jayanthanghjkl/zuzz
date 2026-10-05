"""Occipital Lobe — the signals that mark a screen as sensitive.

The pattern tables and the pure functions over them, kept apart from
:class:`~brain.occipital_lobe.visual_classifier.VisualClassifier` so that the whole
body of "what counts as credential material" can be read — and argued about — without
the gating and audit-logging code around it.

Three kinds of signal, and the difference between them is the whole design:

* **visual** — a run of four or more masked characters is a password field whatever
  the OCR said. This is the signal that catches a password manager, where the
  surrounding text is the app's name and nothing else.
* **shape** — a token matching a known key format. The value is never quoted into a
  finding, only the kind of thing it was.
* **keyword** — words like "password" or "recovery phrase" on their own. These produce
  ``caution``, never ``sensitive``: a login form is an ordinary thing to be looking at,
  and a classifier that calls every one sensitive teaches the user to click through the
  warning, which is the failure a warning exists to prevent.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence, Tuple

#: The three verdicts, in increasing order of concern.
SAFE = "safe"
CAUTION = "caution"
SENSITIVE = "sensitive"

#: Characters a masked password field is drawn with. Six are listed because the three
#: common ones are not universal: Windows uses bullets, browsers use bullets, and some
#: Linux toolkits use asterisks or a filled block.
MASK_CHARACTERS = "•●▪▫*·◦⁃∙"

#: How many masked characters make a run worth reporting. Four, because a two-dot
#: ellipsis and a stray asterisk are ordinary punctuation.
MIN_MASK_RUN = 4

#: Words that mean a credential is on screen somewhere on it. Caution, not sensitive.
CREDENTIAL_WORDS = (
    "password", "passphrase", "passcode", "pin entry", "one-time code",
    "recovery phrase", "seed phrase", "private key", "api key", "access token",
    "auth token", "credit card", "card number", "cvv", "security code",
)

#: Application names that are credential surfaces in their own right.
CREDENTIAL_WINDOWS = (
    "keepass", "lastpass", "bitwarden", "1password", "dashlane", "enpass",
    "roboform", "gnome-keyring", "windows security", "credential manager",
)

#: Key shapes, for a token sitting on screen in a code editor or a terminal.
KEY_SHAPES: Sequence[Any] = (
    ("api_key", re.compile(r"\b(?:sk|gsk|xoxb|xoxp|ghp|gho|npm|glpat)[-_][A-Za-z0-9_-]{16,}\b")),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("huggingface_token", re.compile(r"\bhf_[A-Za-z0-9]{20,}\b")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}")),
    ("assignment", re.compile(
        r"\b(?:api[_-]?key|secret|password|token|access[_-]?key)\b\s*[:=]\s*"
        r"[\"']?[A-Za-z0-9._~+/-]{12,}",
        re.IGNORECASE,
    )),
    # The same credential with the separator lost, which is how OCR renders a real
    # login form: the label and the field are separate layout elements, and the space
    # between them survives while the colon does not. Kept separate from the
    # keyword-only case on purpose -- a bare "password" with no value beside it is
    # `caution`, because every login form looks like that, whereas a *value* next to
    # the word is credential material regardless of how the screen spelled it.
    ("labelled_secret", re.compile(
        r"\b(?:password|passphrase|passcode|api[_-]?key|secret|access[_-]?key|token)\b"
        r"\s+[\"']?[A-Za-z0-9._~+/-]{8,}",
        re.IGNORECASE,
    )),
)

#: The model's own verdict, matched leniently on purpose: a small model asked for a
#: strict format will sometimes answer ``SENSITIVE: yes`` with a colon.
MODEL_YES = re.compile(r"SENSITIVE\s*[:=]?\s*yes", re.IGNORECASE)

#: A credential keyword with a value beside it, in the several spellings a UI
#: produces. Deliberately looser than the ``labelled_secret`` shape above, because
#: this pattern exists only to scrub text that is *about to be written to the audit
#: log*, where a false positive costs a dropped phrase and a false negative costs a
#: secret in an append-only file. ``redact`` classifies; this one only cleans.
LABELLED_VALUE = re.compile(
    r"\b(?:password|passphrase|passcode|pin|secret|credential|api[\s_-]?key|"
    r"access[\s_-]?key|auth[\s_-]?token|access[\s_-]?token|bearer|token|key)\b"
    r"\s*(?:is|was|are|:|=|-|of)\s*"
    r"[\"']?[^\s\"']{4,}",
    re.IGNORECASE,
)

#: A run of digits long enough to be a card number, with the spaces and dashes a
#: payment form renders them with.
CARD_SHAPE = re.compile(r"\b(?:\d[ -]?){13,19}\b")

#: Any run of mask characters, so evidence can never quote a password field even
#: as a row of bullets.
MASK_RUN = re.compile(f"[{re.escape(MASK_CHARACTERS)}]{{{MIN_MASK_RUN},}}")

#: The length above which a bare token is treated as a secret-shaped value. 14
#: characters of mixed case, digits and no spaces is not a word; it is the shape
#: every generated credential has.
_SECRET_TOKEN_MIN = 14


def _looks_secret(token: str) -> bool:
    """Return True when one bare token has the shape of a generated secret.

    The last of the three passes, and the one that catches what a table of formats
    cannot: a credential in a format nobody wrote a pattern for. It cannot tell such
    a token from a long product name, which is why :func:`safe_evidence` *withholds*
    the evidence rather than guessing at which it was — the cost of a dropped phrase
    is a less informative audit entry, and the cost of guessing wrong is a secret in
    a file nothing ever prunes.

    Args:
        token: One whitespace-delimited token.

    Returns:
        True when it is long enough and mixes cases and digits.
    """
    if len(token) < _SECRET_TOKEN_MIN:
        return False
    return (
        any(char.islower() for char in token)
        and any(char.isupper() for char in token)
        and any(char.isdigit() for char in token)
    )


def safe_evidence(text: str, limit: int = 80) -> str:
    """Return model evidence that cannot carry a credential value.

    :func:`redact` handles the formats in :data:`KEY_SHAPES`. This adds the three
    things that table cannot: a credential beside a label in any spelling, a card
    number, and a run of mask characters. What survives all of them is then checked
    token by token, and anything still secret-shaped causes the whole phrase to be
    withheld rather than quoted.

    So the caller's guarantee is not "we redact most credentials" but **"the string
    this returns contains no credential value"** — proven by
    ``test_classifier_never_echoes_credential_values``, which is the only kind of
    promise a log file is allowed to make.

    Args:
        text: Untrusted text, typically a model's ``EVIDENCE`` line.
        limit: How much of the surviving phrase to keep.

    Returns:
        A short, credential-free phrase, or an empty string when the evidence itself
        had to be withheld. Empty is an honest answer: the model saw a credential
        surface, and that is what the finding records.
    """
    out = redact(str(text or ""))
    out = MASK_RUN.sub("<redacted:masked_input>", out)
    out = LABELLED_VALUE.sub("<redacted:labelled_value>", out)
    out = CARD_SHAPE.sub("<redacted:card_number>", out)
    out = " ".join(out.split())
    if any(_looks_secret(token) for token in out.split()):
        return ""
    return out[:limit]


def verdict(findings: Sequence[Dict[str, str]]) -> Dict[str, Any]:
    """Return the classification for a set of findings.

    Args:
        findings: Everything the signals found.

    Returns:
        ``classification``, the most concerned severity among the findings, plus the
        findings themselves and a one-line summary.
    """
    severities = [finding.get("severity", CAUTION) for finding in findings]
    if SENSITIVE in severities:
        classification = SENSITIVE
    elif CAUTION in severities:
        classification = CAUTION
    else:
        classification = SAFE
    return {
        "classification": classification,
        "sensitive": classification == SENSITIVE,
        "findings": [dict(finding) for finding in findings],
        "summary": (
            "no credential material detected"
            if classification == SAFE
            else f"{classification}: " + ", ".join(finding["id"] for finding in findings)
        ),
    }


def mask_runs(body: str) -> List[Dict[str, str]]:
    """Return a finding for each run of masked characters in some text.

    Args:
        body: The OCR text.

    Returns:
        One finding per run of four or more mask characters, giving the run's length
        rather than its characters. The length of a masked field is not a secret;
        echoing its characters into a report serves nobody.
    """
    pattern = re.compile(f"[{re.escape(MASK_CHARACTERS)}]{{{MIN_MASK_RUN},}}")
    return [
        {
            "id": "masked_input",
            "label": f"a masked field ({len(match.group(0))} characters hidden)",
            "signal": "visual",
            "severity": SENSITIVE,
        }
        for match in pattern.finditer(body)
    ]


def key_shapes(body: str) -> List[Dict[str, str]]:
    """Return a finding for each recognised credential shape in some text.

    Args:
        body: The OCR text.

    Returns:
        One finding per shape matched, naming the shape and never the value.
    """
    return [
        {"id": identifier, "label": label, "signal": "shape", "severity": SENSITIVE}
        for identifier, label in _shape_labels()
        if identifier and _pattern_for(identifier).search(body)
    ]


def _shape_labels() -> List[Tuple[str, str]]:
    """Return a human label for each shape in :data:`KEY_SHAPES`.

    Derived from the identifier rather than stored beside it, so the table stays a
    flat tuple of ``(identifier, pattern)`` and a shape cannot acquire a label that
    disagrees with its name.
    """
    return [(identifier, identifier.replace("_", " ")) for identifier, _ in KEY_SHAPES]


def _pattern_for(identifier: str) -> Any:
    """Return the compiled pattern for one shape identifier.

    Args:
        identifier: The shape's name.

    Returns:
        The pattern, or a never-matching one if the name is unknown.
    """
    for name, pattern in KEY_SHAPES:
        if name == identifier:
            return pattern
    return re.compile(r"(?!x)x")


def redact(body: str) -> str:
    """Return text with every recognised credential token replaced by its shape.

    The one place untrusted text could carry a secret into the audit log: the model is
    shown the screen and asked what it sees, and a small model asked to describe a
    login form will cheerfully quote the password back. Redacting by shape rather than
    dropping the line keeps the evidence useful -- "an API key (api_key)" still tells
    an investigator what was on screen -- while making the value unrecoverable.

    Not a general secret detector and does not claim to be. It covers the formats in
    :data:`KEY_SHAPES`, which are the ones that appear as text on a screen, and the
    residual risk is called out rather than papered over: a credential in no known
    format still reaches the log, so callers handling model output should treat the
    result as evidence to be read by a human, not as something safe to publish.

    Args:
        body: Untrusted text, typically a model's ``EVIDENCE`` line.

    Returns:
        The text with every recognised token replaced by ``<redacted:shape>``.
    """
    out = str(body or "")
    for identifier, pattern in KEY_SHAPES:
        out = pattern.sub(f"<redacted:{identifier}>", out)
    return out


def keywords(body: str) -> List[str]:
    """Return the credential-related words present in some text.

    Args:
        body: The OCR text.

    Returns:
        The matched words and application names, de-duplicated and sorted.
    """
    lowered = body.lower()
    found = {word for word in CREDENTIAL_WORDS if word in lowered}
    found.update(window for window in CREDENTIAL_WINDOWS if window in lowered)
    return sorted(found)