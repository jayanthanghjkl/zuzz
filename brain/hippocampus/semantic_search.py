"""Hippocampus — semantic search.

Finds the memories worth reading for a query. "Semantic" here means *ranked by
relevance*, not *understood*. There is no embedding model and no vector store:
the dependency list is deliberately small, a local-first assistant should not
pull a transformer in to answer "what did I say about the editor", and an
embedding model small enough to ship locally produces neighbours that are
confidently wrong in exactly the way this feature cannot afford.

So the ranking is lexical and weighted. What makes it work is the weighting, not
the matching:

* **Rare words carry the signal.** A query mentioning ``nano`` should not be
  dragged toward a memory containing "file" fifteen times. Inverse document
  frequency does that.
* **A rare word that is *absent* is worse than a common word that is present.**
  Scored here, so a memory matching only stopwords scores near zero rather than
  matching on them.
* **Substring hits count, so ``config`` finds ``configuration``.** Token equality
  alone silently misses the plural of every word a user types.
* **Recency and importance break ties,** because two memories that answer the
  same question equally well should show the newer one.

Deterministic and dependency-free, which also makes it testable: the same query
over the same store always produces the same order.
"""

from __future__ import annotations

import math
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from brain.hippocampus.exceptions import QueryTooShort
from brain.hippocampus.memory_types import Memory

__all__ = ["DEFAULT_STOPWORDS", "SemanticIndex", "tokens"]

#: Words that appear in almost every memory and so distinguish nothing. Kept
#: short on purpose: an aggressive stop list makes a query about "not working"
#: match every memory about work.
DEFAULT_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "but", "by", "did", "do", "does",
        "for", "from", "had", "has", "have", "how", "i", "if", "in", "is", "it",
        "its", "me", "my", "of", "on", "or", "our", "so", "that", "the", "their",
        "then", "there", "these", "they", "this", "to", "was", "we", "were", "what",
        "when", "where", "which", "who", "why", "will", "with", "you", "your",
    }
)

#: Terms shorter than this are dropped as noise. ``c`` and ``x`` match everything.
_MIN_TOKEN = 3

#: How much a substring match is worth relative to a whole-token match. Enough
#: to be found, not enough to rank above a genuine hit on a rarer word.
_PARTIAL_CREDIT = 0.45

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> List[str]:
    """Split text into comparable lowercase terms.

    Args:
        text: Anything searchable.

    Returns:
        Lowercase alphanumeric terms, stopwords and very short noise removed.
        Preserves repeats: a word said three times is three signals, and dropping
        the duplicates would flatten emphasis.
    """
    found = _TOKEN_RE.findall(str(text).lower())
    return [
        word
        for word in found
        if len(word) >= _MIN_TOKEN and word not in DEFAULT_STOPWORDS
    ]


