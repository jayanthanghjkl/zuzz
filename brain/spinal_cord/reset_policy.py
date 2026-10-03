"""Spinal Cord — Reset Policy.

The rules for re-arming the machine, separated from the mechanics of writing and
removing flag files. Kept apart because these rules are the ones an attacker
would want to loosen, and a short file is a short thing to review.

Three requirements, all of them non-negotiable:

* **A human must confirm.** No code path resets the switch without an explicit
  confirmation, because an agent that could un-kill itself could recover from the
  runaway loop that triggered the stop.
* **The word must be typed.** Mirrors the trust-level confirmation, so the
  highest-consequence action in the system is guarded the same way.
* **Resets in quick succession wait longer.** A toggle loop is slowed without
  penalising an ordinary single stop (decision Q11).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import List, Tuple

from brain.spinal_cord.stop_state import cooldown_seconds

#: The user must type this to reset, mirroring the trust-level confirmation.
RESET_WORD = "RESET"


class ResetRefused(RuntimeError):
    """Raised when a reset did not satisfy its confirmation or cooldown."""


def check_confirmation(typed_word: str, confirmed: bool) -> None:
    """Verify the user confirmed the reset.

    Args:
        typed_word: What the user typed.
        confirmed: The user clicked confirm.

    Raises:
        ResetRefused: When either the click or the word is missing.
    """
    if not confirmed:
        raise ResetRefused("resetting the kill switch requires confirmation")
    if typed_word.strip().upper() != RESET_WORD:
        raise ResetRefused(f"type {RESET_WORD} to confirm the reset")


def cooldown_remaining(resets: List[float]) -> int:
    """Return seconds until the next reset is permitted.

    Args:
        resets: Reset timestamps inside the cooldown window, oldest first.

    Returns:
        Seconds left, or 0 when a reset is allowed right now.
    """
    if not resets:
        return 0
    remaining = cooldown_seconds(len(resets)) - (time.time() - resets[-1])
    return max(0, int(round(remaining)))


def check_cooldown(resets: List[float]) -> None:
    """Refuse a reset that arrives too soon after the previous one.

    Args:
        resets: Reset timestamps inside the cooldown window.

    Raises:
        ResetRefused: When the user must wait longer.
    """
    remaining = cooldown_remaining(resets)
    if remaining:
        raise ResetRefused(f"wait {remaining}s before resetting again")


def check_flags_cleared(paths: List[Path], survivors: List[Path]) -> None:
    """Refuse a reset when a flag file survived deletion.

    Reporting a successful reset while a flag survives is the dangerous
    direction: the UI would say ZUZZ is running while the gate still denies
    every action.

    Args:
        paths: Every flag location.
        survivors: Locations that could not be removed.

    Raises:
        ResetRefused: When anything is left in place.
    """
    if not survivors:
        return
    raise ResetRefused(
        "the stop flag could not be removed from "
        + ", ".join(str(path) for path in survivors)
        + " - ZUZZ stays stopped"
    )


def survivors_of(paths: List[Path], cleared: List[Path]) -> List[Path]:
    """Return the flag locations that were not successfully cleared.

    Args:
        paths: Every flag location.
        cleared: Locations that no longer exist.

    Returns:
        The locations still holding a stop flag.
    """
    return [path for path in paths if path not in cleared]


def approval_requirements(cooldown: int) -> Tuple[bool, str]:
    """Describe what the UI must collect before a reset will be accepted.

    Args:
        cooldown: Seconds the user must currently wait, 0 when none.

    Returns:
        ``(needs_confirmation, instruction)`` for the reset dialog.
    """
    if cooldown:
        return True, f"wait {cooldown}s, then type {RESET_WORD} to confirm"
    return True, f"type {RESET_WORD} to confirm"
