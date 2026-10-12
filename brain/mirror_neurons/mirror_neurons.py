"""Mirror Neurons — the orchestrator.

This is the single entry point the tools and the routes use. It wires the
recorder, the filter, the learner, and the macro generator into one object
that holds the state of one observation session at a time.

The public methods correspond to the five tools the region exposes:

* ``start_observation`` — open a session
* ``stop_observation`` — close it and return the records kept
* ``get_detected_patterns`` — run the learner on the current or last session
* ``create_macro_from_pattern`` — turn one pattern into a routine object
* ``suggest_automation`` — run the learner, pick the strongest pattern, and
  return a macro if one meets the threshold

The orchestrator does not store macros and does not arm them. It builds the
:class:`~brain.mirror_neurons.pattern_cloner.Macro` object and hands it back;
the caller decides whether to store it in the Basal Ganglia.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from brain.mirror_neurons.exceptions import (
    MacroCreationRefused,
    ObservationAlreadyActive,
    ObservationNotActive,
    ObservationRefused,
    PatternNotFound,
)
from brain.mirror_neurons.behavior_recorder import BehaviorRecorder, record as record_act
from brain.mirror_neurons.observation_filter import ObservationFilter
from brain.mirror_neurons.pattern_cloner import Macro, Pattern
from brain.mirror_neurons.workflow_learner import WorkflowLearner
from brain.mirror_neurons.macro_generator import generate_macro, MAX_MACRO_STEPS

__all__ = [
    "MirrorNeurons",
    "ObservationSession",
    "Pattern",
]


@dataclass
class ObservationSession:
    """A completed observation session.

    Attributes:
        session_id: Stable identifier for the session.
        records: The acts that passed the filter, oldest first.
        started_at: When the session began.
        stopped_at: When the session ended.
        duration_seconds: How long the session ran.
        patterns: Patterns detected from this session (cached).
    """

    session_id: str
    records: List[Dict[str, Any]]
    started_at: float
    stopped_at: float
    duration_seconds: float
    patterns: List[Pattern] = None

    def __post_init__(self):
        if self.patterns is None:
            self.patterns = []

    @property
    def record_count(self) -> int:
        """Return the number of records in this session."""
        return len(self.records)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form."""
        return {
            "session_id": self.session_id,
            "record_count": len(self.records),
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "duration_seconds": self.duration_seconds,
            "patterns": [p.to_dict() for p in self.patterns],
        }


