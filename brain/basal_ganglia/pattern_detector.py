"""Basal Ganglia — pattern detection.

Finds sequences of actions the user has actually done before, so a routine can be
built from observed behaviour rather than invented.

The input is the activity log, because that is the only honest record of what
happened. A pattern proposed from a guess is a routine that runs the wrong things,
and the user only finds out when an application opens that they did not ask for.

Three decisions shape what comes out:

* **Sessions, not one long stream.** A gap longer than
  :data:`DEFAULT_SESSION_GAP_SECONDS` starts a new session, and a sequence may
  only match within one. Without this, "open editor, open browser" at the start
  of the day and again at the end would look like a four-step habit.
* **Signature includes the target.** ``open_app:vscode.exe`` is the habit; two
  ``open_app`` steps with different targets are two different things a user may
  or may not have meant. Matching on the operation alone would produce a routine
  that opens whatever happened to be logged.
* **Longer patterns subsume shorter ones.** Where a three-step pattern and its
  own two-step prefix occur equally often, the two-step one is dropped: it is the
  same evidence, less specific, and reporting both would have the user choosing
  between a habit and a fragment of it.

Deterministic and dependency-free — the same log always yields the same patterns,
which is what makes "it detected my routine" a claim that can be tested.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "DEFAULT_MIN_REPETITIONS",
    "DEFAULT_SESSION_GAP_SECONDS",
    "MAX_WINDOW",
    "ObservedAction",
    "Pattern",
    "detect_patterns",
]

#: How many times a sequence must appear before ZUZZ calls it a habit. Five is a
#: compromise found by being wrong both ways: below three, two people comparing
#: notes get "we both open the editor first", which is a coincidence. Above ten,
#: a real daily sequence is not detected for most of a week, which is long enough
#: that the user concludes the feature does not work.
DEFAULT_MIN_REPETITIONS = 5

#: A quiet stretch this long starts a new session. Twenty minutes is roughly one
#: coffee, which separates "I opened three things to start work" from "I opened
#: three things just now" without separating two steps of the same task.
DEFAULT_SESSION_GAP_SECONDS = 1200

#: The longest sequence considered. Long enough for "open the tools, open the
#: project, run the build", short enough that a coincidence over five steps is
#: vanishingly unlikely.
MAX_WINDOW = 4

#: Activity-log actions that are bookkeeping about an action rather than actions.
#: Skipping them matters: without this, ``chat_action_refused`` sitting next to
#: ``chat_action_executed`` would make two distinct steps out of one step and its
#: rejection, and a routine built from that would refuse forever.
_BOOKKEEPING = frozenset(
    {
        "chat_action_refused",
        "chat_action_error",
        "chat_action_declined",
        "chat_action_precheck_refused",
        "chat_confirmation_reblocked",
        "audit_write_failed",
        "memory_written",
        "routine_executed",
        "routine_refused",
    }
)


@dataclass(frozen=True)
class ObservedAction:
    """One action as it appeared in the activity log.

    Attributes:
        tool: The tool or Body operation that ran.
        target: What it acted on, when the log recorded one.
        category: The trust category it was decided as, when recorded.
        at: Unix timestamp, or 0.0 when the log carried none.
    """

    tool: str
    target: str = ""
    category: str = ""
    at: float = 0.0

    def signature(self) -> str:
        """Return the identifying string for this step.

        The target is included because ``open_app:vscode.exe`` is the habit and
        ``open_app`` alone is not — two different launches, and a routine built
        from the operation name alone would open whatever was logged.
        """
        return f"{self.tool}:{self.target}" if self.target else str(self.tool)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form."""
        return {"tool": self.tool, "target": self.target, "category": self.category, "at": self.at}


