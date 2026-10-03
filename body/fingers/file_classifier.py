"""Body/Fingers — file classification.

The gate classifies paths with :mod:`core.paths`; the Body must
answer the same questions, and must answer them about the *real*
path — after symlink resolution, after ``\\\\?\\`` stripping, after
traversal. A link in the project directory pointing at
``C:\\Windows`` is a Windows file, whatever it is called here.

This module extends :mod:`core.paths` with the answers the Body
needs on its own:

* which files are secrets, blocked at every trust level even when
  the matrix would allow the read;
* which paths are ZUZZ's own — its code, its logs, its kill switch
  flags — where writes would break immutable rules R3, R6 and R8;
* how big a file is, in bytes and in a form a human can read.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Tuple

from core.config import ROOT
from core.paths import (
    CAUTION,
    PROTECTED,
    SAFE,
    TEMP,
    classify,
    normalise,
)

#: ZUZZ's own code. Writing here would be self-modification of the
#: safety-adjacent core, so the Body refuses writes and deletes even
#: at trust level 5.
ZUZZ_CORE_DIRS = ("brain", "body", "core", "config")

#: Root-level files that are part of ZUZZ's own machinery.
ZUZZ_CORE_FILES = (
    "start.py",
    "requirements.txt",
    "pytest.ini",
    "setup.bat",
    "stop.bat",
    ".gitignore",
)

#: Credential material and ZUZZ's own secrets. Unlike the protected
#: *prefixes* these are matched by name or extension anywhere on the
#: machine, because a secret in a download folder is still a secret.
SECRET_NAMES = (".env", ".npmrc", "credentials", "id_rsa", "id_ed25519")
SECRET_EXTENSIONS = (".pem", ".key")
SECRET_DIRS = (".ssh", ".aws", ".gnupg")
SECRET_EXACT = ("api_vault.enc", "vault.json", ".vault_key")


def classify_path(path: str) -> str:
    """Classify a path exactly the way the gate does.

    Args:
        path: File or directory path, in any spelling.

    Returns:
        One of ``safe``, ``caution``, ``temp`` or ``protected``.
    """
    return classify(path)


def resolve_symlink(path: str) -> str:
    """Follow one level of symlink.

    Args:
        path: A path that may be a symlink.

    Returns:
        The link's target, resolved against the link's directory when
        relative. A path that is not a symlink is returned unchanged
        and absolute. Case is preserved: this is the path the Body
        acts on, not the form it is compared in.
    """
    try:
        target = os.readlink(str(path))
    except (OSError, ValueError):
        return _absolute(str(path))
    if not os.path.isabs(target):
        target = os.path.join(os.path.dirname(str(path)), target)
    return _absolute(target)


def _absolute(path: str) -> str:
    """Expand and normalise a path, preserving case."""
    expanded = os.path.expandvars(os.path.expanduser(path))
    return os.path.normpath(expanded).replace("/", "\\")


def is_symlink(path: str) -> bool:
    """Return True when the path is a symlink (one level is enough)."""
    try:
        return os.path.islink(str(path))
    except (OSError, ValueError):
        return False


def is_secret_file(path: str) -> bool:
    """Return True for credential files, at any location.

    Hard-blocked regardless of trust level: the matrix can
    auto-approve a read at level 2, but a secret is never readable
    through the Body. ``.env.*`` variants, the ``.ssh``/``.aws``/
    ``.gnupg`` directories and their contents, private key
    extensions and the API vault are all covered.

    Args:
        path: File or directory path.

    Returns:
        True when the path is a secret, or lives inside a secret
        directory.
    """
    normalised = normalise(path)
    parts = Path(normalised).parts
    name = parts[-1] if parts else ""
    if any(part in SECRET_DIRS for part in parts):
        return True
    if name.startswith(".env") or name in SECRET_NAMES:
        return True
    if name in SECRET_EXACT:
        return True
    return name.lower().endswith(SECRET_EXTENSIONS)


def is_zuzz_own_file(path: str) -> bool:
    """Return True when the path is ZUZZ's own code, logs or state.

    The check behind the self-modification rules: ``brain/``,
    ``body/``, ``core/`` and ``config/`` are read-only to the agent,
    as are the append-only activity and audit logs and the kill
    switch flag files. ``plugins/`` is deliberately absent — writing
    plugins is how ZUZZ is allowed to grow.

    Args:
        path: File or directory path.

    Returns:
        True when the Body must refuse writes and deletes here.
    """
    from brain.spinal_cord.stop_state import flag_paths

    resolved, _ = resolve_absolute(path)
    if not resolved:
        return False
    normalised = normalise(resolved)
    absolute = Path(normalised)
    for directory in ZUZZ_CORE_DIRS:
        if normalised.startswith(normalise(ROOT / directory)):
            return True
    logs = normalise(ROOT / "storage" / "logs")
    if normalised.startswith(logs):
        return True
    if absolute.name in ZUZZ_CORE_FILES and normalised.startswith(
        normalise(ROOT)
    ):
        return True
    for flag in flag_paths():
        if normalised == normalise(flag) or normalised.startswith(
            normalise(flag)
        ):
            return True
    return False


def get_size_info(path: str) -> Dict[str, Any]:
    """Return a file's size in bytes and in human-readable form.

    Args:
        path: File or directory path.

    Returns:
        A dict with ``bytes`` and ``human`` keys; both zero when the
        path does not exist.
    """
    try:
        size = os.stat(str(path)).st_size
    except OSError:
        return {"bytes": 0, "human": "0 B"}
    return {"bytes": size, "human": human_size(size)}


def human_size(size: float) -> str:
    """Render a byte count the way a file manager would.

    Args:
        size: Byte count.

    Returns:
        For example ``"1.4 MB"``.
    """
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}".replace(".0 ", " ")
        size /= 1024
    return f"{size:.1f} TB"


def resolve_absolute(path: str) -> Tuple[str, bool]:
    """Resolve a path to an absolute form, preserving case.

    Relative paths are resolved against the project root — ZUZZ's
    working scope — not against whatever directory the server
    happened to start in, so the answer is the same for the API, the
    scheduler and the tests.

    The result is the path to *act on*. Comparisons — against a
    permission decision's target, against the protected prefixes —
    use :func:`core.paths.normalise`, which lower-cases, because
    NTFS is case-insensitive and ``C:\\Windows`` and ``c:\\windows``
    are the same directory.

    Args:
        path: Any path, absolute or relative.

    Returns:
        The resolved path, and True when the original was
        relative.
    """
    text = str(path).strip()
    if not text:
        relative = False
        absolute = ""
    elif os.path.isabs(text) or (len(text) > 1 and text[1] == ":"):
        relative = False
        absolute = text
    else:
        relative = True
        absolute = str(ROOT / text)
    return _absolute(absolute), relative


def escapes_project(path: str) -> bool:
    """Return True when a relative path climbs out of the project.

    Args:
        path: The path as it was given.

    Returns:
        True when the path is relative and resolves outside the
        project directory — the classic traversal shape.
    """
    resolved, relative = resolve_absolute(path)
    if not relative or not resolved:
        return False
    return not normalise(resolved).startswith(normalise(ROOT))
