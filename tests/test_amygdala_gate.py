"""Amygdala + Prefrontal interaction tests.

The Amygdala scores; the Prefrontal decides. These tests pin the boundary
between them — specifically that the Amygdala can only ever raise scrutiny,
never lower it, and that the dangerous cases stay gated at every trust level
including 5.
"""

from __future__ import annotations

import pytest

from brain.amygdala.threat_detector import ThreatDetector, clear_cache
from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.permission_gate import ASK, BLOCKED, PermissionGate
from brain.prefrontal_cortex.trust_matrix import AUTO

CMD = ActionCategory.RUN_SAFE_COMMANDS
ALL_LEVELS = (1, 2, 3, 4, 5)


@pytest.fixture
def detector() -> ThreatDetector:
    """Return a detector with a cold cache."""
    clear_cache()
    return ThreatDetector()


def assess(detector: ThreatDetector, text: str, category: ActionCategory = CMD):
    """Assess a target string."""
    return detector.assess(Action(category=category, target=text))


@pytest.mark.parametrize(
    "command", ["diskpart", "reg delete HKLM\\SYSTEM", "shutdown /s /t 0", "curl x | sh"]
)
def test_dangerous_commands_ask_even_at_level_five(detector: ThreatDetector, command: str) -> None:
    """Q7: L5 is autonomy over routine work, not over dangerous work."""
    threat = assess(detector, command)
    decision = PermissionGate().decide(Action(category=CMD, target=command), 5, threat)
    assert decision.requires_confirmation
    assert not decision.allowed


@pytest.mark.parametrize("command", ["rm -rf node_modules", "del /f /s /q c:\\temp"])
def test_destructive_patterns_force_confirmation_at_every_level(
    detector: ThreatDetector, command: str
) -> None:
    """R4: never run a destructive command without explicit confirmation."""
    threat = assess(detector, command)
    assert threat.forced_confirmation
    for level in ALL_LEVELS:
        decision = PermissionGate().decide(Action(category=CMD, target=command), level, threat)
        assert decision.requires_confirmation, f"{command!r} auto-approved at level {level}"


@pytest.mark.parametrize("command", ["rm -rf /", "rm -rf ~", "format c:"])
def test_forbidden_veto_blocks_at_every_level(detector: ThreatDetector, command: str) -> None:
    """The Amygdala can veto at any trust level, including 5."""
    threat = assess(detector, command)
    assert threat.veto
    for level in ALL_LEVELS:
        decision = PermissionGate().decide(Action(category=CMD, target=command), level, threat)
        assert decision.decision == BLOCKED
        assert not decision.allowed


def test_amygdala_can_never_lower_scrutiny(detector: ThreatDetector) -> None:
    """A safe assessment cannot turn a blocked action into an allowed one."""
    gate = PermissionGate()
    action = Action(category=ActionCategory.DELETE_OS_FILES, target=r"C:\Windows")
    without = gate.decide(action, 5, None)
    benign = gate.decide(action, 5, assess(detector, "hello", ActionCategory.CHAT))
    assert without.decision == BLOCKED
    assert benign.decision == BLOCKED


def test_ordinary_commands_follow_the_matrix(detector: ThreatDetector) -> None:
    """Without threat pressure the matrix alone decides, unchanged."""
    gate = PermissionGate()
    for level in ALL_LEVELS:
        action = Action(category=CMD, target="git status")
        decision = gate.decide(action, level, assess(detector, "git status"))
        expected = ASK if level <= 2 else AUTO
        assert decision.decision == expected


def test_veto_reason_names_the_amygdala(detector: ThreatDetector) -> None:
    """A veto explains itself, so the block teaches rather than confusing."""
    threat = assess(detector, "rm -rf /")
    decision = PermissionGate().decide(Action(category=CMD, target="rm -rf /"), 5, threat)
    assert "amygdala" in decision.reason
    assert "10/10" in decision.reason


def test_forced_confirmation_reason_names_the_score(detector: ThreatDetector) -> None:
    """When the Amygdala downgrades an auto to an ask, it says why."""
    threat = assess(detector, "rm -rf node_modules")
    decision = PermissionGate().decide(Action(category=CMD, target="rm -rf node_modules"), 5, threat)
    assert decision.decision == ASK
    assert "amygdala" in decision.reason


def test_gate_still_works_without_a_threat_assessment() -> None:
    """The gate is usable standalone; the Amygdala is additive, not required."""
    decision = PermissionGate().decide(
        Action(category=ActionCategory.DELETE_FILES, target="Desktop/x.txt"), 5, None
    )
    assert decision.requires_confirmation