"""Spinal Cord — switch persistence tests.

Two properties of the flag file that the rest of the switch depends on: it must
survive a restart, and one location failing must not disarm it. Also pins the
*known* limit — the switch is only as strong as files an agent cannot reach.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from brain.spinal_cord.kill_switch import RESET_WORD, KillSwitch
from brain.spinal_cord.stop_state import SOURCE_UI, flag_paths
from core.audit import AuditLog


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    """Return an audit log in the temp directory."""
    return AuditLog(tmp_path / "audit.jsonl")


@pytest.fixture
def switch(tmp_path: Path, audit: AuditLog) -> KillSwitch:
    """Return a switch with both flag locations inside the temp directory."""
    return KillSwitch(paths=[tmp_path / "storage" / "stop.flag", tmp_path / "tmp_stop.flag"], audit=audit)


# -- surviving a restart ----------------------------------------------------


def test_flag_survives_a_new_switch_instance(tmp_path: Path, audit: AuditLog) -> None:
    """ZUZZ comes back stopped if it crashed while stopped."""
    first = KillSwitch(paths=[tmp_path / "stop.flag"], audit=audit)
    first.trigger(source=SOURCE_UI, reason="crash loop")
    second = KillSwitch(paths=[tmp_path / "stop.flag"], audit=audit)
    assert second.is_killed()
    assert second.status()["reason"] == "crash loop"


def test_the_explanation_survives_too(tmp_path: Path, audit: AuditLog) -> None:
    """Coming back stopped without a reason would be confusing."""
    first = KillSwitch(paths=[tmp_path / "stop.flag"], audit=audit)
    first.trigger(source=SOURCE_UI, reason="watchdog tripped")
    second = KillSwitch(paths=[tmp_path / "stop.flag"], audit=audit)
    assert second.status()["reason"] == "watchdog tripped"
    assert second.status()["source"] == SOURCE_UI


def test_deleting_both_flags_by_hand_does_re_arm(tmp_path: Path, audit: AuditLog) -> None:
    """The known limit of flag-file state, recorded rather than hidden.

    The switch is only as strong as the files an agent cannot reach: an actor
    with filesystem access can remove both flags. What holds the line is that the
    reset path refuses without a human, and that R1 protects the storage
    directory from agent-proposed writes and deletes.
    """
    paths = [tmp_path / "stop.flag", tmp_path / "tmp_stop.flag"]
    switch = KillSwitch(paths=paths, audit=audit)
    switch.trigger(source=SOURCE_UI)
    for path in paths:
        path.unlink()
    assert switch.is_killed() is False


# -- reset history ----------------------------------------------------------


def test_reset_history_is_recorded(switch: KillSwitch) -> None:
    """The state file keeps the reset timestamps that drive the cooldown."""
    switch.trigger()
    switch.reset(typed_word=RESET_WORD, confirmed=True)
    state = json.loads(switch.state_path.read_text(encoding="utf-8"))
    assert state["resets"]
    assert switch.status()["resets_this_hour"] == 1


def test_reset_history_is_capped_by_the_window(switch: KillSwitch) -> None:
    """Resets from long ago do not accumulate into an endless penalty."""
    switch.write_state({"resets": [time.time() - 86400] * 5})
    assert switch.status()["resets_this_hour"] == 0


def test_state_survives_a_corrupt_file(switch: KillSwitch) -> None:
    """A truncated state file must not stop the switch from reporting.

    The flags decide whether ZUZZ is stopped; the state file is only the
    explanation, so a corrupt one is ignored rather than raised.
    """
    switch.state_path.parent.mkdir(parents=True, exist_ok=True)
    switch.state_path.write_text("{not json", encoding="utf-8")
    switch.trigger(source=SOURCE_UI)
    assert switch.is_killed()
    assert switch.status()["reason"] == ""


# -- flag locations ---------------------------------------------------------


def test_default_flag_paths_cover_storage_and_temp() -> None:
    """Two locations by default: project storage and the system temp dir."""
    paths = flag_paths()
    assert len(paths) == 2
    assert paths[0].name == "stop.flag"
    assert paths[1].name == "zuzz_stop.flag"
    assert paths[0] != paths[1]


def test_one_unwritable_location_does_not_prevent_the_other(switch: KillSwitch) -> None:
    """A locked path must not stop the other flag from arming."""
    switch.paths[0] = switch.paths[0].parent / "nope" / "stop.flag"
    switch.trigger(source=SOURCE_UI)
    assert switch.is_killed()
    assert switch.paths[1].is_file()


def test_status_lists_where_the_flags_are(switch: KillSwitch) -> None:
    """The UI must be able to show which locations are holding the stop."""
    switch.trigger(source=SOURCE_UI)
    status = switch.status()
    assert status["present_at"] == [str(path) for path in switch.paths]
    assert status["flag_paths"] == [str(path) for path in switch.paths]
