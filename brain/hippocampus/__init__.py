"""Hippocampus — short-term and long-term memory.

Everything ZUZZ knows about earlier conversations, the work it has already done,
the preferences it has been given and the mistakes it has made. The layer that
makes the previous sentence true is four modules:

* :mod:`brain.hippocampus.memory_types` — what a memory is, and the five kinds.
* :mod:`brain.hippocampus.short_term` — what is held right now, in RAM.
* :mod:`brain.hippocampus.long_term` — what is kept, as JSONL under
  ``storage/memory``.
* :mod:`brain.hippocampus.semantic_search` — how a question is matched to it.
* :mod:`brain.hippocampus.consolidation` — what graduates from the first to the
  second.

**The invariant the whole region exists to hold: remembering never writes to
disk.** :meth:`~brain.hippocampus.hippocampus.Hippocampus.remember_conversation`
and its siblings score a memory, hold it in a bounded in-memory buffer and return
an id. Only :meth:`~brain.hippocampus.hippocampus.Hippocampus.consolidate` writes,
and only for what scores above a threshold.

That separation is the point rather than tidiness. A memory that lands on disk the
instant it is spoken can never be revised, so a user who says "I use vim" and
then says "actually, neovim" would be permanently contradicted by their own
record — and the contradiction would be in the one store the user never edits by
hand. Two tiers also means a crash or the stop button cannot leave a half-formed
memory behind, because there is no half-formed state on disk to leave.

Nothing here decides permission. Memory is not an act on the machine, so the
``memory_access`` row in ``config/trust_levels.json`` is ``auto`` at every level;
the Prefrontal Cortex's gate is still consulted for every tool call that reaches
this region, and the *routes* enforce it, not this region.

See :mod:`brain.hippocampus.hippocampus` for the orchestrator,
:mod:`brain.hippocampus.exceptions` for the refusal hierarchy, and
:mod:`face.api.routes_memory` for the HTTP surface.
"""

from brain.hippocampus.consolidation import Consolidator, score_importance
from brain.hippocampus.exceptions import (
    MemoryRefused,
    MemoryStoreUnavailable,
    NothingToForget,
    QueryTooShort,
    UnknownMemoryKind,
)
from brain.hippocampus.hippocampus import Hippocampus
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
    MemoryType,
    UserProfile,
    coerce_kind,
)
from brain.hippocampus.semantic_search import SemanticIndex
from brain.hippocampus.short_term import ShortTermMemory

__all__ = [
    "CONVERSATION",
    "Consolidator",
    "FACT",
    "Hippocampus",
    "LongTermMemory",
    "MISTAKE",
    "Memory",
    "MemoryKind",
    "MemoryRefused",
    "MemoryStoreUnavailable",
    "MemoryType",
    "NAME_KEYS",
    "NothingToForget",
    "PREFERENCE",
    "QueryTooShort",
    "SemanticIndex",
    "ShortTermMemory",
    "TASK",
    "UnknownMemoryKind",
    "UserProfile",
    "coerce_kind",
    "score_importance",
]