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