class SemanticIndex:
    """Ranks memories against a query.

    Built from a snapshot of the store rather than held live. A persistent index
    has to be kept in step with five files that other code also writes, and the
    store here is small enough — thousands of memories, not millions — that
    rebuilding costs less than the bookkeeping would.
    """

    def __init__(self, memories: Optional[Sequence[Memory]] = None) -> None:
        """Build an index over a snapshot of memories.

        Args:
            memories: The memories to index. Copied by reference and assumed
                immutable for the lifetime of the index.
        """
        self._memories: List[Memory] = list(memories or [])
        self._tokens: List[List[str]] = []
        self._document_frequency: Dict[str, int] = {}
        self._build()

    def __len__(self) -> int:
        """Return how many memories are indexed."""
        return len(self._memories)

    def search(
        self, query: str, limit: int = 10, kind: Optional[str] = None
    ) -> List[Tuple[Memory, float]]:
        """Return the memories most relevant to a query.

        Args:
            query: What to find. Stopwords alone are not a query.
            limit: Maximum results.
            kind: Restrict to one kind, or None for all of them.

        Returns:
            ``(memory, score)`` pairs, best first. Empty when nothing scored.

        Raises:
            QueryTooShort: When the query carries no searchable term. Ranking
                with nothing to rank by would return an arbitrary slice, and a
                caller asking "what do you remember?" would get ten unrelated
                things and read the list as an answer.
        """
        terms = tokens(query)
        if not terms:
            raise QueryTooShort(
                "that question has no searchable words in it; "
                "ask about a topic rather than about remembering itself"
            )
        capped = max(0, int(limit))
        if not capped or not self._memories:
            return []

        total = len(self._memories)
        unique_terms = set(terms)
        scored: List[Tuple[Memory, float]] = []
        for position, memory in enumerate(self._memories):
            if kind is not None and memory.kind != str(kind):
                continue
            score = self._score(terms, unique_terms, position, total)
            if score > 0.0:
                scored.append((memory, round(score, 6)))
        scored.sort(key=_ranking)
        return scored[:capped]

    def including(self, memories: Iterable[Memory]) -> "SemanticIndex":
        """Return a new index over these memories plus more.

        Used to rank short-term and durable memories on one scale. Rebuilding is
        cheap at this corpus size and keeps the two tiers from being compared
        against incompatible score ranges.

        Args:
            memories: Extra memories to fold in.

        Returns:
            A new index. This one is unchanged — an index handed out to a caller
            must not be able to change under them.
        """
        return SemanticIndex(self._memories + [item for item in memories if item not in self._memories])

    def terms_for(self, memory: Memory) -> List[str]:
        """Return the terms a memory is indexed under, for a status endpoint."""
        for position, candidate in enumerate(self._memories):
            if candidate.id == memory.id:
                return list(self._tokens[position])
        return []

    # -- internals ------------------------------------------------------

    def _build(self) -> None:
        """Tokenise every memory and tally document frequencies."""
        for memory in self._memories:
            found = tokens(memory.searchable())
            self._tokens.append(found)
            for term in set(found):
                self._document_frequency[term] = self._document_frequency.get(term, 0) + 1

    def _idf(self, term: str, total: int) -> float:
        """Return how much a term distinguishes, from 1 down toward 0.

        A term present in every memory scores zero, which is the behaviour that
        keeps a query about ``nano`` from being dragged toward a memory that says
        "file" a lot.
        """
        seen = self._document_frequency.get(term, 0)
        if not seen or not total:
            return 0.0
        # ``+ 1`` inside the log so a term in every document still yields a
        # small positive weight rather than exactly zero, and ``log(2)`` so a
        # term in half the store is worth something.
        return math.log((total + 1) / (seen + 1)) + math.log(2.0)

    def _score(
        self,
        terms: Sequence[str],
        unique_terms: set,
        index: int,
        total: int,
    ) -> float:
        """Score one memory against the query terms."""
        document = self._tokens[index]
        if not document:
            return 0.0
        document_set = set(document)
        score = 0.0
        matched = 0
        for term in unique_terms:
            weight = self._idf(term, total)
            if weight <= 0.0:
                continue
            if term in document_set:
                # Repeats add a little, with a square root so a word said five
                # times is emphatically relevant but not five times more so.
                score += weight * (1.0 + 0.25 * math.log1p(document.count(term)))
                matched += 1
            else:
                # Prefix and substring credit, so ``config`` finds
                # ``configuration``. Only when the fragment is long enough to be
                # meaningful and the fragment is rarer than the whole store.
                for word in document_set:
                    if len(term) >= 4 and term in word:
                        score += weight * _PARTIAL_CREDIT
                        matched += 1
                        break
        if not matched:
            return 0.0
        # Reward covering more of the query, and reward rarity of what matched.
        coverage = matched / max(1, len(unique_terms))
        distinct = len(document_set)
        return score * (0.5 + 0.5 * coverage) * (1.0 + math.log1p(distinct))

    def as_index(self) -> Dict[str, int]:
        """Return the term frequencies, for a status endpoint or a debug view."""
        return dict(sorted(self._document_frequency.items()))


def _ranking(item: Tuple[Memory, float]) -> Tuple[float, float, float]:
    """Sort key: score, then importance, then recency, newest first.

    Recall:
        item: A ``(memory, score)`` pair.

    Returns:
        A descending-sortable tuple. The secondary keys exist so two memories
        that answer a question equally well do not come back in an order that
        depends on the store's file order.
    """
    memory, score = item
    return (-score, -memory.importance, -memory.created)


def index_of(memories: Iterable[Memory]) -> SemanticIndex:
    """Build an index over any iterable of memories.

    Args:
        memories: The memories to index.

    Returns:
        A ready index.
    """
    return SemanticIndex(list(memories))