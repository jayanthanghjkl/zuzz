"""Spinal Cord — Process Termination.

Stopping a process politely first, then forcefully. The graceful attempt lets the
brain flush its logs; the force fallback is what makes the two-second guarantee
real, because a frozen process does not respond to a polite request at all.

Isolated in its own module because it is the one piece of the Spinal Cord the
standalone emergency-stop process needs *without* pulling in the safety layer —
the whole point of that process is to work when the brain cannot.
"""

from __future__ import annotations


def terminate_brain(pid: int, force_after: float = 2.0) -> bool:
    """Terminate a process politely, then forcefully.

    Args:
        pid: Process id to terminate.
        force_after: Seconds before escalating to a hard kill.

    Returns:
        True when the process is gone, including when it never existed — a PID
        that has already exited is a stopped process, not a failure.
    """
    import psutil

    try:
        process = psutil.Process(pid)
    except psutil.Error:
        return True
    try:
        process.terminate()
        gone, _ = psutil.wait_procs([process], timeout=force_after)
        if gone:
            return True
        process.kill()
        psutil.wait_procs([process], timeout=force_after)
    except psutil.Error:
        return not psutil.pid_exists(pid)
    return not psutil.pid_exists(pid)
