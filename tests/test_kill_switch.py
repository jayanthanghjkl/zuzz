"""Spinal Cord — kill switch tests.

The switch is the last line of defence, so these tests attack it: can anything
re-arm it without a human, and can a stop be lost to a file that fails to write.

The three concerns are split across files so each stays readable:

* ``test_switch_reset.py`` — confirmation, cooldown and the fail-safe reset.
* ``test_switch_persistence.py`` — restart survival and flag-file behaviour.
* ``test_reset_policy.py`` — the confirmation rules on their own.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brain.spinal_cord.kill_switch import KillSwitch
from brain.spinal_cord.stop_state import (
    SOURCE_HOTKEY,
    SOURCE_UI,
    SOURCE_WATCHDOG,
    SOURCES,
)
from core.audit import AuditLog


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    """Return an audit log in the temp directory."""
    return AuditLog(tmp_path / "audit.jsonl")


@pytest.fixture
def switch(tmp_path: Path, audit: AuditLog) -> KillSwitch:
    """Return a switch with both flag locations inside the temp directory."""
    return KillSwitch(
        paths=[tmp_path / "storage" / "stop.flag", tmp_path / "tmp_stop.flag"], audit=audit
    )


# -- the switch engages ----------------------------------------------------


def test_fresh_switch_is_not_engaged(switch: KillSwitch) -> None:
    """A new switch allows everything."""
    assert switch.is_killed() is False


def test_trigger_writes_both_locations(switch: KillSwitch) -> None:
    """Both flags are written, so one deleted file cannot disarm the switch."""
    switch.trigger(source=SOURCE_UI, reason="test")
    assert all(path.is_file() for path in switch.paths)
    assert switch.is_killed()


def test_trigger_records_reason_and_source(switch: KillSwitch) -> None:
    """The UI can explain *why* ZUZZ stopped."""
    switch.trigger(source=SOURCE_HOTKEY, reason="runaway loop")
    status = switch.status()
    assert status["source"] == SOURCE_HOTKEY
    assert status["reason"] == "runaway loop"
    assert status["since"]


def test_second_trigger_keeps_the_first_reason(switch: KillSwitch) -> None:
    """The first cause is the one worth reading in an audit."""
    switch.trigger(source=SOURCE_UI, reason="original")
    switch.trigger(source=SOURCE_WATCHDOG, reason="overwritten")
    assert switch.status()["reason"] == "original"
    assert switch.status()["source"] == SOURCE_UI


def test_unknown_trigger_source_is_refused(switch: KillSwitch) -> None:
    """Only the four documented sources can stop ZUZZ."""
    with pytest.raises(ValueError):
        switch.trigger(source="telepathy")


@pytest.mark.parametrize("source", SOURCES)
def test_trigger_engages_the_switch_for_every_source(switch: KillSwitch, source: str) -> None:
    """Every documented source genuinely stops the machine."""
    switch.trigger(source=source, reason=source)
    assert switch.is_killed()


def test_trigger_is_audited(switch: KillSwitch, audit: AuditLog) -> None:
    """Engaging the stop is a safety event, not ordinary activity."""
    switch.trigger(source=SOURCE_HOTKEY, reason="user pressed ctrl+shift+q")
    events = audit.by_event("kill_switch")
    assert events and "ctrl+shift+q" in events[-1]["summary"]


def test_trigger_needs_no_confirmation(switch: KillSwitch) -> None:
    """A stop that asked for confirmation would not work in an emergency."""
    switch.trigger(source=SOURCE_UI, reason="pressing stop mid-outage")
    assert switch.is_killed()
