"""Body/Fingers — file operations behind the gate.

Read, write, copy, move, rename, delete, stat. Every method
takes a :class:`~brain.prefrontal_cortex.permission_decision.PermissionDecision`
as its first argument and refuses to run without one: the
signature *is* the enforcement, not a convention.

The Body never decides. It re-checks the decision (including
a kill switch that may have engaged since the decision was
issued), resolves symlinks so the *target* is classified,
applies the shared guards, and only then touches the disk.

Size and encoding rules:

* reads default to a 5 MB cap with a 50 MB hard maximum;
* text is decoded UTF-8, then UTF-8 with BOM, then latin-1;
* binary content is refused as text, with the first 100 bytes
  in hex so the caller can see what it is;
* a locked file is waited on for at most 30 seconds, then
  reported as locked rather than retried forever.
"""

from __future__ import annotations

import codecs
import mimetypes
import os
import shutil
import time
from typing import Any, Dict, Optional, Union

from brain.prefrontal_cortex.permission_decision import PermissionDecision
from brain.spinal_cord.kill_switch import KillSwitch, get_kill_switch

from body.fingers import guards
from body.fingers.exceptions import (
    BinaryFileRefused,
    FileLockedError,
    FileTooLarge,
)
from body.fingers.file_backup import backup_before_write
from body.fingers.file_classifier import (
    classify_path,
    get_size_info,
    human_size,
    is_symlink,
    resolve_absolute,
    resolve_symlink,
)
from body.fingers.trash_manager import TrashManager

#: Default read cap.
DEFAULT_MAX_READ = 5_242_880

#: Reads above this size are refused at any cap.
HARD_MAX_READ = 52_428_800

#: Chunk size for streaming reads.
STREAM_CHUNK = 65_536

#: How long to wait for a locked file, in seconds.
LOCK_TIMEOUT = 30.0

#: Seconds between lock polls.
LOCK_POLL = 0.25

Content = Union[str, bytes]