class MirrorNeurons:
    """Learn workflows by watching the user.

    A single orchestrator instance holds one active session at a time. When a
    session stops, its records are kept in ``last_session`` so the user can
    run the learner on them, suggest a macro, or review what was captured.

    The orchestrator is safe to share across threads — the methods that
    mutate state (``start_observation``, ``stop_observation``) are not
    internally locked, but the region is intended to be called from the
    single-threaded async server, so contention is not a concern. If a
    multi-threaded caller needs safety, they should wrap the calls.
    """

    def __init__(
        self,
        parietal: Any = None,
        filter: Optional[ObservationFilter] = None,
    ) -> None:
        """Wire up the internal components.

        Args:
            parietal: The Parietal Lobe, for context inference. Optional —
                without it every record's context is "unknown".
            filter: The observation filter. Defaults to the shipped permissive
                filter.
        """
        self._recorder = BehaviorRecorder(filter=filter, parietal=parietal)
        self._learner = WorkflowLearner()
        self._last_session: Optional[ObservationSession] = None
        self._session_counter = 0

    def start_observation(self) -> str:
        """Begin a new observation session.

        Returns:
            The session ID.

        Raises:
            ObservationAlreadyActive: When a session is already running.
            ObservationRefused: When the filter would ignore everything.
        """
        # Check if filter would ignore everything (a filter with only ignore rules)
        if self._recorder.filter.rules and all(r.verdict == "ignore" for r in self._recorder.filter.rules):
            raise ObservationRefused("the observation filter would discard every act; add a watch rule first")

        self._recorder.start()
        self._session_counter += 1
        return f"obs_{int(time.time())}_{self._session_counter}"

    def stop_observation(self) -> ObservationSession:
        """End the current session and return the records kept.

        Returns:
            An :class:`ObservationSession` with the records and metadata.

        Raises:
            ObservationNotActive: When no session is running.
        """
        if not self._recorder.active:
            raise ObservationNotActive("no observation session is running")

        records = self._recorder.stop()
        stopped_at = time.time()
        session_id = f"obs_{int(stopped_at)}_{self._session_counter}"
        started_at = stopped_at - (stopped_at - self._recorder._started) if self._recorder._started else stopped_at

        session = ObservationSession(
            session_id=session_id,
            records=records,
            started_at=self._recorder._started,
            stopped_at=stopped_at,
            duration_seconds=stopped_at - self._recorder._started,
        )
        self._last_session = session
        return session

    def get_detected_patterns(self, records: Optional[Sequence[Dict[str, Any]]] = None) -> List[Pattern]:
        """Run the learner on the given records or the last session.

        Args:
            records: Acts to analyze. When omitted, uses the last completed
                session's records.

        Returns:
            Patterns found, strongest first.

        Raises:
            ObservationNotActive: When no records are available and no session
                has been completed.
        """
        if records is None:
            if self._last_session is None:
                raise ObservationNotActive("no observation session has been completed")
            records = self._last_session.records

        patterns = self._learner.learn(records)
        if self._last_session is not None:
            self._last_session.patterns = patterns
        return patterns

    def create_macro_from_pattern(self, pattern_id: str, records: Optional[Sequence[Dict[str, Any]]] = None) -> Macro:
        """Turn a detected pattern into a macro.

        Args:
            pattern_id: The pattern's id, as returned by ``get_detected_patterns``.
            records: Acts to re-learn from. When omitted, uses the last session.

        Returns:
            A :class:`~brain.mirror_neurons.pattern_cloner.Macro` ready to store.

        Raises:
            PatternNotFound: When ``pattern_id`` names no known pattern.
            MacroCreationRefused: When the pattern cannot become a routine.
        """
        patterns = self.get_detected_patterns(records)
        pattern = next((p for p in patterns if p.id == pattern_id), None)
        if pattern is None:
            raise PatternNotFound(f"pattern {pattern_id!r} was not detected")
        return generate_macro(pattern)

    def suggest_automation(self, records: Optional[Sequence[Dict[str, Any]]] = None) -> Optional[Macro]:
        """Run the learner and return a macro for the strongest pattern.

        This is the "one button" path: run the learner on the current or last
        session, pick the pattern with the most occurrences, and build a macro
        from it if it meets the repetition threshold.

        Args:
            records: Acts to analyze. When omitted, uses the last completed
                session's records.

        Returns:
            A macro, or ``None`` when no pattern meets the threshold.
        """
        patterns = self.get_detected_patterns(records)
        if not patterns:
            return None
        strongest = patterns[0]
        try:
            return generate_macro(strongest)
        except MacroCreationRefused:
            return None

    @property
    def active(self) -> bool:
        """Return whether an observation session is currently running."""
        return self._recorder.active

    @property
    def last_session(self) -> Optional[ObservationSession]:
        """Return the most recently completed session, if any."""
        return self._last_session

    def record_act(
        self,
        tool: str,
        target: str = "",
        context: Optional[str] = None,
        category: str = "",
        detail: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Record one act through the active session's recorder.

        This is the hook the routes call when a tool executes, so the mirror
        neurons see what the user is doing in real time.

        Returns:
            The filter's verdict.
        """
        if not self._recorder.active:
            from brain.mirror_neurons.observation_filter import FilterVerdict
            return FilterVerdict(watched=False)
        return record_act(self._recorder, tool, target, context, category, detail)