"""Spinal Cord — Switch Status.

Reads the switch's on-disk state and turns it into the payload the UI and the
API render. Split from :mod:`brain.spinal_cord.kill_switch` so both files stay
under the 200-line limit.

Purely a reader: nothing here engages, clears or records anything. That matters
because the status endpoint is the one thing the dashboard calls on a timer, and
it must not be able to change the machine's state by being polled.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List

from brain.spinal_cord.reset_policy import RESET_WORD, approval_requirements
from brain.spinal_cord.stop_state import (
    COOLDOWN_WINDOW_SECONDS,
    STATE_NAME,
    read_json,
    remaining_flags,
)


class SwitchStatus:
    """Status and state-file reading for :class:`~kill_switch.KillSwitch`.

    Expects the host class to provide ``paths`` and ``state_path``, and to have
    ``reset_policy`` imported for :meth:`cooldown_remaining`.
    """

    paths: List[Path]
    state_path: Path

    def state_path_for(self) -> Path:
        """Return where the state file lives, given the first flag location."""
        return self.paths[0].parent / STATE_NAME

    def status(self) -> Dict[str, Any]:
        """Return the switch's full state for the UI and the API.

        Returns:
            Whether it is engaged, why, when, from where, what a reset would
            need, and any reset timer. While stopped, every explanation field is
            populated; while running, they are blank so a stale reason from the
            last stop cannot be mistaken for the current one.
        """
        state = read_json(self.state_path)
        engaged = bool(remaining_flags(self.paths))
        cooldown = self.cooldown_remaining()  # type: ignore[attr-defined]
        needs_confirm, instruction = approval_requirements(cooldown)
        return {
            "engaged": engaged,
            "source": state.get("source", "") if engaged else "",
            "reason": state.get("reason", "") if engaged else "",
            "last_action": state.get("last_action", "") if engaged else "",
            "since": state.get("ts") if engaged else None,
            "flag_paths": [str(path) for path in self.paths],
            "present_at": [str(path) for path in remaining_flags(self.paths)],
            "cooldown_remaining": cooldown,
            "resets_this_hour": len(self.recent_resets()),
            "reset_word": RESET_WORD,
            "reset_requires_confirmation": needs_confirm if engaged else False,
            "reset_instruction": instruction if engaged else "",
        }

    def recent_resets(self) -> List[float]:
        """Return reset timestamps inside the cooldown window.

        Timestamps outside the window are dropped, so a reset from yesterday
        cannot make today's cooldown indefinite.
        """
        cutoff = time.time() - COOLDOWN_WINDOW_SECONDS
        stamps = read_json(self.state_path).get("resets", [])
        return [float(s) for s in stamps if isinstance(s, (int, float)) and s >= cutoff]

    def write_state(self, updates: Dict[str, Any]) -> None:
        """Merge updates into the state file next to the primary flag.

        Written through :func:`~brain.spinal_cord.stop_state.write_json`, which
        reports failure instead of raising: the stop flag itself is what decides
        whether ZUZZ is stopped, so a state file that cannot be written must not
        take the stop down with it.
        """
        from brain.spinal_cord.stop_state import write_json

        payload = read_json(self.state_path)
        payload.update(updates)
        write_json(self.state_path, payload)
