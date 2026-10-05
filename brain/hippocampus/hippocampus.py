"""Hippocampus — the memory orchestrator.

One object the rest of ZUZZ talks to. It owns the four pieces and decides which
one answers a question:

* :class:`~brain.hippocampus.short_term.ShortTermMemory` — what just happened,
  in RAM, bounded and never written.
* :class:`~brain.hippocampus.long_term.LongTermMemory` — what is kept, on disk.
* :class:`~brain.hippocampus.semantic_search.SemanticIndex` — how a question is
  matched to what is kept.
* :class:`~brain.hippocampus.consolidation.Consolidator` — what graduates from
  the first to the second.

**Remembering never writes to disk.** It scores, holds in short-term memory, and
returns an id. That is the single most important property here, and it is a
design choice rather than an omission: a memory that lands on disk the instant it
is spoken can never be revised, and a user who says "I use vim" and then says
"actually, neovim" would be permanently contradicted by their own record.
Consolidation is the separate, deliberate step that makes a memory durable.

**Recall reads both tiers.** A question is answered from short-term memory as
well as the store, because the thing a user just said is usually the most
relevant thing and it is the one least likely to be on disk yet.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from brain.hippocampus.consolidation import Consolidator, score_importance
from brain.hippocampus.exceptions import MemoryRefused
from brain.hippocampus.long_term import LongTermMemory
from brain.hippocampus.memory_types import (
    CONVERSATION,
    FACT,
    MISTAKE,
    NAME_KEYS,
    PREFERENCE,
    TASK,
    Memory,
    MemoryKind,
    UserProfile,
    coerce_kind,
)
from brain.hippocampus.semantic_search import SemanticIndex
from brain.hippocampus.short_term import DEFAULT_CAPACITY, ShortTermMemory

__all__ = ["MAX_CONTEXT_CHARS", "Hippocampus"]

#: How much of a user or assistant message is kept. A pasted file is not a
#: conversation, and truncating at the boundary keeps a recall list readable.
MAX_CONTEXT_CHARS = 2000

#: How many tags the profile counts when guessing what the user works on.
MAX_TOPICS = 8


class Hippocampus:
    """ZUZZ's memory.

    Attributes:
        short_term: What is held right now.
        long_term: What is kept between runs.
        consolidator: What decides what graduates.
        capacity: The short-term ceiling.
    """

    def __init__(
        self,
        directory: Optional[Path] = None,
        capacity: int = DEFAULT_CAPACITY,
        threshold: float = 0.5,
        index_cache_seconds: float = 30.0,
    ) -> None:
        """Wire up the memory tiers.

        Args:
            directory: Where the durable stores live. Defaults to
                ``storage/memory``. Injectable so a test never reads the
                developer's real memory and a second instance proves
                persistence rather than sharing a cache.
            capacity: Short-term ceiling.
            threshold: Importance a memory must reach to be consolidated.
            index_cache_seconds: How long a built search index is reused. A
                rebuild reads every file, and a chat page asking for a profile
                every few seconds would otherwise re-read five JSONL files each
                time. Kept short so a freshly remembered memory becomes findable
                quickly.
        """
        self.short_term = ShortTermMemory(capacity=capacity)
        self.long_term = LongTermMemory(directory)
        self.consolidator = Consolidator(self.short_term, self.long_term, threshold)
        self.capacity = self.short_term.capacity
        self._index_cache_seconds = float(index_cache_seconds)
        self._index: Optional[SemanticIndex] = None
        self._index_built_at = 0.0
        self._index_size = 0
        self._lock = threading.Lock()

    # -----------------------------------------------------------------
    # Remembering
    # -----------------------------------------------------------------

    def remember_conversation(
        self,
        user_msg: str,
        assistant_msg: str = "",
        context: str = "",
    ) -> str:
        """Remember one exchange.

        Held in short-term memory only. A transcript is the least durable of the
        five kinds and the one the chat log already holds for unrelated reasons,
        so this scores low by default and is promoted only if something about it
        turns out to matter.

        Args:
            user_msg: What the user said.
            assistant_msg: What ZUZZ replied.
            context: Where it happened, e.g. ``"chat"``.

        Returns:
            The memory id.
        """
        user = _clip(user_msg)
        answer = _clip(assistant_msg)
        summary = _one_line(user, limit=90)
        if not summary:
            summary = _one_line(answer, limit=90) or "empty exchange"
        body = "\n".join(part for part in (f"user: {user}", f"zuzz: {answer}") if part)
        return self._remember(
            Memory(
                kind=CONVERSATION,
                summary=summary,
                text=body,
                context=str(context or "chat"),
                importance=0.2,
                tags=["chat"],
            )
        )

    def remember_task(self, task: str, solution: str = "", outcome: str = "") -> str:
        """Remember how something was done.

        Args:
            task: What was attempted.
            solution: How it was solved — the part worth having next time.
            outcome: What happened, e.g. ``"worked"`` or ``"timed out"``.

        Returns:
            The memory id.
        """
        headline = _clip(task)
        body = "\n".join(
            part
            for part in (
                f"task: {_clip(task)}",
                f"solution: {_clip(solution)}" if solution else "",
                f"outcome: {_clip(outcome)}" if outcome else "",
            )
            if part
        )
        # Scored high because it is the one kind that is useless without the
        # solution in it, and valuable with it: "install the driver" alone is
        # worse than no memory at all.
        importance = 0.8 if solution else 0.4
        return self._remember(
            Memory(
                kind=TASK,
                summary=_one_line(headline, limit=90) or "task",
                text=body,
                value=_clip(outcome),
                context="task",
                importance=importance,
                tags=["task"],
            )
        )

    def remember_preference(
        self, key: str, value: str, context: str = ""
    ) -> str:
        """Remember a standing preference.

        Args:
            key: What the preference is about, e.g. ``"editor"``.
            value: The preference itself, e.g. ``"vscode"``.
            context: Where it was stated.

        Returns:
            The memory id.

        Raises:
            MemoryRefused: When the key or the value is blank. A preference with
                no subject cannot be recalled by subject, and one with no value
                is not a preference.
        """
        subject = _clip(key, limit=80)
        choice = _clip(value, limit=400)
        if not subject or not choice:
            raise MemoryRefused(
                "a preference needs both a subject and a value, "
                "for example key='editor' value='vscode'"
            )
        normalised = subject.lower().replace(" ", "_")
        # A stated preference outranks anything computed: the user naming
        # something they like is the strongest signal available, and this is why
        # a preference survives consolidation while small talk does not.
        importance = 0.95 if normalised in NAME_KEYS else 0.85
        return self._remember(
            Memory(
                kind=PREFERENCE,
                summary=f"{subject}: {choice}"[:160],
                text=f"the user's {subject} is {choice}",
                key=normalised,
                value=choice,
                context=str(context or "conversation"),
                importance=importance,
                tags=["preference", normalised],
            )
        )

    def remember_mistake(self, action: str, error: str = "", correction: str = "") -> str:
        """Remember a mistake and, ideally, what to do instead.

        Args:
            action: What ZUZZ did.
            error: What went wrong.
            correction: What to do differently. The most valuable half — a
                remembered error with no correction is a warning, not a lesson.

        Returns:
            The memory id.
        """
        did = _clip(action)
        went_wrong = _clip(error)
        instead = _clip(correction)
        body = "\n".join(
            part
            for part in (
                f"action: {did}",
                f"error: {went_wrong}" if went_wrong else "",
                f"correction: {instead}" if instead else "",
            )
            if part
        )
        importance = 0.9 if instead else 0.6
        return self._remember(
            Memory(
                kind=MISTAKE,
                summary=_one_line(did, limit=90) or "mistake",
                text=body,
                value=instead,
                context="lesson",
                importance=importance,
                tags=["mistake"],
            )
        )

    def remember_fact(self, subject: str, detail: str = "", context: str = "") -> str:
        """Remember a fact about the user or the work.

        Args:
            subject: What the fact is about.
            detail: The fact itself.
            context: Where it came from.

        Returns:
            The memory id.
        """
        about = _clip(subject)
        body = _clip(detail) or about
        return self._remember(
            Memory(
                kind=FACT,
                summary=_one_line(about, limit=90) or "fact",
                text=body,
                key=_one_line(about, limit=60).lower().replace(" ", "_"),
                value=_clip(detail, limit=400),
                context=str(context or "conversation"),
                importance=0.7,
                tags=["fact"],
            )
        )

    def remember(self, memory: Memory) -> str:
        """Hold an already-built memory.

        Args:
            memory: The memory to hold.

        Returns:
            Its id.
        """
        return self._remember(memory)

    # -----------------------------------------------------------------
    # Recalling
    # -----------------------------------------------------------------

    def recall_similar(
        self,
        query: str,
        type: Optional[MemoryKind] = None,
        limit: int = 10,
    ) -> List[Memory]:
        """Find the memories most relevant to a question.

        Searches short-term memory and the durable store together and returns
        them ranked. Short-term is included deliberately: the thing a user just
        said is usually the most relevant thing and is by definition the one
        least likely to be on disk yet.

        Args:
            query: What to find.
            type: Restrict to one kind, or None for all of them.
            limit: Maximum results.

        Returns:
            Memories, most relevant first.

        Raises:
            MemoryRefused: When ``type`` is not one of the five kinds, or the
                query carries no searchable word.
        """
        wanted = coerce_kind(type) if type is not None else None
        candidates = self._rank(query, wanted, int(limit))
        # Collapse an id held in both tiers — consolidation discards from
        # short-term after writing, but a memory can be re-remembered from the
        # same text, and showing the user the same fact twice reads as two.
        seen: set = set()
        results: List[Memory] = []
        for memory in candidates:
            if memory.id in seen:
                continue
            seen.add(memory.id)
            results.append(memory)
            if len(results) >= int(limit):
                break
        return results

    def recall_history(self, limit: int = 20, type: Optional[MemoryKind] = None) -> List[Memory]:
        """Return memories newest first, without ranking them.

        For a chronological view — the History panel — where relevance is the
        wrong ordering and putting the newest conversation at the bottom would be
        useless.

        Args:
            limit: Maximum results.
            type: Restrict to one kind, or None for all of them.

        Returns:
            Memories, newest first.

        Raises:
            MemoryRefused: When ``type`` is not a kind ZUZZ keeps.
        """
        wanted = coerce_kind(type) if type is not None else None
        everything = self.short_term.all() + self.long_term.read(wanted)
        everything.sort(key=lambda memory: memory.created, reverse=True)
        return everything[: max(0, int(limit))]

    def get_user_profile(self) -> UserProfile:
        """Return what ZUZZ has worked out about the user.

        Rebuilt from scratch on every call rather than cached. A cached profile
        goes stale the moment a preference changes, and a stale profile is worse
        than none: ZUZZ would introduce the user by the name they had last asked
        to stop using.

        Returns:
            The profile, with preferences newest-wins.
        """
        everything = self.short_term.all() + self.long_term.read()
        everything.sort(key=lambda memory: memory.created)
        profile = UserProfile(total=len(everything))
        topics: Dict[str, int] = {}
        for memory in everything:
            if memory.kind == CONVERSATION:
                profile.conversations += 1
            elif memory.kind == TASK:
                profile.tasks += 1
            elif memory.kind == PREFERENCE:
                profile.preferences[memory.key] = memory.value
            elif memory.kind == MISTAKE:
                profile.mistakes += 1
            elif memory.kind == FACT:
                profile.facts += 1
            for tag in memory.tags:
                topics[tag] = topics.get(tag, 0) + 1
            if memory.created:
                profile.oldest = profile.oldest or memory.created
                profile.newest = max(profile.newest, memory.created)
        for name_key in NAME_KEYS:
            if name_key in profile.preferences and profile.preferences[name_key]:
                profile.name = profile.preferences[name_key]
                break
        ranked = sorted(topics.items(), key=lambda pair: (-pair[1], pair[0]))
        profile.topics = [tag for tag, _ in ranked[:MAX_TOPICS] if tag not in ("chat",)]
        return profile

    # -----------------------------------------------------------------
    # Forgetting
    # -----------------------------------------------------------------

    def forget_before(self, date: Any) -> int:
        """Forget everything older than a date.

        Args:
            date: Anything date-like — an ISO string, a ``datetime``, or a unix
                timestamp. A bare string is the common case from the API and from
                the model's tool call, and making the caller convert it would put
                a parse failure in front of a user who asked to forget something.

        Returns:
            How many memories were removed, from both tiers.

        Raises:
            MemoryRefused: When the date cannot be read as one.
        """
        cutoff = _as_timestamp(date)
        removed = self.long_term.forget_before(cutoff)
        # Short-term is a bounded buffer, so aging it out is not really
        # forgetting — but leaving it would let a recall keep returning something
        # the user has just asked ZUZZ to forget, which is the visible failure.
        removed += self.short_term.discard(
            [memory.id for memory in self.short_term.all() if memory.created < cutoff]
        )
        self._invalidate()
        return removed

    def forget(self, memory_id: str) -> bool:
        """Forget one memory by id.

        Args:
            memory_id: The id to remove.

        Returns:
            True when it was held and is now gone.
        """
        target = str(memory_id)
        removed = self.short_term.discard([target])
        removed += self.long_term.forget_ids([target])
        if removed:
            self._invalidate()
        return bool(removed)

    def forget_kind(self, kind: MemoryKind) -> int:
        """Forget every memory of one kind.

        Args:
            kind: The kind to clear.

        Returns:
            How many were removed.

        Raises:
            MemoryRefused: When the kind is not one ZUZZ keeps.
        """
        return self.long_term.forget_kind(coerce_kind(kind))

    def consolidate(self, dry_run: bool = False) -> Dict[str, Any]:
        """Promote the held memories worth keeping to disk.

        Args:
            dry_run: Report without writing.

        Returns:
            The promotion report.
        """
        report = self.consolidator.consolidate(dry_run=dry_run)
        self._invalidate()
        return report

    def flush(self) -> Dict[str, Any]:
        """Write every held memory to disk, with no threshold applied.

        The durability counterpart to :meth:`consolidate`. See
        :meth:`~brain.hippocampus.consolidation.Consolidator.flush` for why the two
        are separate: this one exists because the buffer is RAM-only and bounded,
        so anything still held at shutdown would otherwise be lost.

        Args:
            None.

        Returns:
            The promotion report, with ``threshold`` reported as ``0.0``.
        """
        report = self.consolidator.flush()
        self._invalidate()
        return report

    # -----------------------------------------------------------------
    # Reporting
    # -----------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Return a description of every tier, for the API and the dashboard."""
        return {
            "short_term": self.short_term.stats(),
            "long_term": self.long_term.stats(),
            "directory": str(self.long_term.directory),
            "threshold": self.consolidator.threshold,
            "indexed": len(self._index_or_build()),
            "profile": self.get_user_profile().to_dict(),
        }

    def score(self, memory: Memory) -> float:
        """Return how worth keeping a memory is, from 0.0 to 1.0."""
        return score_importance(memory)

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------

    def _remember(self, memory: Memory) -> str:
        """Score a memory, hold it in short-term memory and return its id.

        Args:
            memory: The memory to hold.

        Returns:
            Its id. A *held* id, not a durable one — see the module docstring for
            why the two are deliberately different.
        """
        memory.importance = max(memory.importance, score_importance(memory))
        stored = self.short_term.remember(memory)
        self._invalidate()
        return stored.id

    def _index_source(self) -> List[Memory]:
        """Return the durable memories, which is what the index covers."""
        return self.long_term.read()

    def _rank(
        self, query: str, kind: Optional[str], limit: int
    ) -> List[Memory]:
        """Rank short-term and durable memories together for a query.

        Both tiers go into **one** index and get one ranking, rather than ranking
        each separately and concatenating. Separate rankings have no shared scale
        to compare on, so a single very relevant held memory would lose to
        whatever durable memory happened to score highest — which is backwards,
        since the thing the user just said is usually the one they meant.
        """
        if limit <= 0:
            return []
        index = self._index_or_build()
        held = self.short_term.all()
        if kind is not None:
            held = [memory for memory in held if memory.kind == kind]
        if held:
            index = index.including(held)
        return [memory for memory, _score in index.search(query, limit=limit, kind=kind)]

    def _invalidate(self) -> None:
        """Drop the cached search index after a write."""
        with self._lock:
            self._index = None
            self._index_built_at = 0.0

    def _index_or_build(self) -> SemanticIndex:
        """Return the search index, rebuilding it when it is stale."""
        with self._lock:
            size = self.long_term.count()
            fresh = (
                self._index is not None
                and (time.time() - self._index_built_at) < self._index_cache_seconds
                and self._index_size == size
            )
            if fresh and self._index is not None:
                return self._index
            self._index = SemanticIndex(self._index_source())
            self._index_built_at = time.time()
            self._index_size = size
            return self._index


