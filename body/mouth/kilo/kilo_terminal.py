"""Body/Mouth/Kilo — command execution.

Runs a developer toolchain — ``pytest``, ``pip``, ``git`` — inside a workspace, and
nothing else. Four rules, each because the alternative has ended badly somewhere:

* **Never ``shell=True``.** Commands are argument vectors. With a shell, a package
  name containing ``;`` is a second command, and the allowlist above it pattern-matched
  the wrong string entirely.
* **A closed allowlist of executables, by name.** An allowlist of *arguments* is a
  suggestion; an allowlist of *commands* is the only version of this that is a boundary.
* **Absolute paths in the arguments are refused.** A test runner handed
  ``C:\\Windows\\System32`` as its first argument is not running tests.
* **A timeout, always.** A suite that hangs must not hold the permission prompt open,
  and an unreaped subprocess tree is how a machine ends up with forty orphaned pytest
  runs.

stdout and stderr are captured separately and both kept: a command's failures are
almost entirely on stderr, and an executor returning only stdout reports every failing
test as a success.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from body.mouth.kilo.exceptions import (
    CommandRefused,
    CommandTimedOut,
    DecisionRefused,
    KillSwitchRefused,
)

__all__ = ["CommandResult", "Terminal", "get_terminal"]

#: Seconds a command gets before it is stopped.
DEFAULT_TIMEOUT = 300

#: Bytes of stdout or stderr kept. Enough for a full pytest run; small enough that a
#: runaway loop cannot exhaust memory.
MAX_CAPTURE = 2_000_000

#: The executables this module will run, by exact name. A command not on this list is
#: refused even at trust level 5, because the list is the boundary.
ALLOWED = frozenset({
    "python", "python3", "py", "pytest", "pip", "git", "ruff", "black", "mypy",
    "node", "npm",
})

#: One pytest result line: ``tests/test_app.py::test_hello PASSED``.
PYTEST_LINE = re.compile(
    r"^(?P<nodeid>\S+?)(?:\s+-\s+.*?)?\s+"
    r"(?P<outcome>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)(?:\s+\[[^\]]*\])?\s*$"
)


@dataclass
class CommandResult:
    """What one command did.

    Attributes:
        command: The argument vector, as run.
        returncode: The process exit status.
        stdout: Captured standard output.
        stderr: Captured standard error.
        seconds: How long it ran.
        timed_out: Whether it was stopped.
        cwd: The directory it ran in.
        parsed: Structured results, when the output could be parsed.
    """

    command: List[str]
    returncode: int
    stdout: str
    stderr: str
    seconds: float
    timed_out: bool = False
    cwd: str = ""
    parsed: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Return True when the command succeeded on its own terms."""
        return self.returncode == 0 and not self.timed_out

    def to_dict(self, include_output: bool = True) -> Dict[str, Any]:
        """Return the result as plain data.

        Args:
            include_output: Include stdout, stderr and a combined tail. False for a
                log line.
        """
        record: Dict[str, Any] = {
            "command": list(self.command), "returncode": self.returncode,
            "ok": self.ok, "seconds": round(self.seconds, 2),
            "timed_out": self.timed_out, "cwd": self.cwd,
        }
        if self.parsed:
            record["parsed"] = dict(self.parsed)
        if include_output:
            record["stdout"] = self.stdout
            record["stderr"] = self.stderr
            record["output"] = (self.stdout + self.stderr)[-200_000:]
        return record


