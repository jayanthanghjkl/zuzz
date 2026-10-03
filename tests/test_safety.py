"""Safety tests for the Prefrontal Cortex.

These are written as attacks, not happy paths. Each test tries to get something
done that ZUZZ promised it would not do, and asserts that it fails.

No test here performs a real destructive action — the Body layer does not exist
yet, so the tests assert on decisions rather than on side effects.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.immutable_rules import (
    EXPECTED_DIGEST,
    RULES,
    IntegrityError,
    digest,
    rule_ids,
    verify,
)
from brain.prefrontal_cortex.permission_gate import PermissionGate
from brain.prefrontal_cortex.trust_matrix import ASK, AUTO, BLOCKED
from brain.prefrontal_cortex.trust_manager import TrustChangeRefused, TrustManager
from core.config import ROOT, get_trust_levels
from core.paths import CAUTION, PROTECTED, SAFE, TEMP, classify

ALL_LEVELS = (1, 2, 3, 4, 5)


@pytest.fixture
def gate() -> PermissionGate:
    """Return a gate reading the real permission matrix."""
    return PermissionGate()


@pytest.fixture
def trust(tmp_path: Path) -> TrustManager:
    """Return a trust manager backed by a temporary state file."""
    return TrustManager(path=tmp_path / "trust_level.json")


def act(category: ActionCategory, target: str = "", **kwargs) -> Action:
    """Build an action concisely."""
    return Action(category=category, target=target, **kwargs)


# --------------------------------------------------------------------------
# Immutable rules
# --------------------------------------------------------------------------


def test_rules_match_their_digest() -> None:
    """The rules are the ones that were approved."""
    assert digest() == EXPECTED_DIGEST


def test_verify_raises_when_rules_are_altered(monkeypatch) -> None:
    """An attacker who edits a rule trips the integrity check."""
    monkeypatch.setitem(RULES[0], "rule", "Deletion of system files is fine.")
    with pytest.raises(IntegrityError, match="digest mismatch"):
        verify()


def test_rule_ids_are_unique_and_ordered() -> None:
    """Rules are individually addressable, so a log can cite one."""
    ids = rule_ids()
    assert ids == sorted(set(ids), key=ids.index)
    assert len(ids) == len(RULES)


def test_every_rule_has_a_reason() -> None:
    """A rule without a reason cannot be explained to the user."""
    assert all(rule.get("reason") for rule in RULES)


# --------------------------------------------------------------------------
# Trust matrix, table-driven

# --------------------------------------------------------------------------
# Immutable rule enforcement
# --------------------------------------------------------------------------


@pytest.mark.parametrize("level", ALL_LEVELS)
@pytest.mark.parametrize(
    "category", ["delete_os_files", "share_data", "disable_safety"]
)
def test_immutable_rules_block_at_every_level(gate: PermissionGate, level: int, category: str) -> None:
    """Rules R1, R2 and R3 forbid these outright at every trust level."""
    decision = gate.decide(act(ActionCategory(category)), level)
    assert decision.blocked
    assert not decision.allowed
    assert not decision.requires_confirmation
    assert decision.rule_id is not None


@pytest.mark.parametrize("level", ALL_LEVELS)
def test_installing_software_always_asks_and_cites_r7(gate: PermissionGate, level: int) -> None:
    """R7 forbids *silent* installation, so it asks at every level, citing R7.

    Not blocked: the user is allowed to install software deliberately.
    """
    decision = gate.decide(act(ActionCategory.INSTALL_SOFTWARE, "some-package"), level)
    assert decision.requires_confirmation
    assert not decision.allowed
    assert decision.rule_id == "R7"


@pytest.mark.parametrize("level", ALL_LEVELS)
def test_deleting_a_system_path_is_blocked_even_as_a_plain_delete(
    gate: PermissionGate, level: int
) -> None:
    """Asking to delete C:\\Windows\\System32 is rule R1 whatever the category."""
    decision = gate.decide(act(ActionCategory.DELETE_FILES, r"C:\Windows\System32\drivers"), level)
    assert decision.blocked
    assert decision.rule_id == "R1"


@pytest.mark.parametrize("level", ALL_LEVELS)
def test_writing_into_a_protected_directory_is_blocked(gate: PermissionGate, level: int) -> None:
    """An attacker cannot smuggle a write past the gate via write_files."""
    decision = gate.decide(act(ActionCategory.WRITE_FILES, r"C:\Program Files\evil.dll"), level)
    assert decision.blocked


def test_writing_to_the_env_file_is_blocked(gate: PermissionGate) -> None:
    """The API vault's own input file is protected inside the project too."""
    decision = gate.decide(act(ActionCategory.WRITE_FILES, str(ROOT / ".env")), 5)
    assert decision.blocked
    assert decision.rule_id == "R1"


@pytest.mark.parametrize("level", ALL_LEVELS)
def test_credentials_always_require_confirmation(gate: PermissionGate, level: int) -> None:
    """Rule R5 holds: credentials are never silently accessed."""
    decision = gate.decide(act(ActionCategory.ACCESS_CREDENTIALS, "ssh key"), level)
    assert decision.requires_confirmation
    assert decision.rule_id == "R5"


# --------------------------------------------------------------------------
# Q1 / Q3 decisions: deletion, and the temp-file exception
# --------------------------------------------------------------------------


@pytest.mark.parametrize("level", ALL_LEVELS)
@pytest.mark.parametrize(
    "target", ["Desktop/report.docx", "Documents/notes.txt", "Downloads/setup.exe"]
)
def test_deleting_user_files_asks_at_every_level(gate: PermissionGate, level: int, target: str) -> None:
    """Q3: user file deletion is never auto-approved, not even at level 5."""
    decision = gate.decide(act(ActionCategory.DELETE_FILES, target), level)
    assert decision.requires_confirmation
    assert not decision.allowed


@pytest.mark.parametrize("level", ALL_LEVELS)
def test_temp_deletion_threshold_is_level_three(gate: PermissionGate, level: int) -> None:
    """Q3 exception: temp files may be cleaned without asking from level 3."""
    decision = gate.decide(act(ActionCategory.DELETE_FILES, str(ROOT / "temp" / "scratch.bin")), level)
    if level < 3:
        assert decision.requires_confirmation
        assert not decision.allowed
    else:
        assert decision.allowed
        assert not decision.requires_confirmation


@pytest.mark.parametrize("level", ALL_LEVELS)
def test_writing_to_desktop_asks_at_level_one_only(gate: PermissionGate, level: int) -> None:
    """Q1: writes to caution paths ask at L1 and auto-approve from L2."""
    decision = gate.decide(act(ActionCategory.WRITE_FILES, "Desktop/notes.md"), level)
    assert decision.decision == (ASK if level == 1 else AUTO)


# --------------------------------------------------------------------------
# Dry run
# --------------------------------------------------------------------------


def test_dry_run_is_carried_on_the_decision(gate: PermissionGate) -> None:
    """A dry-run decision is marked, so the Body layer cannot ignore it."""
    decision = gate.decide(act(ActionCategory.MOUSE_KEYBOARD, "click 5,5", dry_run=True), 3)
    assert decision.dry_run
    assert decision.allowed
    assert decision.action.describe() == "would mouse keyboard click 5,5"


def test_dry_run_does_not_bypass_a_block(gate: PermissionGate) -> None:
    """Asking for a dry run is not a way to get a blocked action approved."""
    decision = gate.decide(act(ActionCategory.DELETE_OS_FILES, dry_run=True), 5)
    assert decision.blocked

