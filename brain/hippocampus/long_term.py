"""Hippocampus — long-term memory.

What ZUZZ keeps between runs, as JSONL under ``storage/memory/``. One file per
kind, because the kinds have different lifetimes and a user clearing "what we
learned from my mistakes" should not also clear their chat history.

**JSONL, not JSON.** Append-only is the whole access pattern: remembering is one
line appended, and forgetting is a rewrite that only ever removes lines. An
array-in-one-file store would mean reading and re-serialising every memory to
add one, which turns a cheap operation into an O(n) one and makes a crash during
the write lose the lot.

**Forgetting is a rewrite, done atomically.** Every ``forget_before`` writes a
fresh file beside the old one and renames it into place. Truncating in place
would lose the store to any interruption, and forgetting is the one operation a
user reaches for precisely when they are already upset about something.

Malformed lines are skipped rather than raising. A store is written by several
versions of this code over a machine's lifetime, and one unrecognised line must
not make every other memory unreadable — that failure mode turns a schema change
into total data loss.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from brain.hippocampus.exceptions import MemoryStoreUnavailable
from brain.hippocampus.memory_types import (
    CONVERSATION,
    FACT,
    MISTAKE,
    PREFERENCE,
    TASK,
    Memory,
    MemoryKind,
    from_dict,
)
from core.config import ROOT

__all__ = ["DEFAULT_DIR", "LongTermMemory", "filename_for"]

#: Where the stores live. Under ``storage/memory`` rather than ``storage/logs``
#: because these are records rather than a log: nothing here is append-only,
#: because forgetting has to be able to remove a line.
DEFAULT_DIR = ROOT / "storage" / "memory"

#: Kind to filename. A separate name per kind rather than ``<kind>.jsonl`` so a
#: rename in the type vocabulary does not silently orphan a store full of
#: memories nobody can reach.
_FILES: Dict[str, str] = {
    CONVERSATION: "conversations.jsonl",
    TASK: "learned_tasks.jsonl",
    PREFERENCE: "preferences.jsonl",
    MISTAKE: "mistakes.jsonl",
    FACT: "facts.jsonl",
}


def filename_for(kind: MemoryKind) -> str:
    """Return the file that holds one kind of memory.

    Args:
        kind: The kind of memory.

    Returns:
        The filename. An unknown kind gets the conversation file rather than
        raising, because this is called on the read path for a stored record
        whose kind may predate the current vocabulary — losing the transcript to
        a rename would be worse than filing it somewhere readable.
    """
    return _FILES.get(str(kind), _FILES[CONVERSATION])


class LongTermMemory:
    """The durable memory stores.

    Attributes:
        directory: Where the five JSONL files live.
    """

    def __init__(self, directory: Optional[Path] = None) -> None:
        """Create the store, making sure its directory exists.

        Args:
            directory: Where to keep the files. Defaults to
                ``storage/memory``.

        Raises:
            MemoryStoreUnavailable: If the directory cannot be created. Raised
                here rather than on first write so that a misconfigured path is
                reported at startup, where it can be read, instead of at the
                moment the user asks ZUZZ to remember something.
        """
        self.directory = Path(directory) if directory else DEFAULT_DIR
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise MemoryStoreUnavailable(
                f"cannot use the memory store at {self.directory}: {exc}", str(self.directory)
            ) from exc
        self._lock = threading.Lock()

    # -- writing --------------------------------------------------------

    def store(self, memory: Memory) -> str:
        """Append one memory to its store and return its id.

        Args:
            memory: The memory to keep.

        Returns:
            The id, so a caller can forget exactly this memory later.

        Raises:
            MemoryStoreUnavailable: If the file could not be appended to.
        """
        path = self.path_for(memory.kind)
        line = json.dumps(memory.to_dict(), ensure_ascii=False, default=str)
        with self._lock:
            try:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError as exc:
                raise MemoryStoreUnavailable(
                    f"cannot remember this: {exc}", str(path)
                ) from exc
        return memory.id

    def store_many(self, memories: Iterable[Memory]) -> List[str]:
        """Append several memories, oldest first.

        Args:
            memories: The memories to keep.

        Returns:
            Their ids, in the order given.

        Raises:
            MemoryStoreUnavailable: If a file could not be appended to. Whatever
                was written before the failure stays written, and the exception
                reports it, because a partially-promoted batch is recoverable by
                id and silently dropping the remainder is not.
        """
        return [self.store(memory) for memory in memories]

    # -- reading --------------------------------------------------------

    def path_for(self, kind: MemoryKind) -> Path:
        """Return the file holding one kind of memory."""
        return self.directory / filename_for(kind)

    def read(
        self, kind: Optional[MemoryKind] = None, limit: Optional[int] = None
    ) -> List[Memory]:
        """Read stored memories, oldest first.

        Args:
            kind: Restrict to one kind, or None for every kind.
            limit: Return only the most recent N. The files are read whole either
                way — this bounds what is returned, not what is read, and saying
                otherwise would invite a caller to believe it is cheap.

        Returns:
            The memories, oldest first, with duplicate ids collapsed. A rewrite
            interrupted between the temp file and the rename can leave a doubled
            line, and the same id appearing twice in a recall list reads as two
            separate memories when it is one.
        """
        kinds = [str(kind)] if kind is not None else list(_FILES)
        seen: set = set()
        memories: List[Memory] = []
        for name in kinds:
            for memory in self._read_file(self.path_for(name)):
                if memory.id in seen:
                    continue
                seen.add(memory.id)
                memories.append(memory)
        memories.sort(key=lambda memory: memory.created)
        if limit is not None and limit >= 0:
            return memories[-int(limit) :] if limit else []
        return memories

    def count(self, kind: Optional[MemoryKind] = None) -> int:
        """Return how many memories are stored.

        Args:
            kind: Restrict to one kind, or None for the total.

        Returns:
            The count of well-formed records. A malformed line is not counted,
            because reporting it as a memory would make the count a lie that
            ``GET /api/memory/history`` then contradicts.
        """
        if kind is not None:
            return len(self._read_file(self.path_for(str(kind))))
        return sum(len(self._read_file(self.path_for(name))) for name in _FILES)

    # -- forgetting -----------------------------------------------------

    def forget_before(self, cutoff: float) -> int:
        """Remove every memory older than a timestamp.

        Args:
            cutoff: Unix timestamp, seconds. Memories at or after it are kept.

        Returns:
            How many were removed, across every kind.

        Raises:
            MemoryStoreUnavailable: If a file could not be rewritten. The store
                is left as it was rather than half-forgotten.
        """
        return self._rewrite(lambda memory: memory.created < float(cutoff))

    def forget_ids(self, memory_ids: Iterable[str]) -> int:
        """Remove specific memories by id.

        Args:
            memory_ids: Ids to remove.

        Returns:
            How many were actually present and removed. An id that is not stored
            counts as zero, not as an error.
        """
        wanted = {str(item) for item in memory_ids}
        return self._rewrite(lambda memory: memory.id in wanted)

    def forget_kind(self, kind: MemoryKind) -> int:
        """Remove every memory of one kind.

        Args:
            kind: The kind to clear.

        Returns:
            How many were removed.
        """
        return self._rewrite(lambda memory: memory.kind == str(kind))

    # -- internals ------------------------------------------------------

    def _read_file(self, path: Path) -> List[Memory]:
        """Parse one JSONL file, skipping anything unreadable."""
        if not path.is_file():
            return []
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise MemoryStoreUnavailable(
                f"cannot read the memory store: {exc}", str(path)
            ) from exc
        memories: List[Memory] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                memories.append(from_dict(record))
        return memories

    def _rewrite(self, should_remove) -> int:
        """Drop every record matching ``should_remove``, atomically per file.

        Args:
            should_remove: Predicate over a :class:`Memory`.

        Returns:
            The total number of records removed across every kind.

        Raises:
            MemoryStoreUnavailable: If a file could not be replaced. Earlier
                files have already been rewritten by then, so the store is left
                partially forgotten rather than untouched; the id list from
                :meth:`forget_ids` is what makes that recoverable.
        """
        removed = 0
        with self._lock:
            for name in _FILES:
                path = self.path_for(name)
                if not path.is_file():
                    continue
                kept: List[str] = []
                dropped = 0
                for memory in self._read_file(path):
                    if should_remove(memory):
                        dropped += 1
                    else:
                        kept.append(json.dumps(memory.to_dict(), ensure_ascii=False, default=str))
                if not dropped:
                    # This file held nothing to remove, so it is not rewritten at
                    # all. Rewriting byte-identical content through a temp file
                    # would churn the disk and risk a rename for no reason.
                    continue
                removed += dropped
                try:
                    os.replace(self._write_temp(path, kept), path)
                except OSError as exc:
                    raise MemoryStoreUnavailable(
                        f"cannot rewrite the memory store: {exc}", str(path)
                    ) from exc
        return removed

    def _write_temp(self, path: Path, lines: List[str]) -> Path:
        """Write ``lines`` to a temp file beside ``path`` and return its path.

        Args:
            path: The file being replaced.
            lines: The JSONL lines to write.

        Returns:
            The temp file's path, ready to be renamed over ``path``.

        Raises:
            OSError: Propagated from the write for the caller to convert.
        """
        temp = path.with_suffix(path.suffix + ".tmp")
        with temp.open("w", encoding="utf-8") as handle:
            for line in lines:
                handle.write(line + "\n")
        return temp

    def stats(self) -> Dict[str, Any]:
        """Return a per-kind tally, for the API and the logs."""
        return {name: len(self._read_file(self.path_for(name))) for name in _FILES}