"""Spinal Cord — reset policy tests.

The rules for re-arming the machine, tested apart from the flag-file mechanics.
These are the rules an attacker would want to loosen, so each one is pinned
directly rather than only through the switch that uses them.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from brain.spinal_cord.reset_policy import (
    RESET_WORD,
    ResetRefused,
    approval_requirements,
    check_cooldown,
    check_confirmation,
    check_flags_cleared,
    cooldown_remaining,
    survivors_of,
)
from brain.spinal_cord.stop_state import COOLDOWN_LADDER, cooldown_seconds


# -- confirmation -----------------------------------------------------------


def test_confirmation_is_required() -> None:
    """Nothing self-heals: an agent cannot click the dialog."""
    with pytest.raises(ResetRefused) as exc:
        check_confirmation(RESET_WORD, confirmed=False)
    assert "confirmation" in str(exc.value)


@pytest.mark.parametrize("word", ["", "yes", "maybe", "RESET NOW", "CONFIRM", "unreset"])
def test_only_the_exact_word_is_accepted(word: str) -> None:
    """The word must be typed, exactly."""
    with pytest.raises(ResetRefused):
        check_confirmation(word, confirmed=True)


@pytest.mark.parametrize("word", ["RESET", "reset", "  RESET  ", "Reset"])
def test_word_is_case_and_whitespace_insensitive(word: str) -> None:
    """A user typing in a hurry should not be punished for a capital R."""
    check_confirmation(word, confirmed=True)


def test_confirmation_order_is_click_then_word() -> None:
    """Both are required; neither alone is enough."""
    with pytest.raises(ResetRefused):
        check_confirmation("", confirmed=False)


# -- cooldown ---------------------------------------------------------------


def test_no_resets_means_no_wait() -> None:
    """A single stop does not make the user wait."""
    assert cooldown_remaining([]) == 0


def test_second_reset_waits_five_seconds() -> None:
    """One reset in history already puts the next one behind the first rung.

    The ladder is indexed by how many resets are already on record, so the very
    first reset of a session — with nothing in history — is the immediate one.
    """
    assert cooldown_remaining([time.time()]) == COOLDOWN_LADDER[1]


def test_cooldown_ladder_is_progressive() -> None:
    """Repeated resets wait longer each time, then stop growing at the cap."""
    ladder = list(COOLDOWN_LADDER)
    assert [cooldown_seconds(n) for n in range(len(ladder))] == ladder
    assert cooldown_seconds(len(ladder)) == ladder[-1]
    assert cooldown_seconds(99) == ladder[-1]
    assert cooldown_seconds(0) == 0


def test_cooldown_counts_up_with_each_recent_reset() -> None:
    """A longer reset history means a longer next wait."""
    for count, expected in enumerate(COOLDOWN_LADDER):
        remaining = cooldown_remaining([time.time()] * count)
        if expected == 0:
            assert remaining == 0
        else:
            assert expected - 1 <= remaining <= expected


def test_cooldown_runs_down_over_time() -> None:
    """The wait shrinks as time passes, so it is a timer not a tally."""
    assert cooldown_remaining([time.time() - COOLDOWN_LADDER[1]]) == 0
    assert cooldown_remaining([time.time() - 1]) < COOLDOWN_LADDER[1]


def test_check_cooldown_names_the_wait() -> None:
    """The refusal says how long, so the UI can show a countdown."""
    with pytest.raises(ResetRefused) as exc:
        check_cooldown([time.time()] * 2)
    assert "wait" in str(exc.value) and "s before" in str(exc.value)


def test_check_cooldown_allows_the_first_reset() -> None:
    """No history means no refusal."""
    check_cooldown([])


# -- surviving flags --------------------------------------------------------


def test_survivors_excludes_cleared_paths(tmp_path: Path) -> None:
    """Only the locations that survived count as survivors."""
    first, second = tmp_path / "a.flag", tmp_path / "b.flag"
    assert survivors_of([first, second], [first]) == [second]
    assert survivors_of([first, second], [first, second]) == []


def test_cleared_flags_are_accepted() -> None:
    """A clean reset goes through."""
    check_flags_cleared([Path("a.flag"), Path("b.flag")], [])


def test_surviving_flag_refuses_the_reset(tmp_path: Path) -> None:
    """A flag that cannot be deleted keeps ZUZZ stopped.

    Reporting a successful reset while a flag survives is the dangerous
    direction: the UI would say running while the gate still denies everything.
    """
    held = tmp_path / "held.flag"
    with pytest.raises(ResetRefused) as exc:
        check_flags_cleared([held], [held])
    assert "stays stopped" in str(exc.value)
    assert str(held) in str(exc.value)


# -- UI guidance ------------------------------------------------------------


def test_instruction_always_asks_for_the_word() -> None:
    """The dialog must state the word even when no wait is pending."""
    _, instruction = approval_requirements(0)
    assert RESET_WORD in instruction


def test_instruction_includes_the_wait_when_cooling_down() -> None:
    """A pending cooldown is stated up front."""
    needs_confirm, instruction = approval_requirements(7)
    assert needs_confirm is True
    assert "wait 7s" in instruction
