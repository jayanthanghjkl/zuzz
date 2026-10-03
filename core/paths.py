"""Protected and temp path classification.

The Prefrontal Cortex and the Body's file layer must answer "is this path
protected?" identically. If they disagree, the gate blocks what the Body
refuses — or worse, the gate allows what the Body deletes. So the answer lives
here, once, and both sides import it.

Three classifications:

* ``protected`` — operating system or credential files. Blocked at every trust
  level, and writes to them are blocked too.
* ``temp`` — the project's ``temp/`` directory and the system temp directory.
  Safe to clean up, so deletion may auto-approve from trust level 3.
* ``caution`` — Desktop, Documents, Downloads. Real user data: writes and
  deletions ask, even at the highest trust level.

Anything else is ``safe``: the project directory itself.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Set

from core.config import ROOT

SAFE = "safe"
CAUTION = "caution"
PROTECTED = "protected"
TEMP = "temp"

# System locations that must never be written to or deleted. Compared as
# normalised prefixes, so C:\Windows\System32 is protected because C:\Windows is.
PROTECTED_PREFIXES = (
    os.environ.get("SystemRoot", r"C:\Windows"),
    os.environ.get("ProgramFiles", r"C:\Program Files"),
    os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    os.environ.get("ProgramData", r"C:\ProgramData"),
    r"C:\$Recycle.Bin",
    r"C:\System Volume Information",
)

# Credential files are protected wherever they live, including inside the
# project directory — `.env` is the API vault's input and must not be readable
# or writable by an action the agent proposed. Compared lower-cased, because
# NTFS is case-insensitive and `C:\USERS\ME\.ENV` is the same file.
PROTECTED_NAMES = frozenset(
    {name.lower() for name in (".env", "id_rsa", "id_ed25519", ".npmrc", "credentials", "shadow")}
)

CAUTION_NAMES = frozenset(
    name.lower() for name in ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos")
)


def _normalise(path: str) -> str:
    """Return a comparable form of a path.

    Args:
        path: Any filesystem path.

    Returns:
        The path with separators normalised, expanded, and lower-cased, so
        comparisons are not defeated by ``C:\\Windows`` vs ``c:/windows``.

    The Win32 ``\\\\?\\`` extended-length prefix is stripped first: the
    shell APIs treat ``\\\\?\\C:\\Windows`` and ``C:\\Windows`` as the
    same directory, and a classifier that missed the equivalence would
    let a guarded path be reached by spelling it with the prefix.
    """
    text = str(path).strip()
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    expanded = os.path.expandvars(os.path.expanduser(text))
    return os.path.normpath(expanded).replace("/", "\\").lower()


def normalise(path: str) -> str:
    """Return the comparable form of a path, as the classifiers use.

    Args:
        path: Any filesystem path.

    Returns:
        The normalised, lower-cased path. The Body layer resolves paths
        to this form before classifying them and before matching them
        against a permission decision's target, so a decision cannot be
        replayed against a differently-spelled path.
    """
    return _normalise(path)


def _protected_set() -> Set[str]:
    """Return the normalised protected prefixes."""
    return {_normalise(prefix) for prefix in PROTECTED_PREFIXES}


def is_protected(path: str) -> bool:
    """Return True when a path is a system location or a credential file.

    Args:
        path: File or directory path.

    Returns:
        True when the path must not be written to or deleted.
    """
    normalised = _normalise(path)
    if any(normalised.startswith(prefix) for prefix in _protected_set()):
        return True
    return Path(normalised).name in PROTECTED_NAMES


def is_temp(path: str) -> bool:
    """Return True when a path is inside a disposable temp directory.

    Relative paths are resolved against the project root as well, because the
    body layer works in paths like ``temp/scratch.bin`` and that must classify
    the same as the absolute equivalent — otherwise the temp-deletion exception
    would silently not apply to the paths ZUZZ actually produces.

    Args:
        path: File or directory path.

    Returns:
        True for the project's ``temp/`` directory and the system temp
        directory.
    """
    normalised = _normalise(path)
    if not Path(str(path)).is_absolute():
        normalised = _normalise(ROOT / str(path))
    candidates = [_normalise(ROOT / "temp"), _normalise(tempfile.gettempdir())]
    return any(normalised.startswith(candidate) for candidate in candidates)


def classify(path: str) -> str:
    """Classify a path as protected, temp, caution or safe.

    Protection is checked first: a temp directory inside the system temp path
    that also matched a protected prefix must never come back as merely
    disposable.

    Args:
        path: File or directory path.

    Returns:
        One of ``protected``, ``temp``, ``caution`` or ``safe``.
    """
    if is_protected(path):
        return PROTECTED
    if is_temp(path):
        return TEMP
    if Path(_normalise(path)).name in CAUTION_NAMES:
        return CAUTION
    return SAFE