class FileManager:
    """File operations. Every method requires a decision."""

    def __init__(
        self,
        trash_manager: Optional[TrashManager] = None,
        logger: Optional[Any] = None,
        kill_switch: Optional[KillSwitch] = None,
    ) -> None:
        """Wire the file manager to its collaborators.

        Args:
            trash_manager: The trash ``delete`` moves into.
            logger: Activity logger; None means the shared one.
            kill_switch: The switch re-checked on every call;
                None means the process-wide one. It is handed to the
                trash manager too, so a delete reaches the same stop
                as every other operation.
        """
        self.kill_switch = kill_switch if kill_switch is not None else get_kill_switch()
        self.trash_manager = trash_manager or TrashManager()
        if getattr(self.trash_manager, "kill_switch", None) is None:
            self.trash_manager.kill_switch = self.kill_switch
        self.logger = logger

    # -- read -----------------------------------------------------------

    def read(
        self,
        decision: PermissionDecision,
        path: str,
        max_size: int = DEFAULT_MAX_READ,
        binary: bool = False,
        lock_timeout: float = LOCK_TIMEOUT,
    ) -> Union[str, bytes]:
        """Read a text file, or raw bytes when ``binary`` is set.

        Args:
            decision: An allowed ``read_files`` decision.
            path: The file to read.
            max_size: Read cap; may not exceed the hard maximum.
            binary: Return undecoded bytes instead of text.
            lock_timeout: Seconds to wait for a locked file.

        Returns:
            The file's text, or its bytes when ``binary`` is set.

        Raises:
            FileTooLarge: The file exceeds the cap or the hard
                maximum.
            BinaryFileRefused: The content is not text.
            FileLockedError: The file stayed locked too long.
        """
        resolved, _ = resolve_absolute(path)
        guards.verify_decision(
            decision, guards.category_for("read"), resolved, self.kill_switch
        )
        real = guards.resolve_target(path, self.logger)
        guards.guard_read(real)
        self._check_size(real, max_size)
        with _open_locked(real, "rb", lock_timeout) as handle:
            chunk = handle.read(max_size)
        if binary:
            self._log("read_file", path=real, bytes=len(chunk))
            return chunk
        text = _decode(chunk, real)
        self._log("read_file", path=real, bytes=len(chunk))
        return text

    # -- write ----------------------------------------------------------

    def write(
        self,
        decision: PermissionDecision,
        path: str,
        content: Content,
        mode: str = "w",
        lock_timeout: float = LOCK_TIMEOUT,
    ) -> Dict[str, Any]:
        """Write a file, snapshotting the old version first.

        Args:
            decision: An allowed ``write_files`` decision.
            path: The file to write.
            content: Text (encoded UTF-8) or bytes.
            mode: ``"w"`` to overwrite, ``"a"`` to append.
            lock_timeout: Seconds to wait for a locked file.

        Returns:
            The path, bytes written, mode, and the backup path
            when one was made.

        Raises:
            ValueError: For a mode other than ``w`` or ``a``.
        """
        if mode not in ("w", "a"):
            raise ValueError(f"mode must be 'w' or 'a', not {mode!r}")
        resolved, _ = resolve_absolute(path)
        guards.verify_decision(
            decision, guards.category_for("write"), resolved, self.kill_switch
        )
        real = guards.resolve_target(path, self.logger)
        guards.guard_write(real)
        backup = (
            backup_before_write(decision, real)
            if mode == "w" and os.path.isfile(real)
            else None
        )
        payload = content.encode("utf-8") if isinstance(content, str) else content
        with _open_locked(real, "wb" if mode == "w" else "ab", lock_timeout) as handle:
            handle.write(payload)
        self._log(
            "write_file",
            path=real,
            bytes=len(payload),
            mode=mode,
            backup=backup,
        )
        return {
            "path": real,
            "bytes": len(payload),
            "mode": mode,
            "backup": backup,
        }

    # -- copy / move / rename --------------------------------------------

    def copy(
        self, decision: PermissionDecision, src: str, dst: str
    ) -> Dict[str, Any]:
        """Copy a file, guarding both ends.

        Args:
            decision: An allowed ``write_files`` decision for ``src``.
            src: The file to copy.
            dst: Where to copy it; guarded as a write.

        Returns:
            The source, destination and size.
        """
        resolved, _ = resolve_absolute(src)
        guards.verify_decision(
            decision, guards.category_for("copy"), resolved, self.kill_switch
        )
        source = guards.resolve_target(src, self.logger)
        guards.guard_read(source)
        destination = guards.resolve_target(dst, self.logger)
        guards.guard_write(destination)
        shutil.copy2(source, destination)
        info = get_size_info(destination)
        self._log("copy_file", src=source, dst=destination, bytes=info["bytes"])
        return {"src": source, "dst": destination, "bytes": info["bytes"]}

    def move(
        self, decision: PermissionDecision, src: str, dst: str
    ) -> Dict[str, Any]:
        """Move a file, guarding both ends.

        Args:
            decision: An allowed ``write_files`` decision for ``src``.
            src: The file to move.
            dst: Where to move it; guarded as a write.

        Returns:
            The source, destination and size.
        """
        resolved, _ = resolve_absolute(src)
        guards.verify_decision(
            decision, guards.category_for("move"), resolved, self.kill_switch
        )
        source = guards.resolve_target(src, self.logger)
        guards.guard_read(source)
        destination = guards.resolve_target(dst, self.logger)
        guards.guard_write(destination)
        shutil.move(source, destination)
        info = get_size_info(destination)
        self._log("move_file", src=source, dst=destination, bytes=info["bytes"])
        return {"src": source, "dst": destination, "bytes": info["bytes"]}

    def rename(
        self, decision: PermissionDecision, path: str, new_name: str
    ) -> Dict[str, Any]:
        """Rename a file within its directory.

        Args:
            decision: An allowed ``write_files`` decision for ``path``.
            path: The file to rename.
            new_name: The new name — a name, not a path.

        Returns:
            The old and new paths.

        Raises:
            ValueError: When ``new_name`` is not a plain name.
        """
        if not new_name or new_name in (".", "..") or os.sep in new_name or "/" in new_name:
            raise ValueError(f"new_name must be a plain file name, not {new_name!r}")
        resolved, _ = resolve_absolute(path)
        guards.verify_decision(
            decision, guards.category_for("rename"), resolved, self.kill_switch
        )
        real = guards.resolve_target(path, self.logger)
        guards.guard_write(real)
        renamed = os.path.join(os.path.dirname(real), new_name)
        guards.guard_write(renamed)
        shutil.move(real, renamed)
        self._log("rename_file", src=real, dst=renamed)
        return {"src": real, "dst": renamed}

    # -- delete ---------------------------------------------------------

    def delete(
        self, decision: PermissionDecision, path: str
    ) -> Dict[str, Any]:
        """Delete a file — always by moving it to the trash.

        Args:
            decision: An allowed ``delete_files`` decision.
            path: The file or directory to trash.

        Returns:
            The path and the ``trash_id`` it can be restored by.
        """
        resolved, _ = resolve_absolute(path)
        guards.verify_decision(
            decision, guards.category_for("delete"), resolved, self.kill_switch
        )
        trash_id = self.trash_manager.trash(decision, path)
        return {"path": resolved, "trash_id": trash_id}

    # -- stat -----------------------------------------------------------

    def stat(self, decision: PermissionDecision, path: str) -> Dict[str, Any]:
        """Return a file's metadata.

        Args:
            decision: An allowed ``read_files`` decision.
            path: The file to inspect.

        Returns:
            Size, timestamps, type, permissions, symlink and
            classification information. ``owner`` is ``None``
            until the Windows SID resolver is wired (it needs
            pywin32, which is not a dependency yet).
        """
        resolved, _ = resolve_absolute(path)
        guards.verify_decision(
            decision, guards.category_for("stat"), resolved, self.kill_switch
        )
        real = guards.resolve_target(path, self.logger)
        guards.guard_read(real)
        info = os.stat(real)
        target = resolve_symlink(real) if is_symlink(real) else None
        return {
            "path": real,
            "size": get_size_info(real),
            "modified": _iso(info.st_mtime),
            "created": _iso(info.st_ctime),
            "type": {
                "extension": os.path.splitext(real)[1],
                "mime": mimetypes.guess_type(real)[0] or "application/octet-stream",
            },
            "owner": None,
            "permissions": {
                "read": os.access(real, os.R_OK),
                "write": os.access(real, os.W_OK),
                "execute": os.access(real, os.X_OK),
            },
            "is_symlink": is_symlink(real),
            "symlink_target": target,
            "classification": classify_path(real),
        }

    # -- internals ------------------------------------------------------

    def _check_size(self, path: str, max_size: int) -> None:
        """Refuse files above the cap or the hard maximum."""
        size = _file_size(path)
        if size > HARD_MAX_READ:
            raise FileTooLarge(
                f"{path} is {human_size(size)}, above the {human_size(HARD_MAX_READ)} "
                f"hard maximum; use stat() instead"
            )
        if size > max_size:
            raise FileTooLarge(
                f"{path} is {human_size(size)}, above the "
                f"{human_size(max_size)} read cap; raise max_size "
                f"(up to {human_size(HARD_MAX_READ)}) or use stat()"
            )

    def _log(self, action: str, **fields: Any) -> None:
        """Record an action in the activity log, when wired."""
        if self.logger is not None:
            self.logger.log("body", action, **fields)