class Terminal:
    """Runs allowlisted tools inside a workspace."""

    def __init__(self, logger: Optional[Any] = None, kill_switch: Optional[Any] = None) -> None:
        """Build the executor.

        Args:
            logger: Activity logger.
            kill_switch: The emergency stop, checked before every run.
        """
        self.logger = logger
        self.kill_switch = kill_switch

    def limits(self) -> Dict[str, Any]:
        """Return the execution policy. Not gated; a gauge.

        A user being asked to approve a command deserves to see what commands are
        possible before they approve the first one.
        """
        return {"allowed": sorted(ALLOWED), "timeout_seconds": DEFAULT_TIMEOUT,
                "max_capture_bytes": MAX_CAPTURE, "shell": False}

    def check(self, argv: Sequence[str]) -> List[str]:
        """Validate a command without running it. Not gated; a preview.

        Args:
            argv: The argument vector.

        Returns:
            The vector as a clean list of strings.

        Raises:
            CommandRefused: The vector is empty, the executable is not on the
                allowlist, or an argument is an absolute path or climbs out.
        """
        parts = [str(item) for item in argv if str(item).strip()]
        if not parts:
            raise CommandRefused("no command was given")
        program = os.path.basename(parts[0].strip().strip('"')).lower()
        if program.endswith(".exe"):
            program = program[:-4]
        if program not in ALLOWED:
            raise CommandRefused(
                f"{parts[0]!r} is not a tool the coding agents may run. Allowed: "
                + ", ".join(sorted(ALLOWED))
            )
        for part in parts[1:]:
            if re.match(r"^[A-Za-z]:", part) or part.startswith(("/", "\\")):
                raise CommandRefused(
                    f"{part!r} is an absolute path; the coding agents run tools with "
                    f"relative paths inside a workspace only"
                )
            if ".." in part.replace("\\", "/").split("/"):
                raise CommandRefused(f"{part!r} climbs out of the workspace")
        return parts

    def run(
        self, decision: Any, argv: Sequence[str], cwd: str, timeout: Optional[int] = None
    ) -> CommandResult:
        """Run one command in a workspace.

        Args:
            decision: An allowed ``execute_code`` decision. Required and checked for
                presence; the caller decided it.
            argv: The argument vector. Never a string.
            cwd: The workspace root, which must exist.
            timeout: Seconds before the process is stopped.

        Returns:
            What the command did, with stdout and stderr separate and a parsed pytest
            summary when the command was a test run.

        Raises:
            DecisionRefused: No decision was passed. Running a subprocess is the most
                direct way to touch the machine this package has, so an empty-handed
                caller is a bug rather than a shortcut.
            KillSwitchRefused: The stop is engaged.
            CommandRefused: The vector is not runnable, or the directory is not there.
            CommandTimedOut: The process exceeded its timeout and was stopped.
        """
        if decision is None:
            raise DecisionRefused(
                "running a command needs an execute_code decision the caller already holds"
            )
        if self.kill_switch is not None and self.kill_switch.is_killed():
            raise KillSwitchRefused("kill switch engaged; nothing will be executed")
        parts = self.check(argv)
        if not os.path.isdir(cwd):
            raise CommandRefused(f"{cwd} is not a directory, so nothing can run there")

        budget = max(1, min(int(timeout or DEFAULT_TIMEOUT), DEFAULT_TIMEOUT))
        started = time.monotonic()
        try:
            completed = subprocess.run(  # noqa: S603 - allowlisted program, no shell
                parts, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=budget, shell=False,
            )
            stdout, stderr, code, timed_out = completed.stdout, completed.stderr, completed.returncode, False
        except subprocess.TimeoutExpired as expired:
            stdout = expired.stdout or ""
            stderr = (expired.stderr or "") + f"\nstopped after {budget}s; it did not finish"
            code, timed_out = 124, True
        except OSError as exc:
            raise CommandRefused(f"{parts[0]} could not be started: {exc}") from exc

        result = CommandResult(
            command=parts, returncode=int(code), stdout=str(stdout)[:MAX_CAPTURE],
            stderr=str(stderr)[:MAX_CAPTURE], seconds=time.monotonic() - started,
            timed_out=timed_out, cwd=cwd,
        )
        result.parsed = self.parse(result)
        self._log("kilo_command", argv=parts, cwd=cwd, returncode=result.returncode,
                  seconds=round(result.seconds, 2), timed_out=timed_out)
        if timed_out:
            raise CommandTimedOut(f"{parts[0]} ran for {budget}s and was stopped",
                                  command=" ".join(parts), seconds=budget)
        return result

    def parse(self, result: CommandResult) -> Dict[str, Any]:
        """Pull structured results out of a command's output.

        Only pytest is parsed, and only because the Tester agent proposes pytest files
        and the Code page has to show *which* tests failed rather than a wall of
        output. Anything unrecognised comes back empty, which the UI renders as "no
        structured result" rather than as "passed".

        Args:
            result: The command result.

        Returns:
            Per-test outcomes and totals, or an empty dict.
        """
        if not result.command or "pytest" not in os.path.basename(result.command[0]):
            return {}
        tests: List[Dict[str, str]] = []
        for line in (result.stdout + "\n" + result.stderr).splitlines():
            match = PYTEST_LINE.match(line.strip())
            if match:
                tests.append({"nodeid": match.group("nodeid"),
                              "outcome": match.group("outcome")})
        if not tests:
            return {}
        counts: Dict[str, int] = {}
        for test in tests:
            counts[test["outcome"]] = counts.get(test["outcome"], 0) + 1
        return {"total": len(tests), "counts": counts, "tests": tests,
                "failed": [t["nodeid"] for t in tests
                           if t["outcome"] in ("FAILED", "ERROR")]}

    def _log(self, action: str, **fields: Any) -> None:
        """Record a run in the activity log, when wired."""
        if self.logger is None:
            return
        try:
            self.logger.log("kilo", action, **fields)
        except Exception:
            pass


_TERMINAL: Optional[Terminal] = None


def get_terminal(logger: Optional[Any] = None, kill_switch: Optional[Any] = None) -> Terminal:
    """Return the process-wide executor, building it on first use.

    Args:
        logger: Activity logger, used the first time only.
        kill_switch: The emergency stop.
    """
    global _TERMINAL
    if _TERMINAL is None:
        _TERMINAL = Terminal(logger=logger, kill_switch=kill_switch)
    return _TERMINAL
