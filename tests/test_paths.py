"""Path classification tests.

The Prefrontal Cortex and the Body's file layer must classify a path the same
way, or the gate blocks what the Body refuses — or worse, allows what the Body
deletes. These tests pin that shared answer.
"""

from __future__ import annotations

import pytest

from core.config import ROOT
from core.paths import CAUTION, PROTECTED, SAFE, TEMP, classify, is_protected, is_temp


@pytest.mark.parametrize(
    "path,expected",
    [
        (r"C:\Windows\System32\config", PROTECTED),
        (r"c:/program files/app", PROTECTED),
        (r"C:\$Recycle.Bin\x", PROTECTED),
        (str(ROOT / ".env"), PROTECTED),
        (str(ROOT / ".ENV"), PROTECTED),
        (str(ROOT / "docs" / "README.md"), SAFE),
        (str(ROOT / "brain" / "thalamus"), SAFE),
        ("Desktop", CAUTION),
        ("Documents", CAUTION),
        ("Downloads", CAUTION),
        ("desktop", CAUTION),
        (str(ROOT / "temp" / "x"), TEMP),
    ],
)
def test_classify(path: str, expected: str) -> None:
    """Paths are classified the way both the gate and the Body expect."""
    assert classify(path) == expected


def test_protected_beats_temp() -> None:
    """A temp-looking path under a protected root stays protected."""
    assert classify(r"C:\Windows\Temp\junk.tmp") == PROTECTED


def test_system_temp_is_temp() -> None:
    """The system temp directory counts as disposable."""
    import tempfile

    assert is_temp(str(tempfile.gettempdir()) + "\\scratch.tmp")


def test_normalisation_defeats_case_and_separator_differences() -> None:
    """`C:\\WINDOWS` and `c:/windows` are the same directory."""
    assert is_protected(r"c:\windows\system32") is is_protected("C:/Windows/System32")


def test_ssh_private_key_is_protected() -> None:
    """Credential material is protected wherever it lives."""
    assert is_protected(r"C:\Users\me\.ssh\id_rsa") is True
    assert is_protected("id_rsa") is True
    assert is_protected(r"C:\Users\me\.ssh\id_ed25519") is True


def test_project_path_is_not_protected() -> None:
    """The project directory itself is ordinary safe space."""
    assert is_protected(str(ROOT)) is False


@pytest.mark.parametrize(
    "root",
    [
        "C:\\",
        "C:/",
        "c:\\",
        "D:\\",
        # Drive-relative. On Windows ``cd c:`` and ``Remove-Item c:`` both mean
        # the root of the C volume, so matching only the trailing-backslash
        # spelling would leave the same drive reachable by dropping one character.
        "c:",
        "Z:",
    ],
)
def test_volume_root_classified_protected(root: str) -> None:
    """A bare volume root is protected, not safe.

    Found in Session 13 review: ``C:\\`` classified as ``safe``, because every
    other protected entry in :mod:`core.paths` works by matching a *prefix*, and
    a volume root has no parent left to match. That put "delete C:\\" one trust
    level away from a recycle-bin exception aimed at temporary files.

    The positive half matters as much: this is a check on the whole drive, not
    on user data inside it, so ``C:\\Users`` must still classify as ordinary
    safe space rather than becoming collateral damage.
    """
    assert classify(root) == PROTECTED
    assert is_protected(root) is True


@pytest.mark.parametrize(
    "inside",
    ["C:\\Users", "C:\\Users\\jayan\\notes.txt", "D:\\Projects\\app"],
)
def test_volume_root_protection_does_not_extend_to_its_contents(inside: str) -> None:
    """Protection stops at the root; ordinary paths inside the volume are untouched.

    The failure mode of over-correcting here is worse than the one being fixed:
    if ``C:\\Users`` came back protected, every file the project touches on this
    machine would refuse to delete, and the agent would be useless rather than
    safe.
    """
    assert classify(inside) != PROTECTED


def test_delete_volume_root_is_refused_at_every_trust_level() -> None:
    """"Delete C:\\" is refused by the immutable rules, so it never parks for approval.

    The Amygdala call is the end of the chain rather than the gate's: a refusal
    at this level is unconditional. Trust level 4 auto-approves ordinary
    deletions and even temp-cleanup, so a permission to park "delete C:\\" for
    the user to approve would have meant a one-click path to erasing a drive
    from a reply that merely asked.
    """
    from brain.amygdala.threat_detector import ThreatDetector
    from brain.prefrontal_cortex.action import Action, ActionCategory
    from brain.prefrontal_cortex.permission_gate import PermissionGate

    gate = PermissionGate()
    for level in (1, 2, 3, 4, 5):
        action = Action(category=ActionCategory.DELETE_FILES, target="C:\\")
        decision = gate.decide(action, level, ThreatDetector().assess(action))
        assert decision.blocked is True, f"trust level {level} did not refuse a volume root"
        assert decision.requires_confirmation is False, level
        assert "R1" in decision.reason, decision.reason