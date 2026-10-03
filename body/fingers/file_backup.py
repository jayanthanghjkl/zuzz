"""Body/Fingers — backups before overwrite.

Overwriting a file is irreversible the moment it happens, so the
Body keeps a short history beside the original: ``name.bak.1`` is
the most recent previous version, ``name.bak.2`` the one before
that, up to ``max_backups`` versions. Rotation happens *before*
the write, using moves rather than deletes, so no Body code ever
has to destroy a file to make room.

Every backup call carries the same :class:`PermissionDecision` as
the write it serves: a backup is itself a write, and it must not
happen under authority the write did not have.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import List, Optional

from brain.prefrontal_cortex.permission_decision import PermissionDecision

from body.fingers.exceptions import DecisionRefused

#: Number of previous versions kept beside the original.
MAX_BACKUPS = 3


def backup_before_write(
    decision: PermissionDecision,
    path: str,
    max_backups: int = MAX_BACKUPS,
) -> Optional[str]:
    """Snapshot the current version of a file before it is overwritten.

    Called by :meth:`~body.fingers.file_manager.FileManager.write`
    before the first byte changes. Files that do not exist yet have
    nothing to back up, and appends are not overwrites, so both
    return ``None`` without touching the disk.

    Args:
        decision: The decision authorising the write this serves.
        path: The file about to be overwritten.
        max_backups: How many versions to keep.

    Returns:
        The path of the newest ``.bak.1`` file, or ``None`` when
        there was nothing to back up.

    Raises:
        DecisionRefused: When the decision is missing or not allowed.
    """
    _require(decision)
    if not os.path.isfile(str(path)):
        return None
    if max_backups < 1:
        return None
    source = Path(str(path))
    for version in range(max_backups, 1, -1):
        _move(source.with_suffix(source.suffix + f".bak.{version - 1}"),
              source.with_suffix(source.suffix + f".bak.{version}"))
    _move(source, source.with_suffix(source.suffix + ".bak.1"))
    return str(source.with_suffix(source.suffix + ".bak.1"))


def list_backups(path: str) -> List[str]:
    """Return the ``.bak`` files kept beside a file, newest first.

    Read-only metadata about ZUZZ's own backups, so it needs no
    permission decision. Secret files never have backups listed:
    backing them up would copy the secret.

    Args:
        path: The file whose backups are wanted.

    Returns:
        Backup paths, newest version first.
    """
    from body.fingers.file_classifier import is_secret_file

    if is_secret_file(path):
        return []
    source = Path(str(path))
    backups = [
        str(candidate)
        for candidate in Path(source.parent).glob(source.name + ".bak.*")
        if candidate.is_file()
    ]
    return sorted(
        backups,
        key=lambda candidate: _version(candidate, source.name),
        reverse=True,
    )


def restore_backup(
    decision: PermissionDecision,
    path: str,
    backup_version: int,
) -> str:
    """Move a backup back over the current file.

    Args:
        decision: The decision authorising the write.
        path: The file to restore into.
        backup_version: Which version — ``1`` is the newest.

    Returns:
        The path that was restored.

    Raises:
        DecisionRefused: When the decision is missing or not allowed.
        FileNotFoundError: When that version does not exist.
    """
    from body.fingers.exceptions import ZuzzCoreRefused
    from body.fingers.file_classifier import (
        is_secret_file,
        is_zuzz_own_file,
    )

    _require(decision)
    if is_secret_file(path) or is_zuzz_own_file(path):
        raise ZuzzCoreRefused(f"refusing to restore into {path}")
    source = Path(str(path))
    backup = source.with_suffix(source.suffix + f".bak.{backup_version}")
    if not backup.is_file():
        raise FileNotFoundError(f"no backup version {backup_version} for {path}")
    shutil.move(str(backup), str(source))
    return str(source)


def _require(decision: PermissionDecision) -> None:
    """Refuse the call unless the decision permits execution."""
    if not isinstance(decision, PermissionDecision):
        raise DecisionRefused(
            "backup requires a PermissionDecision from the permission gate"
        )
    if decision.blocked or not decision.allowed:
        raise DecisionRefused(
            f"backup refused: the decision is {decision.decision} "
            f"({decision.reason})"
        )


def _version(candidate: str, stem: str) -> int:
    """Return the version number of a backup path, 0 when unparseable.

    Args:
        candidate: The backup path, as a string, because
            :func:`list_backups` sorts the string paths it is
            about to return rather than temporary ``Path``
            objects.
        stem: The original file name, e.g. ``data.txt``.

    Returns:
        The trailing integer — ``data.txt.bak.2`` gives 2.
    """
    suffix = os.path.basename(candidate)[len(stem):]
    try:
        return int(suffix.rsplit(".", 1)[-1])
    except (IndexError, ValueError):
        return 0


def _move(source: Path, destination: Path) -> None:
    """Move a file, overwriting the destination when it exists."""
    if source.exists():
        if destination.exists():
            os.replace(str(source), str(destination))
        else:
            shutil.move(str(source), str(destination))
