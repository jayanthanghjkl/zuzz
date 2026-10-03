"""Spinal Cord — Stop State.

Where the stop flag lives and how it is written. Split out of `kill_switch.py`
so both files stay under the 200-line limit and so the standalone emergency-stop
process can write the flag without importing the whole safety layer.

Three things live here:

* **Two locations.** The flag is written to the project storage directory and to
  the system temp directory (decision Q10), so one deleted or locked file cannot
  disarm the switch.
* **JSON, not an empty file.** The flag carries the timestamp, the source and the
  reason, so the UI can explain *why* ZUZZ stopped instead of just that it did.
* **Never fatal.** Writing one location may fail — a locked file, a read-only
  temp directory. That must not prevent the other location from arming, and it
  must not crash whatever triggered the stop.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from core.config import ROOT

#: Filename used in both locations.
FLAG_NAME = "stop.flag"

#: Canonical state file, written next to the primary flag.
STATE_NAME = "kill_switch.json"

#: Progressive cooldown: repeated resets within the window get longer waits.
COOLDOWN_WINDOW_SECONDS = 3600.0
COOLDOWN_LADDER = (0, 5, 30, 60)

#: Where a stop came from. Recorded so the UI can tell a hotkey press from the
#: watchdog tripping on a runaway.
SOURCE_HOTKEY = "hotkey"
SOURCE_WATCHDOG = "watchdog"
SOURCE_UI = "ui"
SOURCE_PROCESS = "process"

SOURCES = (SOURCE_HOTKEY, SOURCE_WATCHDOG, SOURCE_UI, SOURCE_PROCESS)


def flag_paths() -> List[Path]:
    """Return both flag locations.

    Returns:
        The project storage path and the system temp path. Both are written, so
        neither is a single point of failure.
    """
    return [ROOT / "storage" / FLAG_NAME, Path(tempfile.gettempdir()) / "zuzz_stop.flag"]


def read_json(path: Path) -> Dict[str, Any]:
    """Read a JSON file, returning an empty dict when unusable.

    A corrupt or truncated state file must not stop the switch from reporting:
    the flag files themselves are what decide whether ZUZZ is stopped, and this
    is only the explanation attached to them.

    Args:
        path: File to read.

    Returns:
        The parsed mapping, or ``{}`` when missing or malformed.
    """
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, payload: Dict[str, Any]) -> bool:
    """Write a JSON file, reporting failure instead of raising.

    Args:
        path: File to write.
        payload: Mapping to serialise.

    Returns:
        True when the file was written.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def write_flags(paths: List[Path], payload: Dict[str, Any]) -> List[Path]:
    """Write the payload to every flag location, tolerating partial failure.

    Args:
        paths: Flag locations.
        payload: State to record in each one.

    Returns:
        The paths that were actually written.
    """
    written = [path for path in paths if write_json(path, payload)]
    return written


def clear_flags(paths: List[Path]) -> List[Path]:
    """Remove every flag file, tolerating one that cannot be deleted.

    A flag that survives a reset is the dangerous direction, so the caller is
    expected to check :func:`remaining_flags` afterwards and refuse if any are
    left in place.

    Args:
        paths: Flag locations to clear.

    Returns:
        The paths that no longer exist.
    """
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue
    return [path for path in paths if not path.exists()]


def remaining_flags(paths: List[Path]) -> List[Path]:
    """Return the flag locations that still exist."""
    return [path for path in paths if path.exists()]


def cooldown_seconds(reset_count: int) -> int:
    """Return the wait imposed on the reset after ``reset_count`` recent ones.

    Progressive rather than fixed: the first reset is immediate, and resets
    repeated within the hour wait 5s, then 30s, then 60s (decision Q11). A
    toggle loop is slowed without penalising normal use.

    Args:
        reset_count: How many resets happened inside the cooldown window.

    Returns:
        Seconds the user must wait.
    """
    if reset_count <= 0:
        return 0
    return COOLDOWN_LADDER[min(reset_count, len(COOLDOWN_LADDER) - 1)]
