"""Basal Ganglia — context triggers.

Decides *when* a routine fires. Separate from the routine and from the permission
decision on purpose, because those are three different questions and conflating
them is how a habit engine ends up launching applications at the wrong time:

* :mod:`brain.basal_ganglia.pattern_detector` answers "does this sequence happen?"
* :mod:`brain.basal_ganglia.routine_builder` answers "what should it do?"
* this module answers "is now the moment?"
* the trust matrix answers "is it allowed?" — and it is asked last, because a
  routine that is not allowed now is still not allowed at nine in the morning.

Four trigger kinds, and nothing cleverer:

``manual``
    Never fires on its own. The default, and the only one that cannot surprise
    anyone.
``time``
    At an hour and minute, optionally on named weekdays. A window rather than an
    instant, so "start work at nine" means nine o'clock rather than the single
    second the scheduler happened to wake up at.
``app``
    When an application is open. Checked against what the user actually has open,
    not against what ZUZZ opened.
``command``
    When the user says something matching a phrase. The lowest-trust trigger and
    the only one that can be triggered by text.

**Every check fails closed.** A trigger whose subject cannot be determined returns
False rather than raising, because the alternative is a background scheduler
raising once an hour and a routine that fires erratically instead of never.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from brain.basal_ganglia.exceptions import TriggerRefused

__all__ = [
    "APP",
    "COMMAND",
    "KIND_VALUES",
    "MANUAL",
    "TIME",
    "Trigger",
    "describe",
    "due",
]

#: The four trigger kinds.
MANUAL = "manual"
TIME = "time"
APP = "app"
COMMAND = "command"

KIND_VALUES: Tuple[str, ...] = (MANUAL, APP, TIME, COMMAND)

#: How wide a time trigger's window is, in minutes. Fifteen is wide enough to
#: survive a scheduler that wakes up late and narrow enough that "start work at
#: nine" does not also fire at half nine after lunch.
DEFAULT_TIME_WINDOW_MINUTES = 15

#: Weekday names, indexed to match ``datetime.weekday()`` so a caller can pass
#: ``["monday", "wednesday"]`` and get the right days.
_WEEKDAYS: Dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


@dataclass(frozen=True)
class Trigger:
    """When a routine should fire.

    Attributes:
        kind: One of the four kinds.
        at: ``HH:MM`` for a time trigger.
        days: Weekday names, lower case. Empty means every day.
        app: Application or window name an app trigger watches for.
        command: A phrase a command trigger matches against.
        match_all_words: Whether every word of the phrase must be present rather
            than the phrase as a whole.
        window_minutes: How wide a time trigger's window is.
        enabled: A switch that silences the trigger without deleting it.
    """

    kind: str = MANUAL
    at: str = ""
    days: Tuple[str, ...] = ()
    app: str = ""
    command: str = ""
    match_all_words: bool = False
    window_minutes: int = DEFAULT_TIME_WINDOW_MINUTES
    enabled: bool = True

    def matches(self, context: Optional[Dict[str, Any]] = None) -> bool:
        """Return whether this trigger fires for a context.

        Args:
            context: What is known right now. ``at`` or ``now`` as a
                ``datetime`` or timestamp, ``apps`` as a list of open
                application names, ``message`` as the user's latest text.

        Returns:
            True when the trigger fires. False whenever anything is undetermined
            — an unreadable clock does not fire a routine, and neither does a
            missing application list.
        """
        if not self.enabled:
            return False
        state = context or {}
        if self.kind == TIME:
            return self._matches_time(state)
        if self.kind == APP:
            return self._matches_app(state)
        if self.kind == COMMAND:
            return self._matches_command(state)
        return False

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form."""
        return {
            "kind": self.kind,
            "at": self.at,
            "days": list(self.days),
            "app": self.app,
            "command": self.command,
            "match_all_words": self.match_all_words,
            "window_minutes": self.window_minutes,
            "enabled": self.enabled,
            "description": describe(self),
        }

    def _matches_time(self, context: Dict[str, Any]) -> bool:
        """Return whether the clock is inside the window."""
        moment = _as_datetime(context.get("at", context.get("now")))
        if moment is None or not self.at:
            return False
        if self.days and _WEEKDAYS.get(moment.strftime("%A").lower(), -1) not in {
            _WEEKDAYS.get(day, -1) for day in self.days
        }:
            return False
        try:
            hour, minute = (int(part) for part in self.at.split(":"))
        except (AttributeError, ValueError):
            return False
        target = hour * 60 + minute
        current = moment.hour * 60 + moment.minute
        distance = abs(current - target)
        # Wrap around midnight rather than refusing: a trigger at 23:55 with a
        # fifteen-minute window has to be able to fire at 00:05, and a morning
        # routine is exactly where that shows up.
        return min(distance, 1440 - distance) <= max(0, int(self.window_minutes))

    def _matches_app(self, context: Dict[str, Any]) -> bool:
        """Return whether the watched application is open."""
        if not self.app:
            return False
        opened = context.get("apps", context.get("windows", []))
        if not isinstance(opened, (list, tuple, set)):
            return False
        needle = self.app.strip().lower()
        for entry in opened:
            text = str(entry).lower()
            if needle in text or text.split(".")[0] in needle:
                return True
        return False

    def _matches_command(self, context: Dict[str, Any]) -> bool:
        """Return whether the latest message matches the phrase."""
        if not self.command:
            return False
        message = str(context.get("message", "") or "").lower()
        if not message:
            return False
        phrase = self.command.strip().lower()
        if phrase in message:
            return True
        if not self.match_all_words:
            return False
        # Split rather than compare whole phrases, so a phrase written with odd
        # spacing still matches the words the user actually typed.
        return all(word in message for word in re.split(r"\s+", phrase) if word)


