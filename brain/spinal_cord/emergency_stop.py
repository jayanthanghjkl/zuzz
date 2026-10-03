"""Spinal Cord — Emergency Stop.

A standalone process, started before the brain, that listens for ``Ctrl+Shift+Q``
and stops ZUZZ. It has to be separate: if the brain is the thing that has gone
wrong — hung in a loop, spinning a CPU, deadlocked — then a hotkey handled
inside that process is a hotkey that will never fire.

What it does on the hotkey, in order:

1. Write the stop flag to both locations, so the brain denies everything from
   its next check even if it never receives the signal.
2. Terminate the brain's PID politely, then forcefully.

It deliberately imports only :mod:`brain.spinal_cord.stop_state` and
:mod:`brain.spinal_cord.terminate` — no gate, no audit log, no configuration
beyond the flag paths. Everything else is a dependency that could be the reason
the brain is unresponsive.

Run it directly::

    python -m brain.spinal_cord.emergency_stop --pid 1234

The front end also has a Stop button, because a global hotkey is easy to forget
while testing.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import List, Optional

from brain.spinal_cord.stop_state import SOURCE_HOTKEY, flag_paths, write_flags

#: The combination that stops ZUZZ from anywhere.
HOTKEY = "ctrl+shift+q"

#: How long to wait before escalating to a hard kill.
FORCE_AFTER = 2.0


def stop_now(pid: Optional[int] = None, reason: str = "") -> bool:
    """Write the stop flag and terminate the brain.

    Args:
        pid: The brain's process id; omit to only set the flag.
        reason: What caused the stop, recorded in the flag.

    Returns:
        True when the brain process is gone, or when only the flag was set.
    """
    payload = {"ts": time.time(), "source": SOURCE_HOTKEY, "reason": reason, "last_action": ""}
    write_flags(flag_paths(), payload)
    if pid is None:
        return True
    from brain.spinal_cord.terminate import terminate_brain

    return terminate_brain(pid, FORCE_AFTER)


def listen(pid: Optional[int] = None, hotkey: str = HOTKEY) -> None:
    """Block until the hotkey is pressed, then stop ZUZZ.

    Args:
        pid: The brain's process id to terminate.
        hotkey: The key combination to listen for.

    Raises:
        RuntimeError: If the ``keyboard`` package is not installed.
    """
    import keyboard

    print(f"ZUZZ emergency stop armed: press {hotkey} to stop the brain.")
    listener = keyboard.add_hotkey(hotkey, lambda: stop_now(pid, reason=f"{hotkey} pressed"))
    try:
        listener.join()
    except KeyboardInterrupt:
        pass
    finally:
        keyboard.remove_hotkey(listener)


def main(argv: Optional[List[str]] = None) -> int:
    """Parse arguments and run the listener.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description="ZUZZ global emergency stop.")
    parser.add_argument("--pid", type=int, default=None, help="PID of the brain process")
    parser.add_argument("--hotkey", default=HOTKEY, help=f"stop combination (default {HOTKEY})")
    parser.add_argument(
        "--once", action="store_true", help="stop immediately instead of listening"
    )
    args = parser.parse_args(argv)

    if args.once:
        return 0 if stop_now(args.pid, reason="emergency stop requested") else 1
    try:
        listen(args.pid, args.hotkey)
    except ImportError:
        print(
            "The 'keyboard' package is required for the hotkey. "
            "Install it, or use the Stop button in the UI.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
