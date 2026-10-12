"""Mirror Neurons — record what the user does.

The recorder is the only part of this region that touches the desktop, and it
does so *passively*: it asks the Parietal Lobe for the active window and the
pointer position, and it asks the activity logger for what has happened since
the session started. It never issues a tool call of its own, and it never
writes to the activity log — it reads from it, and the reads are gated like
any other system-awareness act.

A session is a list of records, oldest first. Each record is a small dict with
the fields the pattern detector needs: what tool was involved, what it acted
on, what context the user was in, and when it happened. The shape is kept
deliberately plain so the pattern detector can be tested without the recorder.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from brain.mirror_neurons.exceptions import ObservationRefused
from brain.mirror_neurons.observation_filter import ObservationFilter, FilterVerdict

__all__ = ["BehaviorRecorder", "record"]


class BehaviorRecorder:
    """Collect the acts a user performs while an observation session is open.

    Attributes:
        filter: What to keep. Injectable so a test can feed it a rule that
            ignores everything and assert the session stays empty.
        parietal: The Parietal Lobe, for context. Optional — without it every
            record's context is ``"unknown"``, which is still a usable record,
            just a less informative one.
    """

    def __init__(
        self,
        filter: Optional[ObservationFilter] = None,
        parietal: Any = None,
    ) -> None:
        """Wire up the recorder.

        Args:
            filter: What to keep. Defaults to the shipped permissive filter.
            parietal: The Parietal Lobe, for context. May be None.
        """
        self.filter = filter if filter is not None else ObservationFilter()
        self.parietal = parietal
        self.records: List[Dict[str, Any]] = []
        self._started: float = 0.0
        self._open: bool = False

    def start(self) -> None:
        """Begin a session.

        Raises:
            ObservationAlreadyActive: When one is already running. Two
                sessions would interleave their records and produce patterns
                that are a mess. Refusing is cheaper than a warning label.
        """
        if self._open:
            from brain.mirror_neurons.exceptions import ObservationAlreadyActive

            raise ObservationAlreadyActive(
                "an observation session is already running; stop it first"
            )
        self.records = []
        self._started = time.time()
        self._open = True

    def stop(self) -> List[Dict[str, Any]]:
        """End the session and return the records kept.

        Returns:
            The records, oldest first.

        Raises:
            ObservationNotActive: When no session is open.
        """
        if not self._open:
            from brain.mirror_neurons.exceptions import ObservationNotActive

            raise ObservationNotActive("no observation session is running")
        self._open = False
        kept = list(self.records)
        self.records = []
        return kept

    @property
    def active(self) -> bool:
        """Return whether a session is open."""
        return self._open

    def record(self, entry: Dict[str, Any]) -> FilterVerdict:
        """Run one act through the filter and keep it if it passes.

        Args:
            entry: The act. The recorder adds ``captured_at`` unless the caller
                supplied one, so every record carries its own timestamp.

        Returns:
            The filter's verdict, so a caller can log why something was
            ignored. The default recorder does not log — it is the route's
            job, and the route already logs everything it touches.
        """
        verdict = self.filter.decide(entry)
        if not verdict.watched:
            return verdict
        payload = dict(entry)
        payload.setdefault("captured_at", time.time())
        self.records.append(payload)
        return verdict

    def context(self) -> str:
        """Return the current context, or ``"unknown"`` when no lobe is attached.

        The inference stays on the machine and the category is what reaches
        the record — the lobe's own module docstring argues the case for not
        shipping the title bar to the model.
        """
        if self.parietal is None:
            return "unknown"
        try:
            return str(self.parietal.detect_context().get("context", "unknown"))
        except Exception:
            return "unknown"


def record(
    recorder: BehaviorRecorder,
    tool: str,
    target: str = "",
    context: Optional[str] = None,
    category: str = "",
    detail: Optional[Dict[str, Any]] = None,
) -> FilterVerdict:
    """Record one act through ``recorder``, with context looked up if needed.

    A small helper so the route does not have to build the dict by hand. The
    context is looked up from the recorder's lobe when the caller does not
    supply one, which is the common case — the user did something, and the
    question is what they were doing when they did it.

    Args:
        recorder: The session to record into.
        tool: What was involved, e.g. ``"file_write"``.
        target: What it acted on. A path, an app name, a URL.
        context: The inferred context, or None to look it up.
        category: The trust category, if known.
        detail: Extra fields to carry.

    Returns:
        The filter's verdict.
    """
    entry: Dict[str, Any] = {
        "tool": tool,
        "target": target,
        "context": context if context is not None else recorder.context(),
        "category": category,
    }
    if detail:
        entry["detail"] = detail
    return recorder.record(entry)