def _clip(value: Any, limit: int = MAX_CONTEXT_CHARS) -> str:
    """Return text stripped and truncated, never None.

    Args:
        value: Anything.
        limit: Maximum characters to keep.

    Returns:
        The stripped text, truncated with an ellipsis if it was cut.
    """
    text = str(value if value is not None else "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _one_line(text: str, limit: int = 90) -> str:
    """Collapse text to a single scannable line."""
    collapsed = " ".join(str(text or "").split())
    return _clip(collapsed, limit)


def _as_timestamp(date: Any) -> float:
    """Return a unix timestamp for anything date-like.

    Args:
        date: An ISO-8601 string, a ``datetime`` with or without a timezone, a
            ``date``, or a number already in seconds.

    Returns:
        Seconds since the epoch.

    Raises:
        MemoryRefused: When the value is not one of those. A forget that quietly
            resolved an unreadable date to "now" and removed everything would be
            the worst possible failure in this module, so it refuses instead.
    """
    if isinstance(date, (int, float)) and not isinstance(date, bool):
        return float(date)
    if hasattr(date, "timestamp"):
        try:
            return float(date.timestamp())
        except (OSError, ValueError) as exc:
            raise MemoryRefused(f"cannot read {date!r} as a date: {exc}") from exc
    text = str(date or "").strip()
    if not text:
        raise MemoryRefused("a forget needs a date to forget before")
    try:
        return float(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MemoryRefused(
            f"{date!r} is not a date ZUZZ understands; use YYYY-MM-DD, "
            "an ISO timestamp, or days ago as a number"
        ) from exc
    if parsed.tzinfo is None:
        # A bare date means midnight local time. Assuming UTC would shift a
        # "forget everything before 2026-01-01" by one day in the wrong
        # direction, which for the user is exactly the day they wanted to keep.
        #
        # ``astimezone`` on a naive datetime routes through the C runtime's
        # local-time conversion, which raises OSError on Windows for any instant
        # at or before the epoch — so "forget everything before 1970-01-01", the
        # one date a user reaches for when they mean *all of it*, is the date that
        # crashes. Attaching the local zone and calling ``timestamp`` takes the
        # same value through a path that handles pre-epoch instants, and the
        # ``datetime.timestamp`` fallback covers the remaining platforms.
        try:
            parsed = parsed.astimezone()
        except (OSError, OverflowError, ValueError):
            parsed = parsed.replace(tzinfo=_local_timezone())
    try:
        return parsed.timestamp()
    except (OSError, OverflowError, ValueError) as exc:
        raise MemoryRefused(f"cannot read {date!r} as a date: {exc}") from exc


def _local_timezone():
    """Return the machine's local timezone, or UTC when it cannot be read.

    A forget is a destructive operation, so its boundary should not depend on
    whether the platform could name its own timezone. Falling back to UTC can move
    the boundary by at most the local offset, which for a whole-day-granularity
    request is the difference between two adjacent days — worth saying, and far
    better than a refusal that leaves the user unable to clear their own memory.
    """
    try:
        local = datetime.now().astimezone().tzinfo
    except (OSError, OverflowError, ValueError):
        local = None
    return local or timezone.utc


def utc_now() -> str:
    """Return the current time as an ISO-8601 UTC string, for API payloads."""
    return datetime.now(timezone.utc).isoformat()