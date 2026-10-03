"""Spinal Cord — Kill Switch.

The in-process half of the emergency stop. Reads a flag file rather than holding
state in memory, for one reason: **the flag must survive the brain restarting**.
If ZUZZ crashes while stopped and comes back up, it has to come back stopped. An
in-memory boolean would quietly re-arm the machine.

Three properties this module is responsible for:

* **Everyone denies.** :meth:`KillSwitch.is_killed` is checked before any action,
  and while it is true every action is denied without further checks — no matrix
  lookup, no threat scoring, no confirmation dialog that could be clicked through.
* **Nothing self-heals.** The flag is only cleared by :meth:`KillSwitch.reset`,
  which requires an explicit human confirmation. An agent that could un-kill
  itself could recover from the runaway loop that triggered the stop.
* **Fail safe.** If a reset cannot delete a flag, the switch stays engaged and
  the refusal says so, rather than reporting a stop that is no longer in force.

The pieces are split so each file stays under the 200-line limit and so the
confirmation rules can be reviewed on their own:

* :mod:`brain.spinal_cord.stop_state` — flag locations, file formats, cooldowns.
* :mod:`brain.spinal_cord.reset_policy` — the rules for re-arming the machine.
* :mod:`brain.spinal_cord.stop_scope` — what the stop actually stops.
* :mod:`brain.spinal_cord.watchdog` — automatic detection of runaway work.
* :mod:`brain.spinal_cord.emergency_stop` — the out-of-process hotkey.

Decisions applied: progressive reset cooldown (Q11), state persistence across
restarts (Priority 4), no panic mode (Q14).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from brain.spinal_cord.reset_policy import (
    RESET_WORD,
    ResetRefused,
    check_cooldown,
    check_confirmation,
    check_flags_cleared,
    cooldown_remaining,
    survivors_of,
)
from brain.spinal_cord.stop_state import (
    SOURCES,
    SOURCE_UI,
    STATE_NAME,
    clear_flags,
    flag_paths,
    remaining_flags,
    write_flags,
)
from brain.spinal_cord.switch_status import SwitchStatus
from core.audit import KILL_SWITCH, AuditLog, get_audit_log

__all__ = ["RESET_WORD", "KillSwitch", "ResetRefused", "get_kill_switch"]


class KillSwitch(SwitchStatus):
    """The emergency stop, as consulted by the gate on every action.

    Attributes:
        paths: Flag locations; the first is the canonical one.
        state_path: Where the explanation and reset history are kept.
        audit: Where activations and resets are recorded.
    """

    def __init__(
        self,
        paths: Optional[List[Path]] = None,
        audit: Optional[AuditLog] = None,
    ) -> None:
        """Bind the switch to its flag locations.

        Args:
            paths: Override for the flag locations, used by tests.
            audit: Override for the audit log, used by tests.
        """
        self.paths = [Path(p) for p in paths] if paths else flag_paths()
        self.state_path = self.state_path_for()
        self.audit = audit if audit is not None else get_audit_log()

    # -- state ------------------------------------------------------------

    def is_killed(self) -> bool:
        """Return True while the emergency stop is engaged.

        Checked before every action. While true, nothing executes.
        """
        return bool(remaining_flags(self.paths))

    # -- engaging ---------------------------------------------------------

    def trigger(
        self,
        source: str = SOURCE_UI,
        reason: str = "",
        last_action: str = "",
    ) -> Dict[str, Any]:
        """Engage the emergency stop.

        Idempotent: a second trigger does not overwrite the original cause,
        because the *first* reason is the one worth reading in an audit.

        Args:
            source: One of :data:`~brain.spinal_cord.stop_state.SOURCES`.
            reason: What caused the stop.
            last_action: The action in flight when it stopped, if any.

        Returns:
            The status after triggering.

        Raises:
            ValueError: If the source is not a recognised trigger.
        """
        if source not in SOURCES:
            raise ValueError(f"unknown trigger source: {source!r}")
        if self.is_killed():
            return self.status()
        state = {"ts": time.time(), "source": source, "reason": reason, "last_action": last_action}
        written = write_flags(self.paths, state)
        self.write_state(state)
        self.audit.record(
            KILL_SWITCH,
            reason or "emergency stop engaged",
            level="forbidden",
            source=source,
            reason=reason,
            last_action=last_action,
            flags_written=len(written),
        )
        return self.status()

    # -- resetting --------------------------------------------------------

    def cooldown_remaining(self) -> int:
        """Return seconds until the next reset is permitted.

        Returns:
            Seconds left, or 0 when a reset is allowed right now.
        """
        return cooldown_remaining(self.recent_resets())

    def reset(self, typed_word: str = "", confirmed: bool = False) -> Dict[str, Any]:
        """Disengage the emergency stop. Requires an explicit human confirmation.

        Args:
            typed_word: What the user typed; must equal :data:`RESET_WORD`.
            confirmed: The user clicked confirm.

        Returns:
            The status after resetting.

        Raises:
            ResetRefused: When the confirmation or cooldown is not satisfied, or
                when a flag file survives and the machine must stay stopped.
        """
        if not self.is_killed():
            return self.status()
        check_confirmation(typed_word, confirmed)
        check_cooldown(self.recent_resets())

        cleared = clear_flags(self.paths)
        check_flags_cleared(self.paths, survivors_of(self.paths, cleared))

        resets = self.recent_resets()
        self.write_state(
            {
                "resets": resets + [time.time()],
                "source": "",
                "reason": "",
                "last_action": "",
            }
        )
        self.audit.record(
            KILL_SWITCH,
            "emergency stop reset by user",
            source=SOURCE_UI,
            typed_word=RESET_WORD,
            resets_this_hour=len(resets) + 1,
        )
        return self.status()


_SWITCH: Optional[KillSwitch] = None


def get_kill_switch() -> KillSwitch:
    """Return the process-wide kill switch singleton."""
    global _SWITCH
    if _SWITCH is None:
        _SWITCH = KillSwitch()
    return _SWITCH
