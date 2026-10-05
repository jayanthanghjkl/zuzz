"""Hippocampus â€” memory types.

What ZUZZ remembers, and the shape it remembers it in. One value object
(:class:`Memory`) for all five kinds, because the thing that makes a memory
useful is the same for each of them â€” when it happened, how it was phrased, and
enough text to find it again â€” and five near-identical classes would be five
places for those fields to drift apart.

The kinds are kept apart by :class:`MemoryType` rather than by type because they
live in five separate files on disk. A conversation is a transcript, a task is a
recipe, a preference is a standing instruction, a mistake is a lesson and a fact
is a claim. They have genuinely different lifetimes â€” a preference should
outlive a conversation by months â€” and one undifferentiated store would make
``forget_before`` a blunt instrument where a precise one is available.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List

from brain.hippocampus.exceptions import UnknownMemoryKind

__all__ = [
    "CONVERSATION",
    "FACT",
    "MISTAKE",
    "NAME_KEYS",
    "Memory",
    "MemoryKind",
    "MemoryType",
    "PREFERENCE",
    "TASK",
    "UserProfile",
    "coerce_kind",
    "from_dict",
    "new_id",
]

#: The five kinds of memory ZUZZ keeps. Each maps to one file in
#: ``storage/memory/``, so adding a kind here means adding a file and a row.
CONVERSATION = "conversation"
TASK = "task"
PREFERENCE = "preference"
MISTAKE = "mistake"
FACT = "fact"

#: Every kind, in the order the profile counts them.
_MEMORY_KINDS = (CONVERSATION, TASK, PREFERENCE, MISTAKE, FACT)

#: The vocabulary of kinds, as a tuple. Useful for iteration and for validating a
#: caller's claim that something "is a memory type".
MemoryType = _MEMORY_KINDS

#: The type of a single kind â€” a plain string, so a kind read back from JSONL is
#: the same value as one written in code and neither needs unwrapping.
MemoryKind = str


def new_id() -> str:
    """Return a fresh memory id.

    A UUID4 rather than a counter or a timestamp. A counter would make the ids
    guessable and, worse, would collide with any other writer; a timestamp alone
    collides for two memories written in the same second, which is exactly what
    happens when ZUZZ records a task and its outcome together.

    Returns:
        A hyphenated UUID string.
    """
    return uuid.uuid4().hex


def coerce_kind(value: Any) -> str:
    """Return a memory kind, refusing one ZUZZ does not keep.

    Args:
        value: The kind named by a caller, a tool or a stored record.

    Returns:
        A valid :data:`MemoryKind`.

    Raises:
        UnknownMemoryKind: When the kind is not one of the five. Falling back to
            ``conversation`` would file a preference under a transcript, and the
            mistake would not surface until someone tried to read it back.

            This is the region's own refusal type rather than a bare
            :class:`ValueError`, which is what it used to raise. A caller that
            catches :class:`~brain.hippocampus.exceptions.MemoryRefused` — the
            documented way to handle any Hippocampus refusal, and what the API
            routes do — was silently missing this one, because ``ValueError`` is
            not a ``MemoryRefused``. An exported exception that is never raised
            is worse than a missing one: it reads as coverage.
    """
    text = str(value).strip().lower()
    if text not in _MEMORY_KINDS:
        raise UnknownMemoryKind(
            f"unknown memory kind {value!r}; ZUZZ keeps: {', '.join(_MEMORY_KINDS)}"
        )
    return text


@dataclass
class Memory:
    """One thing ZUZZ remembers.

    Attributes:
        id: Stable identifier, used to forget exactly this memory.
        kind: One of the five kinds.
        summary: One line a human can scan. This is what a recall returns before
            the full text, and it is deliberately short: the recall list is
            shown in a panel, not read.
        text: The full content â€” the searchable part.
        key: The subject of the memory, for kinds that have one. A preference has
            a key ("editor") and a value ("vscode"); a conversation has none.
        value: The answer, for kinds that have one.
        context: Where this came from, e.g. the route or tool that wrote it.
        importance: 0.0 to 1.0. Consolidation promotes on this, so it is what
            decides what is worth keeping and what ages out.
        created: Unix timestamp, seconds.
        tags: Free-form labels, used to narrow a recall.
        meta: Anything else worth keeping that has no column of its own.
    """

    kind: str
    summary: str
    text: str = ""
    key: str = ""
    value: str = ""
    context: str = ""
    importance: float = 0.5
    created: float = field(default_factory=time.time)
    id: str = field(default_factory=new_id)
    tags: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def age_days(self) -> float:
        """Return how many days ago this was remembered.

        Clamped at zero rather than allowed to go negative on a machine whose
        clock was set backwards: a negative age would sort as the *newest*
        memory and, on consolidation, would look like the most important.
        """
        return max(0.0, (time.time() - self.created) / 86400.0)

    def searchable(self) -> str:
        """Return the text used to match this memory against a query.

        Includes the key and value, not just the body. A preference stored as
        ``key="editor", value="vscode"`` has an almost empty body, so searching
        the body alone would never find it by the thing a user would actually
        type.
        """
        parts = [self.text, self.summary, self.key, self.value, " ".join(self.tags)]
        return " ".join(part for part in parts if part)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form for the JSONL store and the API."""
        return {
            "id": self.id,
            "kind": self.kind,
            "summary": self.summary,
            "text": self.text,
            "key": self.key,
            "value": self.value,
            "context": self.context,
            "importance": round(float(self.importance), 4),
            "created": float(self.created),
            "tags": list(self.tags),
            "meta": dict(self.meta),
        }

    def brief(self) -> Dict[str, Any]:
        """Return the short form a recall list shows.

        Omits the body on purpose. ``recall_similar`` returns these, and a
        recall list carrying every stored transcript would be a page of text
        rather than a list of things.
        """
        return {
            "id": self.id,
            "kind": self.kind,
            "summary": self.summary,
            "key": self.key,
            "value": self.value,
            "importance": round(float(self.importance), 4),
            "created": float(self.created),
            "age_days": round(self.age_days, 3),
            "tags": list(self.tags),
        }


