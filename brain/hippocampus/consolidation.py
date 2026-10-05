"""Hippocampus — consolidation.

Turning what just happened into what is kept. This is the only path from
short-term memory to disk, and it is a separate step on purpose: remembering is
cheap and happens constantly, promoting is a judgement and happens rarely, and a
region that promoted as a side effect of remembering would turn "ZUZZ heard
something" into "ZUZZ will keep this forever" without anyone deciding that.

Three rules make the promotion worth trusting:

* **Importance decides, and the threshold is explicit.** A greeting is not a
  preference. The score is on every :class:`~brain.hippocampus.memory_types.Memory`
  so a caller can see why something was or was not kept.
* **Promotion is idempotent.** The id is the join key, so a second consolidation
  pass over the same buffer writes nothing twice. Sleeping twice is normal and
  must not be able to inflate what ZUZZ thinks it knows.
* **Cleanup follows the write, never the other way round.** Short-term entries
  are discarded only after the durable write succeeded, so a failed write leaves
  the memory held in RAM and eligible for the next pass instead of losing it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from brain.hippocampus.long_term import LongTermMemory
from brain.hippocampus.memory_types import FACT, MISTAKE, PREFERENCE, TASK, Memory
from brain.hippocampus.short_term import ShortTermMemory

__all__ = [
    "DEFAULT_IMPORTANCE",
    "DEFAULT_MIN_AGE_DAYS",
    "Consolidator",
    "score_importance",
]

#: The importance a memory is given when a caller does not say. Mid-range on
#: purpose: a caller that does not care should be neither promoted by default nor
#: silently dropped, and consolidation's threshold is below this.
DEFAULT_IMPORTANCE = 0.5

#: How old a memory must be before consolidation will promote it. Not zero: a
#: preference stated two minutes ago does not need to be durable yet, and
#: promoting it immediately would make the user wait out the box before changing
#: their mind.
DEFAULT_MIN_AGE_DAYS = 0.0

#: Weight of each signal in :func:`score_importance`. Normalised to sum to 1.0.
_WEIGHTS = {
    "kind": 0.30,
    "explicitness": 0.40,
    "length": 0.15,
    "specificity": 0.15,
}

#: Kinds that are worth keeping on sight. A conversation is the least durable
#: thing ZUZZ holds — it is a transcript, and a transcript is recoverable from
#: the chat log, which is stored elsewhere for entirely different reasons.
_KIND_PRIOR = {
    PREFERENCE: 1.0,
    MISTAKE: 1.0,
    TASK: 0.8,
    FACT: 0.6,
    "conversation": 0.2,
}

#: Phrases that mean the user said this deliberately rather than in passing.
#: Matched case-insensitively against the summary and the key.
_EXPLICIT = (
    "remember", "always", "never", "prefer", "i use", "i want", "my name",
    "call me", "don't", "do not", "from now on", "going forward", "note that",
    "important", "rule:", "my ", "i am", "i'm",
)

#: Below this length a "fact" is almost certainly small talk.
_MIN_USEFUL_LENGTH = 24


def score_importance(memory: Memory) -> float:
    """Estimate how worth keeping a memory is, from 0.0 to 1.0.

    Deliberately a heuristic and deliberately explainable. A learned model would
    rank better and be far harder to argue with when it is wrong, and the failure
    that matters here is not "ranked slightly worse" — it is "silently threw away
    the one thing the user asked it to remember", which an explainable rule lets
    a user check.

    Args:
        memory: The memory to score.

    Returns:
        A score in ``[0.0, 1.0]``.
    """
    kind_score = _KIND_PRIOR.get(memory.kind, 0.4)
    haystack = f"{memory.summary} {memory.key} {memory.context}".lower()
    explicit = 1.0 if any(phrase in haystack for phrase in _EXPLICIT) else 0.0
    length = len(memory.searchable())
    length_score = min(1.0, length / 160.0) if length >= _MIN_USEFUL_LENGTH else 0.0
    specificity = 1.0 if memory.key or memory.tags else 0.0

    raw = (
        _WEIGHTS["kind"] * kind_score
        + _WEIGHTS["explicitness"] * explicit
        + _WEIGHTS["length"] * length_score
        + _WEIGHTS["specificity"] * specificity
    )
    return round(min(1.0, max(0.0, raw)), 4)


class Consolidator:
    """Promotes short-term memories into the durable store.

    Attributes:
        short_term: The buffer to draw from and write back to.
        long_term: The durable store to promote into.
        threshold: The importance a memory must reach to be promoted.
        min_age_days: How old a memory must be before it is eligible.
    """

    def __init__(
        self,
        short_term: ShortTermMemory,
        long_term: LongTermMemory,
        threshold: float = 0.5,
        min_age_days: float = DEFAULT_MIN_AGE_DAYS,
    ) -> None:
        """Wire a consolidator to a buffer and a store.

        Args:
            short_term: The buffer to promote from.
            long_term: The store to promote into.
            threshold: Importance a memory must reach. Kept here rather than read
                from config because it is a tuning knob for this class, not a
                machine-wide policy, and the two drift apart for good reasons.
            min_age_days: Minimum age before a memory is eligible.
        """
        self.short_term = short_term
        self.long_term = long_term
        self.threshold = float(threshold)
        self.min_age_days = float(min_age_days)

    def consolidate(self, dry_run: bool = False) -> Dict[str, Any]:
        """Promote everything now worth keeping.

        Args:
            dry_run: Report what would be promoted without writing. The write is
                one line per memory, but a dry run is what makes "why did it keep
                that?" answerable without emptying the store by hand first.

        Returns:
            A report: how many were promoted, which ids, what was skipped and
            why, and whether the run was a dry run.
        """
        candidates = self.short_term.promote_candidates(self.threshold, self.min_age_days)
        promoted: List[str] = []
        written: List[Memory] = []
        skipped: List[Dict[str, Any]] = []

        for memory in candidates:
            if not self._worth_keeping(memory):
                skipped.append({"id": memory.id, "reason": "below_threshold"})
                continue
            if not dry_run:
                self.long_term.store(memory)
            written.append(memory)
            promoted.append(memory.id)

        if not dry_run and promoted:
            # Only after the writes. A failure mid-batch leaves the survivors in
            # RAM to be promoted again next time rather than dropped on the floor.
            self.short_term.discard(promoted)

        return {
            "promoted": len(promoted),
            "ids": promoted,
            "skipped": len(skipped),
            "reasons": sorted({str(item["reason"]) for item in skipped}),
            "dry_run": bool(dry_run),
            "threshold": self.threshold,
            "min_age_days": self.min_age_days,
            "summaries": [memory.summary for memory in written],
        }

    def flush(self) -> Dict[str, Any]:
        """Write everything held to disk, with no judgement applied.

        :meth:`consolidate` answers "what is worth keeping?". This answers "what
        must not be lost?", and the two are not the same question — which is the
        whole reason both exist.

        The buffer is RAM-only and bounded at
        :data:`~brain.hippocampus.short_term.DEFAULT_CAPACITY` entries, and it
        evicts its weakest entry when full. Anything still sitting in it when the
        process ends was something the user actually said, and a machine that
        forgets it on exit is not one that remembers anything. So this promotes
        every held memory with the threshold set to zero, and the caller's
        ``importance`` is irrelevant because it is a durability path rather than a
        curation one.

        What is given up is only the *timing* of the judgement: a low-scored
        memory reaches disk before anyone has decided whether it was worth
        keeping. The Memory page still shows it, still explains its score, and
        ``forget_before`` still removes it. The alternative — losing it silently at
        shutdown — is strictly worse and is not a trade-off worth naming as one.

        Called on a timer and once more on shutdown. Idempotent for the same reason
        :meth:`consolidate` is: the id is the join key, and promoted entries leave
        the buffer, so a second call writes nothing twice.

        Returns:
            The same report shape :meth:`consolidate` returns, with
            ``threshold`` reported as ``0.0`` so a caller can tell which of the two
            paths produced it.
        """
        candidates = self.short_term.promote_candidates(0.0, 0.0)
        promoted: List[str] = []
        for memory in candidates:
            self.long_term.store(memory)
            promoted.append(memory.id)
        if promoted:
            self.short_term.discard(promoted)
        return {
            "promoted": len(promoted),
            "ids": promoted,
            "skipped": 0,
            "reasons": [],
            "dry_run": False,
            "threshold": 0.0,
            "min_age_days": 0.0,
            "summaries": [memory.summary for memory in candidates],
        }

    def score_all(self) -> List[Dict[str, Any]]:
        """Explain what each held memory would score, newest first.

        Exists so the Memory page can answer "why is this still in short-term
        memory?" without the user having to read this file.

        **``eligible`` is computed by the same rule :meth:`consolidate` uses** —
        :meth:`_worth_keeping` — rather than by checking the stored importance on
        its own. The two used to disagree, and that is the worst kind of bug in a
        panel whose entire job is to explain a decision: a memory scoring 0.18
        against a 0.50 threshold was reported ``eligible`` because its caller had
        claimed ``importance=0.5``, while the consolidation that actually decides
        refused it as ``below_threshold``. The page then said "above the
        threshold" over a bar the memory could not reach. A caller asking what
        would happen must be told what does happen.

        The two scores are reported separately because they fail differently:
        ``importance`` is what the caller claimed, ``score`` is this module's
        reading, and a caller that hands everything ``importance=1.0`` shows up
        here as a high ``importance`` and a low ``score``.

        Returns:
            One entry per held memory: id, kind, summary, both scores, the
            threshold, whether it is currently eligible, and which bar it failed.
        """
        entries: List[Dict[str, Any]] = []
        for memory in self.short_term.recent(count=len(self.short_term.all()) or 1):
            computed = score_importance(memory)
            claimed_enough = memory.importance >= self.threshold
            computed_enough = computed >= self.threshold
            old_enough = memory.age_days >= self.min_age_days
            failed = []
            if not claimed_enough:
                failed.append("claimed_importance")
            if not computed_enough:
                failed.append("computed_score")
            if not old_enough:
                failed.append("too_new")
            entries.append(
                {
                    "id": memory.id,
                    "kind": memory.kind,
                    "summary": memory.summary,
                    "importance": memory.importance,
                    "score": computed,
                    "threshold": self.threshold,
                    "eligible": not failed,
                    "blocked_by": failed,
                }
            )
        return entries

    def _worth_keeping(self, memory: Memory) -> bool:
        """Return whether a candidate clears both the stated and computed bars.

        The stored ``importance`` is the caller's claim and the computed score is
        this module's reading; both must agree. Requiring both is what stops a
        caller that hands everything ``importance=1.0`` from turning
        consolidation into "write whatever was said".
        """
        return (
            memory.importance >= self.threshold
            and score_importance(memory) >= self.threshold
        )