@dataclass(frozen=True)
class Pattern:
    """A repeated sequence of actions.

    Attributes:
        signature: The joined step signatures; also the routine's identity.
        steps: The actions, in order.
        repetitions: How many times the whole sequence appeared.
        first_seen: Unix timestamp of the earliest occurrence, or 0.0.
        last_seen: Unix timestamp of the latest, or 0.0.
        sessions: How many distinct sessions it appeared in. The difference
            between ``repetitions`` and ``sessions`` is the difference between
            "the same habit" and "one long afternoon typed out four times", and
            ``confidence`` reads it.
    """

    signature: str
    steps: Tuple[ObservedAction, ...]
    repetitions: int
    first_seen: float = 0.0
    last_seen: float = 0.0
    sessions: int = 0
    tags: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def length(self) -> int:
        """Return how many steps the pattern has."""
        return len(self.steps)

    @property
    def confidence(self) -> float:
        """Return how much to trust this, from 0.0 to 1.0.

        Driven by how many *sessions* it appeared in rather than raw count, and
        damped for longer patterns. Five repetitions of one two-step pattern
        spread over five days is a habit; five of a four-step pattern on one
        afternoon is a coincidence four steps long, and the raw count alone
        cannot tell those apart.
        """
        spread = max(1, self.sessions)
        per_session = self.repetitions / spread
        length_penalty = 1.0 / (1.0 + 0.35 * max(0, self.length - 2))
        return round(min(1.0, per_session * 0.6 * length_penalty), 4)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form, for the API and the dashboard."""
        return {
            "signature": self.signature,
            "steps": [step.to_dict() for step in self.steps],
            "length": self.length,
            "repetitions": self.repetitions,
            "sessions": self.sessions,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "confidence": self.confidence,
            "tags": list(self.tags),
        }


def _timestamp(record: Dict[str, Any]) -> float:
    """Return a unix timestamp for an activity-log record.

    Args:
        record: One parsed activity-log line.

    Returns:
        Seconds since the epoch, or 0.0 when the record carried no usable time.
        ISO-8601 is what the logger writes, so that is the format handled; a bare
        number is accepted too because the dashboard and the tests both produce
        them.
    """
    raw = record.get("timestamp", record.get("at", record.get("created", 0.0)))
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    text = str(raw or "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return parsed.timestamp() if parsed.tzinfo else parsed.astimezone().timestamp()


def _as_action(record: Dict[str, Any]) -> Optional[ObservedAction]:
    """Return the action a log record describes, or None if it describes none.

    A record counts when it names a tool or a Body operation. The ``tool`` field
    wins over ``action``, because the chat pipeline logs the wrapper under
    ``action`` and the thing that ran under ``tool``.

    Args:
        record: One parsed activity-log line.

    Returns:
        The action, or None for bookkeeping, routing chatter and anything that
        does not name something that ran.
    """
    tool = str(record.get("tool", "") or "").strip()
    if not tool:
        action = str(record.get("action", "") or "").strip()
        if not action or action in _BOOKKEEPING:
            return None
        tool = action
    return ObservedAction(
        tool=tool,
        target=str(record.get("target", "") or "").strip(),
        category=str(record.get("category", "") or "").strip(),
        at=_timestamp(record),
    )


def _split_sessions(
    actions: Sequence[ObservedAction], gap_seconds: float
) -> List[List[ObservedAction]]:
    """Split actions into sessions on long quiet gaps.

    Records with no timestamp are placed by their position in the log and never
    split a session, because inventing a time for them would put an arbitrary
    boundary in the middle of a habit.
    """
    sessions: List[List[ObservedAction]] = [[]]
    previous: Optional[float] = None
    for action in actions:
        if (
            previous is not None
            and action.at
            and previous
            and (action.at - previous) > gap_seconds
        ):
            sessions.append([])
        sessions[-1].append(action)
        if action.at:
            previous = action.at
    return [session for session in sessions if session]


def _is_subsequence_of(shorter: str, longer: str) -> bool:
    """Return whether ``shorter`` appears as consecutive steps of ``longer``."""
    parts = longer.split(">")
    size = len(shorter.split(">"))
    return any(">".join(parts[index : index + size]) == shorter for index in range(len(parts) - size + 1))


def detect_patterns(
    activity_log: Iterable[Dict[str, Any]],
    min_repetitions: int = DEFAULT_MIN_REPETITIONS,
    max_window: int = MAX_WINDOW,
    session_gap_seconds: float = DEFAULT_SESSION_GAP_SECONDS,
) -> List[Pattern]:
    """Find the sequences of actions that keep repeating.

    Args:
        activity_log: Activity-log records, oldest first. The tail of
            :meth:`brain.brainstem.activity_logger.ActivityLogger.tail` is
            exactly this shape.
        min_repetitions: How many times a sequence must appear to count. The
            fifth repetition is what makes this worth checking; see
            :data:`DEFAULT_MIN_REPETITIONS` for why the threshold is five and not
            three or ten.
        max_window: The longest sequence to look for.
        session_gap_seconds: A quiet stretch this long starts a new session, and a
            sequence may only match within one session.

    Returns:
        Patterns, strongest first. Empty when the log holds nothing repeated
        enough to be a habit.

    Notes:
        Never raises on a malformed log. A detector that refuses to report
        because one line was unreadable would go silent exactly when the log is
        most worth reading.
    """
    records = [record for record in (activity_log or []) if isinstance(record, dict)]
    actions = [
        action for action in (_as_action(record) for record in records) if action is not None
    ]
    if not actions:
        return []
    actions.sort(key=lambda action: (action.at or 0.0))

    needed = max(2, int(min_repetitions))
    ceiling = max(1, min(int(max_window), MAX_WINDOW))
    sessions = _split_sessions(actions, float(session_gap_seconds))

    found: Dict[str, Dict[str, Any]] = {}
    for size in range(1, ceiling + 1):
        for session in sessions:
            for start in range(len(session) - size + 1):
                window = tuple(session[start : start + size])
                signature = ">".join(step.signature() for step in window)
                entry = found.get(signature)
                if entry is None:
                    found[signature] = {
                        "steps": window,
                        "repetitions": 1,
                        "first_seen": window[0].at,
                        "last_seen": window[-1].at,
                        "sessions": 1,
                    }
                    continue
                entry["repetitions"] += 1
                entry["last_seen"] = window[-1].at or entry["last_seen"]
                if window[0].at and not entry["first_seen"]:
                    entry["first_seen"] = window[0].at

    # One occurrence per window position per session already inflates the count
    # for a sequence repeated back-to-back inside a single session, so a session
    # is only counted once per signature.
    per_session: Dict[str, set] = {}
    for session in sessions:
        for size in range(1, ceiling + 1):
            for start in range(len(session) - size + 1):
                window = tuple(session[start : start + size])
                per_session.setdefault(
                    ">".join(step.signature() for step in window), set()
                ).add(id(session))

    patterns: List[Pattern] = []
    for signature, entry in found.items():
        if entry["repetitions"] < needed:
            continue
        entry["sessions"] = max(1, len(per_session.get(signature, ())))
        patterns.append(
            Pattern(
                signature=signature,
                steps=entry["steps"],
                repetitions=entry["repetitions"],
                first_seen=entry["first_seen"],
                last_seen=entry["last_seen"],
                sessions=entry["sessions"],
            )
        )

    patterns = _drop_subsumed(patterns)
    patterns.sort(key=lambda pattern: (-pattern.repetitions, -pattern.confidence, pattern.signature))
    return patterns


def _drop_subsumed(patterns: List[Pattern]) -> List[Pattern]:
    """Remove a pattern another one contains, on equal evidence.

    A two-step pattern occurring five times and its own three-step prefix
    occurring five times are the same five events; the longer one is strictly more
    informative and is the one worth offering. Patterns at different counts are
    both kept, because a shorter pattern seen more often is a real signal about a
    smaller habit.
    """
    kept: List[Pattern] = []
    for candidate in patterns:
        subsumed = any(
            other is not candidate
            and other.length > candidate.length
            and other.repetitions == candidate.repetitions
            and _is_subsequence_of(candidate.signature, other.signature)
            for other in patterns
        )
        if not subsumed:
            kept.append(candidate)
    return kept


def now() -> float:
    """Return the current time, so callers can compute an age from a pattern."""
    return time.time()