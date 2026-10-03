"""Prefrontal Cortex — Immutable Rules.

Nine rules ZUZZ cannot be talked out of, held as data so they can be read,
logged and tested. This module is read-only by convention and its contents are
verified by hash at startup.

The hash is computed over a canonical serialisation of :data:`RULES`, not over
this file's text, so reformatting or editing a docstring cannot mask a change to
the rules themselves — and so the expected digest can live beside the data
without being self-referential.
"""

from __future__ import annotations

import hashlib
import json
from typing import Dict, List


class IntegrityError(RuntimeError):
    """Raised when the immutable rules do not match their recorded digest."""


RULES: List[Dict[str, str]] = [
    {
        "id": "R1",
        "rule": "Never delete operating system or system files.",
        "reason": "Destroys the machine beyond recovery without a reinstall.",
    },
    {
        "id": "R2",
        "rule": "Never send user data to unauthorised external parties.",
        "reason": "Data leaving the machine cannot be recalled.",
    },
    {
        "id": "R3",
        "rule": "Never disable safety checks or modify safety code.",
        "reason": "A guard that can be removed is not a guard.",
    },
    {
        "id": "R4",
        "rule": "Never run destructive commands without explicit user confirmation.",
        "reason": "Destructive commands often look harmless until they run.",
    },
    {
        "id": "R5",
        "rule": "Never access passwords or credentials without confirmation.",
        "reason": "Credential theft is indistinguishable from helpfulness.",
    },
    {
        "id": "R6",
        "rule": "Log ALL actions to the unmodifiable activity log.",
        "reason": "An unauditable agent cannot be trusted or debugged.",
    },
    {
        "id": "R7",
        "rule": "Never install software without user approval.",
        "reason": "Installed software persists and executes with user rights.",
    },
    {
        "id": "R8",
        "rule": "The kill switch is always active and cannot be disabled.",
        "reason": "The user must always be able to stop the agent.",
    },
    {
        "id": "R9",
        "rule": "The AI cannot change its own trust level.",
        "reason": "Self-promotion is privilege escalation by another name.",
    },
]


def canonical() -> str:
    """Return the canonical serialisation the digest is computed over.

    Returns:
        Deterministic JSON with sorted keys and no incidental whitespace.
    """
    return json.dumps(RULES, sort_keys=True, separators=(",", ":"))


def digest() -> str:
    """Return the SHA-256 of the canonical rule text.

    Returns:
        Hex digest of the current rules.
    """
    return hashlib.sha256(canonical().encode("utf-8")).hexdigest()


#: Digest of :data:`RULES` as approved on 2026-10-02. Changing a rule without
#: deliberately regenerating this constant makes :func:`verify` fail.
EXPECTED_DIGEST = "64643cb00da07ffef9a575eb698a8893a39c133adf6d7ad6faa8e25a150c335f"


def verify(expected: str = EXPECTED_DIGEST) -> bool:
    """Check the rules still match their recorded digest.

    Args:
        expected: Digest to compare against.

    Returns:
        True when the rules are intact.

    Raises:
        IntegrityError: When the rules have been altered.
    """
    actual = digest()
    if actual != expected:
        raise IntegrityError(
            f"immutable rules digest mismatch: expected {expected}, got {actual}"
        )
    return True


def verify_or_regenerate() -> str:
    """Verify the rules, or explain how to accept an intentional change.

    Used by the maintenance script rather than at runtime, where a mismatch
    must stop the boot instead of quietly rewriting the reference.

    Returns:
        The current digest when verification passes.

    Raises:
        IntegrityError: Always, with the digest needed to update the constant.
    """
    actual = digest()
    if actual != EXPECTED_DIGEST:
        raise IntegrityError(
            "immutable rules changed.\n"
            f"  current digest: {actual}\n"
            f"  to accept the change deliberately, set EXPECTED_DIGEST in\n"
            f"  brain/prefrontal_cortex/immutable_rules.py to that value."
        )
    return actual


def rule_ids() -> List[str]:
    """Return the rule identifiers, for tests and for the API.

    Returns:
        Rule ids in declaration order.
    """
    return [rule["id"] for rule in RULES]