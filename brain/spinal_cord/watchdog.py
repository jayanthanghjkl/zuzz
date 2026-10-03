"""Spinal Cord — Watchdog.

Polls for the conditions that mean ZUZZ is misbehaving and trips the same switch
the hotkey does. A runaway loop or a mass deletion is exactly the situation a
user cannot fix by clicking, which is why detection belongs in a separate
process from the work being watched.

Signals, all cheap to read:

* **CPU saturation.** A process spinning at full tilt for several consecutive
  polls is stuck, not working. One poll can be a legitimate model load, so the
  signal requires a *streak*.
* **Mass file deletion.** Many deletions inside one window means a loop, not a
  task. Counted from the activity log rather than by watching the filesystem, so
  the watchdog sees what ZUZZ did and not what the user's machine did.
* **Desktop input rate** (added with Body/Hands). Mass clicks, mass keystrokes
  and mass application launches mean the same thing in a different place: a
  loop, not a task. A human clicking fifty times in ten seconds is not a user.
* **Repeatedly refused hotkeys.** Reaching for a combination that is on the
  blocklist, over and over, is not a user changing their mind.

Every rate signal is counted from the activity log by *action name*, which makes
the Body's log vocabulary part of this module's contract: if the Body renames
``click_mouse``, the click signal silently stops counting. That is deliberate —
one place decides what a dangerous rate looks like, and the other has to say
honestly what it did.

The watchdog only ever trips the switch; it never resets it. A detector that
could clear its own alarm would be no detector.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from brain.spinal_cord.stop_state import SOURCE_WATCHDOG

#: Consecutive saturated polls before the watchdog trips.
CPU_STREAK_LIMIT = 5

#: Deletions inside the window that count as mass deletion.
DELETION_LIMIT = 25

#: Window the deletion count is measured over.
DELETION_WINDOW_SECONDS = 60.0

#: Clicks inside the window that count as a runaway input loop.
MASS_CLICK_LIMIT = 50

#: Window the click rate is measured over.
CLICK_WINDOW_SECONDS = 10.0

#: Keystrokes inside the window that count as a runaway typing loop.
MASS_KEYSTROKE_LIMIT = 500

#: Window the keystroke rate is measured over.
KEYSTROKE_WINDOW_SECONDS = 10.0

#: Application launches inside the window that count as a spawn loop.
MASS_APP_OPEN_LIMIT = 10

#: Window the application-launch count is measured over.
APP_OPEN_WINDOW_SECONDS = 60.0

#: Refused hotkeys inside the window that count as probing.
#: Deliberately above 1: one refusal is the gate working. A run of them is an
#: agent working through a blocklist, and that is what trips.
BLOCKED_HOTKEY_LIMIT = 3

#: Window the refused-hotkey count is measured over.
HOTKEY_WINDOW_SECONDS = 60.0

#: Log action fragments the Body uses, per signal. Matched as substrings so a
#: single operation logged once can serve more than one count.
#:
#: The four sets are mutually exclusive as substrings, which matters because
#: substring matching is otherwise generous to the point of double counting.
#: ``"hotkey_blocked"`` contains ``"key"``, so a refused hotkey used to be
#: counted as both a refused hotkey and a burst of keystrokes. A refused hotkey
#: is the *absence* of input, and counting it as input would let an agent trip
#: the keystroke signal by having its keystrokes refused. The refusal is
#: therefore logged as ``combo_refused``, which shares no fragment with the
#: other three sets.
CLICK_ACTIONS = ("click",)
KEYSTROKE_ACTIONS = ("key", "type")
APP_OPEN_ACTIONS = ("app_open",)
BLOCKED_HOTKEY_ACTIONS = ("combo_refused",)


class Watchdog:
    """Detects runaway work and trips the kill switch.

    Attributes:
        switch: The kill switch this watchdog trips.
        scope: Stop scope, used to carry the stop out; optional.
        pids: Processes inspected each poll. Empty means this process.
        logger: Activity logger, read for deletions and written for detections.
        cpu_percent: CPU threshold for a single poll.
        cpu_streak_limit: Consecutive saturated polls needed to trip.
        deletion_limit: Deletions inside the window that trip the watchdog.
    """

    def __init__(
        self,
        switch: Any,
        scope: Any = None,
        pids: Optional[List[int]] = None,
        logger: Any = None,
        cpu_percent: float = 95.0,
        cpu_streak_limit: int = CPU_STREAK_LIMIT,
        deletion_limit: int = DELETION_LIMIT,
        click_limit: int = MASS_CLICK_LIMIT,
        keystroke_limit: int = MASS_KEYSTROKE_LIMIT,
        app_open_limit: int = MASS_APP_OPEN_LIMIT,
        blocked_hotkey_limit: int = BLOCKED_HOTKEY_LIMIT,
    ) -> None:
        """Configure the watchdog.

        Args:
            switch: The kill switch to trip.
            scope: Stop scope used to carry the stop out, when available.
            pids: Processes to inspect; defaults to this process.
            logger: Activity logger for deletion counting.
            cpu_percent: CPU percentage counted as saturated.
            cpu_streak_limit: Consecutive saturated polls needed to trip.
            deletion_limit: Deletions inside the window that trip it.
            click_limit: Clicks inside the click window that trip it.
            keystroke_limit: Keystrokes inside the keystroke window that trip it.
            app_open_limit: Application launches inside the window that trip it.
            blocked_hotkey_limit: Refused hotkeys inside the window that trip it.
        """
        import os

        self.switch = switch
        self.scope = scope
        self.pids = list(pids) if pids else [os.getpid()]
        self.logger = logger
        self.cpu_percent = cpu_percent
        self.cpu_streak_limit = cpu_streak_limit
        self.deletion_limit = deletion_limit
        self.click_limit = click_limit
        self.keystroke_limit = keystroke_limit
        self.app_open_limit = app_open_limit
        self.blocked_hotkey_limit = blocked_hotkey_limit
        self._streak = 0
        self.trips = 0

    def poll(self) -> Dict[str, Any]:
        """Check one time and trip the switch if any signal fires.

        Returns:
            A report with ``tripped``, the reason, and every measured value.
            The rate keys are always present, so a dashboard can show a click
            or keystroke rate that is *not* currently tripping.
        """
        cpu = self.max_cpu_percent()
        deletions = self.recent_deletions()
        clicks = self.recent_clicks()
        keystrokes = self.recent_keystrokes()
        app_opens = self.recent_app_opens()
        blocked_hotkeys = self.recent_blocked_hotkeys()
        self._streak = self._streak + 1 if cpu >= self.cpu_percent else 0
        tripped = (
            self._streak >= self.cpu_streak_limit
            or deletions >= self.deletion_limit
            or clicks >= self.click_limit
            or keystrokes >= self.keystroke_limit
            or app_opens >= self.app_open_limit
            or blocked_hotkeys >= self.blocked_hotkey_limit
        )
        rates = {
            "clicks_in_window": clicks,
            "keystrokes_in_window": keystrokes,
            "app_opens_in_window": app_opens,
            "blocked_hotkeys_in_window": blocked_hotkeys,
        }
        report = {
            "tripped": tripped,
            "cpu_percent": cpu,
            "cpu_streak": self._streak,
            "deletions_in_window": deletions,
            **rates,
            "reason": self._reason(cpu, deletions, rates),
            "trips": self.trips,
        }
        if tripped:
            self.trip(report["reason"])
            report["trips"] = self.trips
        return report

    def trip(self, reason: str) -> Dict[str, Any]:
        """Engage the kill switch for a watchdog detection.

        Args:
            reason: What was detected, recorded in the audit log.

        Returns:
            The resulting switch status.
        """
        self.trips += 1
        if self.scope is not None:
            self.scope.engage(reason=reason, source=SOURCE_WATCHDOG)
            return self.switch.status()
        return self.switch.trigger(source=SOURCE_WATCHDOG, reason=reason)

    def _reason(
        self, cpu: float, deletions: int, rates: Dict[str, int]
    ) -> str:
        """Describe why the watchdog would trip, for the UI and the audit log.

        CPU is reported first and deletion second, unchanged from before the
        desktop signals existed, so an audit reader comparing reports across
        versions sees the same reason for the same cause.
        """
        if self._streak >= self.cpu_streak_limit:
            return f"watchdog: cpu saturated at {cpu:.0f}% for {self._streak} polls"
        if deletions >= self.deletion_limit:
            return f"watchdog: {deletions} file deletions in {int(DELETION_WINDOW_SECONDS)}s"
        if rates["clicks_in_window"] >= self.click_limit:
            return (
                f"watchdog: {rates['clicks_in_window']} clicks in "
                f"{int(CLICK_WINDOW_SECONDS)}s"
            )
        if rates["keystrokes_in_window"] >= self.keystroke_limit:
            return (
                f"watchdog: {rates['keystrokes_in_window']} keystrokes in "
                f"{int(KEYSTROKE_WINDOW_SECONDS)}s"
            )
        if rates["app_opens_in_window"] >= self.app_open_limit:
            return (
                f"watchdog: {rates['app_opens_in_window']} app launches in "
                f"{int(APP_OPEN_WINDOW_SECONDS)}s"
            )
        if rates["blocked_hotkeys_in_window"] >= self.blocked_hotkey_limit:
            return (
                f"watchdog: {rates['blocked_hotkeys_in_window']} refused "
                f"hotkeys in {int(HOTKEY_WINDOW_SECONDS)}s"
            )
        return ""

    def max_cpu_percent(self) -> float:
        """Return the highest CPU use across the watched processes.

        A process that has exited is skipped rather than treated as saturated.
        """
        import psutil

        highest = 0.0
        for pid in self.pids:
            try:
                highest = max(highest, psutil.Process(pid).cpu_percent(None))
            except psutil.Error:
                continue
        return round(highest, 1)

    def recent_deletions(self, now: Optional[float] = None) -> int:
        """Count delete actions in the recent window.

        Args:
            now: Override for the current time, used by tests.

        Returns:
            How many deletions were logged inside the window.
        """
        return self._count("delete", DELETION_WINDOW_SECONDS, now)

    def recent_clicks(self, now: Optional[float] = None) -> int:
        """Count mouse clicks in the recent window.

        Args:
            now: Override for the current time, used by tests.

        Returns:
            How many click actions were logged inside the window.
        """
        return self._count(CLICK_ACTIONS, CLICK_WINDOW_SECONDS, now)

    def recent_keystrokes(self, now: Optional[float] = None) -> int:
        """Count keystrokes in the recent window.

        Counts typing as well as key presses and hotkeys: all three are
        "the agent is putting keys into the machine", which is the thing
        worth bounding.

        Args:
            now: Override for the current time, used by tests.

        Returns:
            How many keystroke actions were logged inside the window.
        """
        return self._count(KEYSTROKE_ACTIONS, KEYSTROKE_WINDOW_SECONDS, now)

    def recent_app_opens(self, now: Optional[float] = None) -> int:
        """Count application launches in the recent window.

        Args:
            now: Override for the current time, used by tests.

        Returns:
            How many launches were logged inside the window.
        """
        return self._count(APP_OPEN_ACTIONS, APP_OPEN_WINDOW_SECONDS, now)

    def recent_blocked_hotkeys(self, now: Optional[float] = None) -> int:
        """Count refused hotkey attempts in the recent window.

        One refusal means the blocklist worked. A run of them means something
        is trying combinations until one gets through, which is the shape of
        an attack rather than a user.

        Args:
            now: Override for the current time, used by tests.

        Returns:
            How many refused hotkeys were logged inside the window.
        """
        return self._count(BLOCKED_HOTKEY_ACTIONS, HOTKEY_WINDOW_SECONDS, now)

    def _count(
        self,
        fragments: Any,
        window: float,
        now: Optional[float] = None,
    ) -> int:
        """Count log records whose action matches, inside a time window.

        Args:
            fragments: One substring, or several; a record matches when its
                action contains any of them.
            window: How far back to look, in seconds.
            now: Override for the current time, used by tests.

        Returns:
            How many matching records were logged inside the window.
        """
        if self.logger is None:
            return 0
        wanted = (fragments,) if isinstance(fragments, str) else tuple(fragments)
        cutoff = (now if now is not None else time.time()) - window
        count = 0
        for record in self.logger.tail(1000):
            action = str(record.get("action", ""))
            if not any(fragment in action for fragment in wanted):
                continue
            stamp = self._record_time(record)
            if stamp is not None and stamp >= cutoff:
                count += 1
        return count

    @staticmethod
    def _record_time(record: Dict[str, Any]) -> Optional[float]:
        """Return a log record's timestamp as a Unix time, when it has one."""
        stamp = record.get("timestamp")
        if isinstance(stamp, (int, float)):
            return float(stamp)
        if not isinstance(stamp, str):
            return None
        from datetime import datetime

        try:
            return datetime.fromisoformat(stamp).timestamp()
        except ValueError:
            return None