def from_dict(record: Dict[str, Any]) -> Memory:
    """Rebuild a memory from a stored record.

    Tolerates a missing ``created`` and an unknown ``kind`` rather than raising.
    A store is written by an older version of this code more often than anyone
    expects, and a single unrecognised line must not make every other memory
    unreadable â€” that failure mode turns a schema change into total data loss.

    Args:
        record: One parsed JSONL line.

    Returns:
        The memory, with defaults filled in for anything missing.
    """
    kind = str(record.get("kind", CONVERSATION)).lower()
    if kind not in _MEMORY_KINDS:
        kind = CONVERSATION
    return Memory(
        kind=kind,
        summary=str(record.get("summary", "")),
        text=str(record.get("text", "")),
        key=str(record.get("key", "")),
        value=str(record.get("value", "")),
        context=str(record.get("context", "")),
        importance=float(record.get("importance", 0.5) or 0.0),
        created=float(record.get("created", 0.0) or 0.0),
        id=str(record.get("id") or new_id()),
        tags=[str(tag) for tag in record.get("tags", []) or []],
        meta=dict(record.get("meta", {}) or {}),
    )


@dataclass
class UserProfile:
    """What ZUZZ has worked out about the user.

    Derived rather than stored: it is a reading of the memory store, rebuilt
    from scratch on every call. A cached profile would go stale the moment a
    preference changed, and a stale profile is worse than none â€” ZUZZ would
    introduce the user by the name they had last asked to stop using.

    Attributes:
        name: The user's name, if a memory states one.
        preferences: ``key -> value``, most recently remembered winning.
        facts: Count of stored facts.
        mistakes: Count of stored lessons.
        conversations: Count of stored conversations.
        tasks: Count of stored task solutions.
        total: Count across every kind.
        oldest: Timestamp of the oldest memory, or 0.0 when there are none.
        newest: Timestamp of the newest memory, or 0.0 when there are none.
        topics: The most frequent tags, which is the closest thing to "what is
            this user working on" that memory can honestly answer.
    """

    name: str = ""
    preferences: Dict[str, str] = field(default_factory=dict)
    facts: int = 0
    mistakes: int = 0
    conversations: int = 0
    tasks: int = 0
    total: int = 0
    oldest: float = 0.0
    newest: float = 0.0
    topics: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form for the API."""
        return {
            "name": self.name,
            "preferences": dict(self.preferences),
            "counts": {
                "conversation": self.conversations,
                "task": self.tasks,
                "preference": len(self.preferences),
                "mistake": self.mistakes,
                "fact": self.facts,
                "total": self.total,
            },
            "oldest": self.oldest,
            "newest": self.newest,
            "topics": list(self.topics),
        }


#: Keys a preference may be stored under when it means the user's name. Kept
#: here rather than in the orchestrator so the vocabulary of "who is this" is
#: one list, and a new synonym is a one-line change rather than a grep.
NAME_KEYS = ("name", "user_name", "username", "called", "owner")