"""Spinal Cord — reset behaviour tests.

What the switch does when the user asks to re-arm the machine, exercised through
:meth:`~brain.spinal_cord.kill_switch.KillSwitch.reset` rather than through the
policy helpers. What matters here is that a refusal leaves ZUZZ stopped.

The rules the reset enforces in isolation are pinned in ``test_reset_policy.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brain.spinal_cord.kill_switch import RESET_WORD, KillSwitch, ResetRefused
from brain.spinal_cord.stop_state import COOLDOWN_LADDER
from core.audit import AuditLog


@pytest.fixture
def switch(tmp_path: Path) -> KillSwitch:
    """Return a switch with both flag locations inside the temp directory."""
    audit = AuditLog(tmp_path / "audit.jsonl")
    return KillSwitch(paths=[tmp_path / "stop.flag", tmp_path / "tmp_stop.flag"], audit=audit)


def reset(switch: KillSwitch, word: str = RESET_WORD) -> dict:
    """Reset a switch with valid confirmation."""
    return switch.reset(typed_word=word, confirmed=True)


# -- confirmation -----------------------------------------------------------


def test_reset_requires_confirmation(switch: KillSwitch) -> None:
    """Nothing self-heals: an agent cannot click the dialog."""
    switch.trigger()
    with pytest.raises(ResetRefused):
        switch.reset(typed_word=RESET_WORD, confirmed=False)
    assert switch.is_killed()


def test_reset_requires_the_typed_word(switch: KillSwitch) -> None:
    """The word is required even with confirmation."""
    switch.trigger()
    for word in ("", "yes", "CONFIRM", "RESET NOW", "unreset"):
        with pytest.raises(ResetRefused):
            switch.reset(typed_word=word, confirmed=True)
    assert switch.is_killed()


def test_reset_word_is_case_insensitive(switch: KillSwitch) -> None:
    """A user typing in a hurry should not be punished for a capital R."""
    switch.trigger()
    switch.reset(typed_word="reset", confirmed=True)
    assert switch.is_killed() is False


def test_reset_clears_both_locations(switch: KillSwitch) -> None:
    """After a reset neither location reports a stop."""
    switch.trigger()
    reset(switch)
    assert not any(path.exists() for path in switch.paths)
    assert switch.status()["engaged"] is False


def test_reset_when_not_stopped_is_a_no_op(switch: KillSwitch) -> None:
    """Resetting a running machine does nothing rather than erroring."""
    assert switch.reset(typed_word="", confirmed=False)["engaged"] is False


# -- fail safe --------------------------------------------------------------


def test_reset_refuses_when_a_flag_survives(tmp_path: Path) -> None:
    """A flag that cannot be deleted keeps ZUZZ stopped.

    Reporting a successful reset while a flag survives is the dangerous
    direction: the UI would say running while the gate still denies everything.
    On Windows a read-only file can still be deleted, so the handle is held open
    instead — an open file cannot be removed on either platform.
    """
    free = tmp_path / "free_stop.flag"
    held = tmp_path / "held_stop.flag"
    switch = KillSwitch(paths=[free, held], audit=AuditLog(tmp_path / "audit.jsonl"))
    switch.trigger()

    handle = open(held, "r", encoding="utf-8")
    try:
        with pytest.raises(ResetRefused) as exc:
            reset(switch)
        assert "stays stopped" in str(exc.value)
    finally:
        handle.close()
    assert switch.is_killed()
    assert free.exists() is False


def test_reset_is_audited(switch: KillSwitch) -> None:
    """Re-arming the machine is the record worth having."""
    audit = switch.audit
    switch.trigger()
    reset(switch)
    summaries = [event["summary"] for event in audit.by_event("kill_switch")]
    assert "reset by user" in summaries[-1]


# -- cooldown ---------------------------------------------------------------


def test_first_reset_is_immediate(switch: KillSwitch) -> None:
    """A single stop does not make the user wait."""
    switch.trigger()
    assert switch.cooldown_remaining() == 0
    reset(switch)


def test_second_reset_immediately_after_the_first_waits(switch: KillSwitch) -> None:
    """A toggle loop is slowed after the first, immediate reset."""
    switch.trigger()
    reset(switch)
    switch.trigger()
    remaining = switch.cooldown_remaining()
    assert 0 < remaining <= COOLDOWN_LADDER[1]
    with pytest.raises(ResetRefused) as exc:
        reset(switch)
    assert f"wait {remaining}s" in str(exc.value)
    assert switch.is_killed()


def test_status_tells_the_ui_what_a_reset_needs(switch: KillSwitch) -> None:
    """The dialog must state the word and any pending wait."""
    switch.trigger()
    status = switch.status()
    assert status["reset_requires_confirmation"] is True
    assert RESET_WORD in status["reset_instruction"]

    reset(switch)
    switch.trigger()
    assert "wait" in switch.status()["reset_instruction"]


def test_running_switch_asks_for_no_reset(switch: KillSwitch) -> None:
    """Nothing to confirm when there is nothing to undo."""
    status = switch.status()
    assert status["reset_requires_confirmation"] is False
    assert status["reset_instruction"] == ""