def _file_size(path: str) -> int:
    """Return a file's size, or 0 when it cannot be stated.

    Module-level so tests can substitute a size without
    creating multi-gigabyte files.
    """
    try:
        return os.stat(path).st_size
    except OSError:
        return 0


def _open_locked(path: str, mode: str, timeout: float):
    """Open a file, waiting out a sharing violation.

    Args:
        path: The file to open.
        mode: The open mode.
        timeout: Seconds to wait before giving up.

    Returns:
        The open file handle.

    Raises:
        FileLockedError: When the file is still locked after
            ``timeout`` seconds.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            return open(path, mode)
        except PermissionError:
            if time.monotonic() >= deadline:
                raise FileLockedError(
                    f"{path} is locked by another process; "
                    f"close the other app and retry"
                ) from None
            time.sleep(min(LOCK_POLL, max(0.0, deadline - time.monotonic())))


def _decode(chunk: bytes, path: str) -> str:
    """Decode a chunk as text, refusing binary content.

    The chain is UTF-8, then UTF-8 with a byte-order mark, then
    latin-1. Plain UTF-8 also decodes a BOM'd file — successfully,
    leaving a ``\\ufeff`` at the front of the text — so the mark is
    stripped explicitly rather than relying on the ``utf-8-sig``
    codec, which would only ever run as the fallback.

    Args:
        chunk: The raw bytes.
        path: The file they came from, for the error message.

    Returns:
        The decoded text, without a leading BOM.

    Raises:
        BinaryFileRefused: When no encoding yields clean text.
    """
    if chunk.startswith(codecs.BOM_UTF8):
        chunk = chunk[len(codecs.BOM_UTF8):]
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            text = chunk.decode(encoding)
        except UnicodeDecodeError:
            continue
        if any(ord(char) < 32 and char not in "\t\r\n" for char in text):
            raise BinaryFileRefused(
                f"{path} is not text; first 100 bytes: "
                f"{chunk[:100].hex()}. Use stat() or read with binary=True."
            )
        return text
    raise BinaryFileRefused(
        f"{path} could not be decoded; first 100 bytes: {chunk[:100].hex()}"
    )


def _iso(stamp: float) -> str:
    """Render a stat timestamp as an ISO string."""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat()