def _as_datetime(value: Any) -> Optional[datetime]:
    """Return a naive local datetime for whatever a caller passed as now.

    Args:
        value: A ``datetime``, a unix timestamp, or an ISO-8601 string.

    Returns:
        The moment, as a naive datetime in **this machine's local time**, or None
        when the value is absent or unreadable. None means the caller must treat
        the check as "not now" rather than guessing.

    Everything is normalised to naive local time because the only thing a time
    trigger ever does with it is read ``.hour`` and ``.minute``, and those have
    to mean the same thing however the value arrived. They did not:

    * a ``datetime`` was returned untouched, so an aware UTC value kept its UTC
      hour;
    * a timestamp went through ``fromtimestamp``, which is local.

    So ``09:00`` fired at nine in the morning for one caller and at 09:00 UTC —
    14:30 in India — for another, for the same routine on the same machine. A
    routine that opens the user's editor in the morning is about their wall
    clock, so aware inputs are converted to local and the tzinfo dropped, which
    also makes the three input shapes comparable to each other.
    """
    if isinstance(value, datetime):
        return _to_local(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value))
        except (OSError, ValueError, OverflowError):
            return None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _to_local(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _to_local(moment: datetime) -> datetime:
    """Return ``moment`` as a naive datetime in local time."""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone().replace(tzinfo=None)


def parse(spec: Any) -> Trigger:
    """Build a trigger from plain data.

    Args:
        spec: A mapping, or a bare string naming one of the four kinds.

    Returns:
        The trigger, defaulting to ``manual`` where a field is absent.

    Raises:
        TriggerRefused: When the kind is not one of the four, a time is not
            ``HH:MM``, a weekday is not a weekday, or a kind's subject is missing.
            A trigger that names nothing would be stored looking armed and never
            fire, which is worse than a refusal the user can see and fix.
    """
    if isinstance(spec, str):
        spec = {"kind": spec}
    if not isinstance(spec, dict):
        raise TriggerRefused(
            f"a trigger must be a mapping or a kind name, got {type(spec).__name__}"
        )

    kind = str(spec.get("kind", MANUAL) or MANUAL).strip().lower()
    if kind not in KIND_VALUES:
        raise TriggerRefused(
            f"unknown trigger kind {kind!r}; ZUZZ has: {', '.join(KIND_VALUES)}"
        )

    at = str(spec.get("at", "") or "").strip()
    if at:
        parts = at.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise TriggerRefused(f"a time trigger needs HH:MM, got {at!r}")
        if not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
            raise TriggerRefused(f"{at!r} is not a time of day")
        at = f"{int(parts[0]):02d}:{int(parts[1]):02d}"

    days: List[str] = []
    for day in spec.get("days", []) or []:
        name = str(day).strip().lower()
        if name not in _WEEKDAYS:
            raise TriggerRefused(
                f"unknown weekday {day!r}; use: {', '.join(_WEEKDAYS)}"
            )
        days.append(name)

    app = str(spec.get("app", "") or "").strip()
    command = str(spec.get("command", "") or "").strip()

    if kind == TIME and not at:
        raise TriggerRefused("a time trigger needs an 'at' of HH:MM")
    if kind == APP and not app:
        raise TriggerRefused("an app trigger needs an 'app' to watch for")
    if kind == COMMAND and not command:
        raise TriggerRefused("a command trigger needs a 'command' phrase to match")

    try:
        window = int(spec.get("window_minutes", DEFAULT_TIME_WINDOW_MINUTES))
    except (TypeError, ValueError) as exc:
        raise TriggerRefused("window_minutes must be a whole number of minutes") from exc

    return Trigger(
        kind=kind,
        at=at,
        days=tuple(days),
        app=app,
        command=command,
        match_all_words=bool(spec.get("match_all_words", False)),
        window_minutes=max(0, min(240, window)),
        enabled=bool(spec.get("enabled", True)),
    )


def describe(trigger: Any) -> str:
    """Return a one-line description of a trigger, for the routines list.

    Args:
        trigger: A :class:`Trigger` or plain data describing one.

    Returns:
        A sentence. An unparseable trigger describes itself as such rather than
        raising, because this is used while rendering a list of routines and one
        bad entry should not blank the page.
    """
    try:
        parsed = trigger if isinstance(trigger, Trigger) else parse(trigger)
    except TriggerRefused as exc:
        return f"invalid trigger ({exc})"
    if parsed.kind == MANUAL:
        return "runs only when asked"
    if parsed.kind == TIME:
        days = f" on {', '.join(parsed.days)}" if parsed.days else ""
        return f"at {parsed.at}{days} (±{parsed.window_minutes}m)"
    if parsed.kind == APP:
        return f"when {parsed.app} is open"
    if parsed.kind == COMMAND:
        return f"when a message says “{parsed.command}”"
    return parsed.kind


def due(
    routines: Iterable[Any], context: Optional[Dict[str, Any]] = None
) -> List[Any]:
    """Return the routines whose trigger fires now.

    Args:
        routines: The stored routines.
        context: What is known right now — see :meth:`Trigger.matches`.

    Returns:
        The routines that are due, in the order given. A routine whose trigger
        fails to parse is skipped rather than raised on, so one malformed entry
        cannot stop the rest from firing.
    """
    firing: List[Any] = []
    for routine in routines or []:
        trigger = getattr(routine, "trigger", None)
        if trigger is None:
            continue
        try:
            parsed = trigger if isinstance(trigger, Trigger) else parse(trigger)
        except TriggerRefused:
            continue
        if parsed.matches(context):
            firing.append(routine)
    return firing


def context_from(
    at: Any = None,
    apps: Optional[Sequence[str]] = None,
    message: str = "",
) -> Dict[str, Any]:
    """Build a trigger context from loose arguments.

    Args:
        at: The current time. Defaults to now.
        apps: Open application or window names.
        message: The user's latest message.

    Returns:
        A context mapping for :meth:`Trigger.matches`.
    """
    moment = _as_datetime(at) or datetime.now()
    payload: Dict[str, Any] = {"at": moment, "message": str(message or "")}
    if apps is not None:
        payload["apps"] = [str(item) for item in apps]
    return payload