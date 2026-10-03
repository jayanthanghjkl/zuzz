"""Spinal Cord + Prefrontal Cortex interaction tests.

The kill switch is consulted by the gate before anything else. These tests pin
the boundary: while it is engaged *every* action is denied — no trust level, no
threat score, no confirmation dialog — and while it is not engaged nothing about
the existing behaviour changes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brain.amygdala.threat_detector import ThreatDetector, clear_cache
from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.permission_gate import ASK, AUTO, BLOCKED, PermissionGate
from brain.prefrontal_cortex.trust_matrix import AUTO as MATRIX_AUTO
from brain.spinal_cord.kill_switch import KillSwitch
from core.audit import AuditLog

ALL_LEVELS = (1, 2, 3, 4, 5)
ALL_CATEGORIES = list(ActionCategory)


@pytest.fixture
def switch(tmp_path: Path) -> KillSwitch:
    """Return a switch with flags in the temp directory."""
    return KillSwitch(paths=[tmp_path / "stop.flag"], audit=AuditLog(tmp_path / "audit.jsonl"))


@pytest.fixture
def gate(switch: KillSwitch) -> PermissionGate:
    """Return a gate bound to the test switch."""
    return PermissionGate(kill_switch=switch)


def test_every_category_is_blocked_while_stopped(gate: PermissionGate, switch: KillSwitch) -> None:
    """The switch outranks the trust matrix entirely."""
    switch.trigger(reason="testing")
    for level in ALL_LEVELS:
        for category in ALL_CATEGORIES:
            decision = gate.decide(Action(category=category, target="anything"), level)
            assert decision.decision == BLOCKED, f"{category} at level {level}"


def test_no_trust_level_can_allow_anything_while_stopped(gate: PermissionGate, switch: KillSwitch) -> None:
    """Including level 5 — the stop is not a permission the user can raise."""
    switch.trigger(reason="testing")
    for level in ALL_LEVELS:
        decision = gate.decide(Action(category=ActionCategory.CHAT), level)
        assert decision.allowed is False


def test_still_stopped_actions_ask_for_confirmation(gate: PermissionGate, switch: KillSwitch) -> None:
    """A stop is a refusal, not a dialog that could be clicked through."""
    switch.trigger(reason="testing")
    decision = gate.decide(Action(category=ActionCategory.CHAT), 5)
    assert decision.requires_confirmation is False
    assert decision.decision == BLOCKED


def test_reason_names_the_source_and_cause(gate: PermissionGate, switch: KillSwitch) -> None:
    """The user is told what stopped the machine, not just that it stopped."""
    switch.trigger(source="watchdog", reason="cpu saturated at 99%")
    reason = gate.decide(Action(category=ActionCategory.CHAT), 1).reason
    assert "kill switch" in reason
    assert "watchdog" in reason
    assert "cpu saturated" in reason


def test_amygdala_veto_still_blocks_after_reset(gate: PermissionGate, switch: KillSwitch) -> None:
    """Resetting the switch must not weaken the layer beneath it."""
    switch.trigger()
    switch.reset(typed_word="RESET", confirmed=True)
    clear_cache()
    threat = ThreatDetector().assess(Action(category=ActionCategory.RUN_SAFE_COMMANDS, target="rm -rf /"))
    decision = gate.decide(Action(category=ActionCategory.RUN_SAFE_COMMANDS, target="rm -rf /"), 5, threat)
    assert decision.decision == BLOCKED
    assert "amygdala" in decision.reason


def test_matrix_behaviour_is_unchanged_while_running(gate: PermissionGate) -> None:
    """With the switch off, the gate decides exactly as before."""
    assert gate.stopped_reason() == ""
    chat = gate.decide(Action(category=ActionCategory.CHAT), 5)
    assert chat.decision == MATRIX_AUTO
    command = gate.decide(Action(category=ActionCategory.RUN_SAFE_COMMANDS, target="git status"), 1)
    assert command.decision == ASK


def test_matrix_behaviour_is_unchanged_at_every_level(gate: PermissionGate) -> None:
    """A running gate still follows the trust matrix for routine work."""
    for level in ALL_LEVELS:
        decision = gate.decide(Action(category=ActionCategory.RUN_SAFE_COMMANDS, target="git status"), level)
        assert decision.decision == (ASK if level <= 2 else AUTO)


def test_dry_run_is_blocked_while_stopped(gate: PermissionGate, switch: KillSwitch) -> None:
    """A dry run that needs permission is still permission-gated while stopped."""
    switch.trigger()
    decision = gate.decide(
        Action(category=ActionCategory.WRITE_FILES, target="x.txt", dry_run=True), 5
    )
    assert decision.dry_run is True
    assert decision.allowed is False


def test_gate_defaults_to_the_shared_switch() -> None:
    """A gate built with no arguments still consults the process-wide switch.

    This is what makes the switch impossible to bypass by forgetting to pass it.
    """
    gate = PermissionGate()
    assert gate.kill_switch is not None
    assert gate.kill_switch.is_killed() in (True, False)


def test_stopped_reason_is_empty_while_running(switch: KillSwitch) -> None:
    """A stopped_reason of "" is the signal the gate uses to skip the check."""
    gate = PermissionGate(kill_switch=switch)
    assert gate.stopped_reason() == ""
    switch.trigger(source="hotkey", reason="user")
    assert "hotkey" in gate.stopped_reason()
