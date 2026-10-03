"""Trust level change tests.

Covers decision Q5: the trust level is the most security-critical setting in
ZUZZ, so raising it requires escalating confirmation and the AI cannot touch it
at all. Every test here is an attempt to raise the level without the user's
genuine, evidenced consent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brain.prefrontal_cortex.trust_manager import TrustChangeRefused, TrustManager


@pytest.fixture
def trust(tmp_path: Path) -> TrustManager:
    """Return a trust manager backed by a temporary state file."""
    return TrustManager(path=tmp_path / "trust_level.json")


def test_default_trust_level_is_one(trust: TrustManager) -> None:
    """Level 1 is the default, per the approved matrix."""
    assert trust.current() == 1


@pytest.mark.parametrize(
    "target,requirement",
    [(1, "instant"), (2, "simple"), (3, "explain"), (4, "warn"), (5, "type_confirm")],
)
def test_confirmation_requirement_escalates(
    trust: TrustManager, target: int, requirement: str
) -> None:
    """Each level up requires a stronger confirmation than the last."""
    assert trust.requirement_for(target) == requirement


def test_downgrade_is_instant(trust: TrustManager) -> None:
    """Dropping to level 1 needs no confirmation — it only reduces risk."""
    assert trust.request_change(1) == 1
    trust.save(5)
    assert trust.request_change(1) == 1
    assert trust.current() == 1


def test_rising_a_level_requires_confirmation(trust: TrustManager) -> None:
    """An agent cannot bump its own trust level by asking nicely."""
    with pytest.raises(TrustChangeRefused, match="requires confirmation"):
        trust.request_change(2)


def test_level_five_requires_the_word_and_the_cooldown(trust: TrustManager) -> None:
    """Level 5 needs the typed word *and* the cooldown."""
    trust.save(4)
    with pytest.raises(TrustChangeRefused, match="typing CONFIRM"):
        trust.request_change(5, confirmed=True, typed_word="yes")
    with pytest.raises(TrustChangeRefused, match="cooldown"):
        trust.request_change(5, confirmed=True, typed_word="CONFIRM")
    assert trust.request_change(
        5, confirmed=True, typed_word="CONFIRM", acknowledged_cooldown=True
    ) == 5


def test_level_five_accepts_the_word_case_insensitively(trust: TrustManager) -> None:
    """The user is not punished for their keyboard's shift key."""
    trust.save(4)
    assert trust.request_change(
        5, confirmed=True, typed_word="confirm", acknowledged_cooldown=True
    ) == 5


def test_changing_to_the_same_level_is_a_no_op(trust: TrustManager) -> None:
    """Re-requesting the current level does not demand confirmation."""
    trust.save(3)
    assert trust.request_change(3) == 3


def test_corrupt_state_file_falls_back_to_level_one(trust: TrustManager) -> None:
    """A tampered or broken state file must not silently grant autonomy."""
    trust.path.parent.mkdir(parents=True, exist_ok=True)
    trust.path.write_text("{not json", encoding="utf-8")
    assert trust.current() == 1


def test_state_file_cannot_store_an_out_of_range_level(trust: TrustManager) -> None:
    """Hand-editing the state file to level 99 is clamped, not honoured."""
    trust.path.parent.mkdir(parents=True, exist_ok=True)
    trust.path.write_text(json.dumps({"trust_level": 99}), encoding="utf-8")
    assert trust.current() == 5


def test_negative_level_in_state_is_clamped_up(trust: TrustManager) -> None:
    """A negative level cannot be used to escape the clamp's upper bound."""
    trust.path.parent.mkdir(parents=True, exist_ok=True)
    trust.path.write_text(json.dumps({"trust_level": -4}), encoding="utf-8")
    assert trust.current() == 1


def test_describe_change_gives_the_ui_what_it_needs(trust: TrustManager) -> None:
    """The confirmation dialog can be rendered from the manager alone."""
    described = trust.describe_change(5)
    assert described["current_level"] == 1
    assert described["target_level"] == 5
    assert described["requirement"] == "type_confirm"
    assert described["confirmation_word"] == "CONFIRM"
    assert described["cooldown_seconds"] == 10
    assert described["target_description"]


def test_save_persists_across_managers(tmp_path: Path) -> None:
    """The level survives a restart, so it cannot be reset by relaunching."""
    state = tmp_path / "trust_level.json"
    TrustManager(path=state).save(3)
    assert TrustManager(path=state).current() == 3