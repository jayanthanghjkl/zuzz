"""Mirror Neurons — find the workflows in a set of recorded acts.

The learner is the part that turns a pile of records into a list of patterns.
It is deliberately simple: it looks for the same sequence of tools appearing
multiple times, and it reports how many times it saw each one.

The matching is on *tool names* rather than on full records, for two reasons.
The first is that a workflow is about what was done, not the exact arguments —
"save this file" and "save that file" are the same workflow, and matching on
the tool name says so. The second is that the arguments are where the private
stuff lives, and the learner is not the place to be reading them.

A sequence is a window of consecutive acts. The default window is the whole
session, which is the common case: a user does a thing five times in a row and
the pattern is the whole session. A smaller window finds shorter workflows
inside a longer one, at the cost of more false positives from unrelated acts
that happened to sit next to each other.

The threshold is five repetitions, which is the number the Basal Ganglia uses
for its own pattern detector. Consistency across the two regions is the point:
what counts as a habit in one place counts as a habit in the other.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from brain.mirror_neurons.pattern_cloner import Pattern

__all__ = ["WorkflowLearner", "DEFAULT_MIN_REPETITIONS", "learn"]


#: How many times a sequence must appear to count as a pattern.
DEFAULT_MIN_REPETITIONS = 5

#: The default window size, in acts. ``None`` means the whole session.
DEFAULT_WINDOW: Optional[int] = None


class WorkflowLearner:
    """Find repeated sequences of acts.

    The learner is stateless across calls — it is given records and returns
    patterns. Keeping it stateless means a test can feed it one session's
    worth of records and assert on the patterns, without the learner having
    watched anything itself.
    """

    def __init__(
        self,
        min_repetitions: int = DEFAULT_MIN_REPETITIONS,
        window: Optional[int] = DEFAULT_WINDOW,
    ) -> None:
        """Build a learner.

        Args:
            min_repetitions: How many times a sequence must appear.
            window: How many acts to consider at once. None for the whole
                session. A smaller window finds shorter workflows.
        """
        self.min_repetitions = max(1, int(min_repetitions))
        self.window = window

    def learn(self, records: Sequence[Dict[str, Any]]) -> List[Pattern]:
        """Find patterns in ``records``.

        Args:
            records: Acts, oldest first. Each must carry a ``tool``.

        Returns:
            Patterns, strongest first — most occurrences, then longest.
        """
        if len(records) < self.min_repetitions:
            return []
        windows = self._windows(records)
        counts: Dict[Tuple[str, ...], List[List[Dict[str, Any]]]] = defaultdict(list)
        for window_records in windows:
            key = tuple(
                str(record.get("tool", "") or "").strip().lower()
                for record in window_records
            )
            if key and all(key):
                counts[key].append(list(window_records))
        patterns: List[Pattern] = []
        for key, occurrences in counts.items():
            if len(occurrences) < self.min_repetitions:
                continue
            steps = self._merge(occurrences)
            if not steps:
                continue
            first = min(step.get("captured_at", 0.0) for step in steps if step.get("captured_at"))
            last = max(step.get("captured_at", 0.0) for step in steps if step.get("captured_at"))
            patterns.append(
                Pattern(
                    id="pattern_" + "_".join(key),
                    steps=steps,
                    occurrences=len(occurrences),
                    first_seen=first,
                    last_seen=last,
                    context=steps[0].get("context", "") if steps else "",
                )
            )
        patterns.sort(key=lambda pattern: (-pattern.occurrences, -len(pattern.steps)))
        return patterns

    def _windows(self, records: Sequence[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """Return every window of acts the learner will look at."""
        if self.window is None:
            return [list(records)]
        size = max(2, int(self.window))
        return [list(records[i : i + size]) for i in range(len(records) - size + 1)]

    def _merge(self, occurrences: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """Return one representative copy of the sequence.

        The first occurrence is used as the template, because it is the one
        that started the habit. The other occurrences are evidence of how many
        times it happened, not sources of steps.
        """
        template = occurrences[0]
        return [dict(step) for step in template]


def learn(
    records: Sequence[Dict[str, Any]],
    min_repetitions: int = DEFAULT_MIN_REPETITIONS,
    window: Optional[int] = DEFAULT_WINDOW,
) -> List[Pattern]:
    """Find patterns in ``records`` using a default learner.

    A convenience wrapper so the orchestrator does not have to construct a
    learner for a one-off call.
    """
    return WorkflowLearner(min_repetitions=min_repetitions, window=window).learn(records)