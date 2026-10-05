"""Hippocampus — short-term memory.

What ZUZZ is holding right now, in RAM. Bounded on purpose: short-term memory
that grows without limit is not better memory, it is a slower one, because
everything in it is re-scored on every recall.

Two properties are deliberate and both are load-bearing:

* **It is not written to disk.** A crash, or the stop button, must not leave a
  half-formed record behind. Consolidation is the only path to long-term storage
  and it promotes deliberately, not as a side effect of remembering.
* **It is capped by count, not by age.** A cap by count bounds the work a recall
  does; a cap by age alone does not, because a busy hour can put a thousand
  memories in an afternoon.

The buffer evicts the least important entry when it is full, and ties are broken
by age so the oldest of equally-important memories goes first.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from brain.hippocampus.memory_types import Memory, MemoryKind

__all__ = ["DEFAULT_CAPACITY", "ShortTermMemory"]

#: How many short-term memories ZUZZ holds. Sized for a working session — a
#: long day of chat produces a few hundred turns, and a capacity in the hundreds
#: means the cap is reached in an afternoon rather than in a minute.
DEFAULT_CAPACITY = 200


class ShortTermMemory:
    """The recent, in-memory memories.

    Attributes:
        capacity: Hard ceiling on how many are held.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        """Create an empty buffer.

        Args:
            capacity: Maximum number of memories to hold. A value below one is
                raised to one, because a buffer that can hold nothing is not a
                buffer — it would silently discard every memory it was given and
                report success.
        """
        self.capacity = max(1, int(capacity))
        self._items: List[Memory] = []
        self._lock = threading.Lock()

    def remember(self, memory: Memory) -> Memory:
        """Add one memory, evicting the weakest if the buffer is full.

        Args:
            memory: The memory to hold.

        Returns:
            The memory as stored. A new object rather than the caller's, so a
            caller mutating its copy afterwards cannot change what is remembered.
        """
        with self._lock:
            kept = Memory(**{**memory.__dict__, "tags": list(memory.tags), "meta": dict(memory.meta)})
            self._items.append(kept)
            self._evict()
            return kept

    def extend(self, memories: List[Memory]) -> List[Memory]:
        """Add several memories at once, oldest first.

        Args:
            memories: The memories to hold.

        Returns:
            The memories as stored, in the order given.
        """
        return [self.remember(memory) for memory in memories]

    def all(self) -> List[Memory]:
        """Return every held memory, oldest first."""
        with self._lock:
            return list(self._items)

    def recent(self, count: int = 10, kind: Optional[MemoryKind] = None) -> List[Memory]:
        """Return the most recent memories, newest first.

        Args:
            count: How many to return.
            kind: Restrict to one kind, or None for all of them.

        Returns:
            Up to ``count`` memories.
        """
        with self._lock:
            items = [
                memory for memory in self._items if kind is None or memory.kind == kind
            ]
        items.sort(key=lambda memory: memory.created, reverse=True)
        return items[: max(0, int(count))]

    def find(self, memory_id: str) -> Optional[Memory]:
        """Return one memory by id, or None when it is not held.

        Args:
            memory_id: The id to look for.

        Returns:
            The memory, or None.
        """
        with self._lock:
            for memory in self._items:
                if memory.id == str(memory_id):
                    return memory
        return None

    def discard(self, memory_ids: List[str]) -> int:
        """Remove specific memories, as consolidation does after promoting them.

        Args:
            memory_ids: Ids to remove.

        Returns:
            How many were actually removed. An id that is not held counts as
            zero rather than as an error: promotion and cleanup are separate
            steps and the second must not fail on the first having already
            succeeded.
        """
        wanted = {str(item) for item in memory_ids}
        with self._lock:
            before = len(self._items)
            self._items = [memory for memory in self._items if memory.id not in wanted]
            return before - len(self._items)

    def promote_candidates(
        self, minimum_importance: float = 0.5, older_than_days: float = 0.0
    ) -> List[Memory]:
        """Return the memories worth writing to long-term storage.

        Args:
            minimum_importance: Memories below this score are not worth keeping.
            older_than_days: Ignore anything younger than this. A default of
                zero promotes everything that scores well, which is the right
                answer at the end of a session and the wrong one mid-conversation
                — a preference stated two minutes ago does not need to be durable
                yet, and promoting it would make the user wait out the box before
                changing it.

        Returns:
            Memories meeting both tests, oldest first.
        """
        candidates = [
            memory
            for memory in self.all()
            if memory.importance >= float(minimum_importance)
            and (time.time() - memory.created) >= max(0.0, float(older_than_days)) * 86400.0
        ]
        candidates.sort(key=lambda memory: memory.created)
        return candidates

    def clear(self) -> int:
        """Forget everything held.

        Returns:
            How many were discarded.
        """
        with self._lock:
            count = len(self._items)
            self._items.clear()
            return count

    def stats(self) -> Dict[str, Any]:
        """Return a short description of the buffer, for the API and the logs."""
        with self._lock:
            items = list(self._items)
        return {
            "held": len(items),
            "capacity": self.capacity,
            "by_kind": _count_by(items, lambda memory: memory.kind),
            "mean_importance": (
                round(sum(m.importance for m in items) / len(items), 4) if items else 0.0
            ),
        }

    def _evict(self) -> None:
        """Drop the weakest memory when over capacity. Caller holds the lock."""
        while len(self._items) > self.capacity:
            weakest = min(
                range(len(self._items)),
                key=lambda index: (self._items[index].importance, self._items[index].created),
            )
            self._items.pop(weakest)


def _count_by(items: List[Memory], key: Callable[[Memory], str]) -> Dict[str, int]:
    """Return a tally of ``items`` by the value ``key`` returns."""
    tally: Dict[str, int] = {}
    for item in items:
        tally[key(item)] = tally.get(key(item), 0) + 1
    return tally