"""Fingers — file and directory operations behind the gate.

Every public method in this package takes a
:class:`~brain.prefrontal_cortex.permission_decision.PermissionDecision`
as its first argument, with three documented exceptions
that are system-level rather than agent actions:

* ``TrashManager.list_trash`` and ``TrashManager.trash_stats``
  — read-only metadata about ZUZZ's own trash, which a file
  manager shows without asking, exactly as it shows its own
  recycle bin without asking;
* ``TrashManager.auto_purge_old`` — the scheduled purge job.

``file_backup.list_backups`` is metadata for the same reason.

That list is closed, and
``test_system_methods_are_exactly_the_documented_system_methods``
pins it: a new decision-free method is a test failure, not a
quiet widening of the surface that skips the gate.

See :mod:`body.fingers.guards` for the checks every
operation runs, and :mod:`body.fingers.exceptions` for
how refusals are classified.
"""

from body.fingers.directory_manager import DirectoryManager
from body.fingers.file_backup import (
    backup_before_write,
    list_backups,
    restore_backup,
)
from body.fingers.file_classifier import (
    classify_path,
    get_size_info,
    is_secret_file,
    is_zuzz_own_file,
    resolve_symlink,
)
from body.fingers.file_manager import FileManager
from body.fingers.trash_manager import TrashManager

__all__ = [
    "DirectoryManager",
    "FileManager",
    "TrashManager",
    "backup_before_write",
    "classify_path",
    "get_size_info",
    "is_secret_file",
    "is_zuzz_own_file",
    "list_backups",
    "resolve_symlink",
    "restore_backup",
]
