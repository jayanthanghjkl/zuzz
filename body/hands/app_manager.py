"""Body/Hands — applications and processes.

Opening applications, closing them, and looking at what is running.

**Resolution happens before classification.** The whitelist maps a name to an
executable, and the gate is asked about the executable. An alias therefore
cannot smuggle in something different, and two names for the same application
get the same answer.

**Unknown is not forbidden.** An application that is not on the whitelist can
still be opened — it maps to ``run_safe_commands``, which asks at every trust
level. Removing an entry from the whitelist removes an auto-approval, not an
ability. Blocking is a separate decision and lives in ``never_launch``, which is
a refusal at every level.

**Closing is not killing.** :meth:`AppManager.close_app` posts ``WM_CLOSE``, so
the application gets its chance to save, prompt, or cancel.
:meth:`AppManager.kill_process` terminates immediately and skips all of that.
Both land in ``close_apps``, which asks at every level: either can lose work the
user never saved, and neither is what a person means when they say "close that
tab".

**System processes are never signalled.** Not at any trust level, not with a
valid decision. ZUZZ is not asked whether it may end Explorer; it does not end
Explorer. See :func:`body.hands.window_guards.guard_process`.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, List, Optional

from body.hands import action_classifier, app_registry, window_guards
from body.hands.exceptions import TargetNotFoundRefused
from body.hands.hands_guards import verify_decision


class AppManager:
    """Applications and processes, behind the gate.

    Attributes:
        logger: Activity logger, which is also what the watchdog reads.
        launcher: Callable that starts a command; replaced by tests.
    """

    def __init__(
        self,
        kill_switch: Optional[Any] = None,
        logger: Optional[Any] = None,
        launcher: Optional[Any] = None,
        elevated: bool = False,
    ) -> None:
        """Wire the app manager to its collaborators.

        Args:
            kill_switch: The switch to re-check on every call.
            logger: Activity logger; None means no logging.
            launcher: Callable taking a command and starting it. Defaults to
                :func:`subprocess.Popen`; replaced by tests so nothing is
                actually launched.
            elevated: True when ZUZZ itself is elevated.
        """
        self.kill_switch = kill_switch
        self.logger = logger
        self.launcher = launcher or launch
        self.elevated = elevated

    # -- read --------------------------------------------------------------

    def list_running(self, decision: Any) -> List[Dict[str, Any]]:
        """Return the processes currently running.

        Args:
            decision: A ``read_files`` decision.

        Returns:
            A list of ``{"pid", "name", "protected"}``. Protected processes are
            listed rather than hidden: the user can see what is on the machine,
            and the flag says which of it ZUZZ will never touch.
        """
        verify_decision(
            decision,
            action_classifier.category_for("list_running_apps"),
            str(decision.action.target),
            self.kill_switch,
        )
        running = running_processes()
        self._log("app_list", count=len(running))
        return running

    # -- launch ------------------------------------------------------------

    def open_app(self, decision: Any, name: str) -> Dict[str, Any]:
        """Launch an application.

        Args:
            decision: An ``open_apps`` decision for a whitelisted application,
                or ``run_safe_commands`` for any other.
            name: What the user asked for, e.g. ``"chrome"``.

        Returns:
            The resolved executable, the category it was decided under, and the
            pid if the launcher reported one.

        Raises:
            ProtectedRefused: The resolved command is on the never-launch list.
                Checked before the decision, because a decision to launch an
                encoded PowerShell script is not a decision to be obeyed.
            HandsRefused: The decision does not permit this launch.
        """
        resolved = action_classifier.resolve_target("app", name)
        verify_decision(
            decision, resolved["category"], resolved["executable"], self.kill_switch
        )
        process = self.launcher(resolved["executable"])
        self._log(
            "app_open",
            requested=resolved["requested"],
            executable=resolved["executable"],
            whitelisted=resolved["whitelisted"],
        )
        return dict(resolved, pid=getattr(process, "pid", None))

    # -- close -------------------------------------------------------------

    def close_app(self, decision: Any, name: str) -> Dict[str, Any]:
        """Ask an application to close, the way its close button would.

        Args:
            decision: A ``close_apps`` decision, which asks at every level.
            name: A window title fragment, a process name, or a window handle.

        Returns:
            The windows that were asked to close.

        Raises:
            TargetNotFoundRefused: Nothing running matched.
            ProtectedProcessRefused: A match is a system process.
            HandsRefused: The decision does not permit closing anything.
        """
        target = str(name or "").strip()
        if not target:
            raise TargetNotFoundRefused("a window title or process name is required")
        verify_decision(
            decision, action_classifier.app_close_category(), target, self.kill_switch
        )
        from body.hands import window_manager, window_ops

        matched = _matching_windows(target, window_manager.list_windows())
        if not matched:
            raise TargetNotFoundRefused(
                f"nothing running matches {name!r}, so there is nothing to close"
            )
        closed: List[int] = []
        for window in matched:
            window_guards.guard_process(window.get("process", ""), window.get("pid"))
            if window_ops.close_window(window["hwnd"]):
                closed.append(window["hwnd"])
        self._log("app_close", requested=name, windows=closed)
        return {"requested": name, "closed": closed}

    def kill_process(self, decision: Any, pid: int) -> Dict[str, Any]:
        """Terminate a process immediately.

        Unlike :meth:`close_app` this gives the application no chance to save,
        which is why it is a separate operation and why the refusal to do it to a
        system process is absolute.

        Args:
            decision: A ``close_apps`` decision, which asks at every level.
            pid: The process id.

        Returns:
            The pid that was signalled.

        Raises:
            ProtectedProcessRefused: The process is the system, not the user, or
                the pid is 0 or 4.
            TargetNotFoundRefused: No such process is running.
        """
        verify_decision(
            decision, action_classifier.app_close_category(), str(pid), self.kill_switch
        )
        name = process_name_of(int(pid))
        window_guards.guard_process(name, int(pid))
        try:
            import psutil

            psutil.Process(int(pid)).terminate()
        except Exception as exc:
            raise TargetNotFoundRefused(
                f"could not terminate pid {pid} ({name or 'unknown process'})"
            ) from exc
        self._log("process_kill", pid=int(pid), name=name)
        return {"pid": int(pid), "name": name}

    def _log(self, action: str, **fields: Any) -> None:
        """Record one application action, when a logger is wired.

        ``app_open`` is the name the watchdog counts for spawn loops; see
        ``brain.spinal_cord.watchdog``.
        """
        if self.logger is not None:
            self.logger.log("hands", action, **fields)


def launch(command: str) -> Any:
    """Start an application without a console window of its own.

    Args:
        command: The resolved executable — a bare name from the whitelist, or
            whatever the user typed for an unknown application.

    Returns:
        The started process or None when launched via shell/startfile.
    """
    try:
        return subprocess.Popen(command, shell=False)
    except (FileNotFoundError, OSError):
        if hasattr(os, "startfile"):
            os.startfile(command)
            return None
        raise


def process_name_of(pid: int) -> str:
    """Return a process name for a pid, or an empty string."""
    from body.hands.window_manager import process_name

    return process_name(pid)


def running_processes() -> List[Dict[str, Any]]:
    """Return every running process, flagged for whether it is protected."""
    try:
        import psutil
    except Exception:
        return []
    running: List[Dict[str, Any]] = []
    for process in psutil.process_iter(["pid", "name"]):
        try:
            name = str(process.info.get("name") or "")
            pid = int(process.info["pid"])
        except Exception:
            continue
        running.append(
            {"pid": pid, "name": name, "protected": app_registry.is_protected_process(name, pid)}
        )
    return running


def _matching_windows(target: str, windows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return the windows a close request names.

    Three ways to name a window, checked in that order: an exact handle, then an
    exact process name, then a title fragment. Handles and process names are
    exact because a partial match on either closes something the user did not
    name.

    Args:
        target: What the caller asked to close.
        windows: The windows to search.

    Returns:
        Every matching window.
    """
    needle = str(target).strip()
    lowered = needle.lower()
    by_handle = [w for w in windows if str(w.get("hwnd")) == needle]
    if by_handle:
        return by_handle
    by_process = [w for w in windows if str(w.get("process", "")).lower() == lowered]
    if by_process:
        return by_process
    bare = lowered.removesuffix(".exe")
    return [
        w
        for w in windows
        if lowered in str(w.get("title", "")).lower()
        or str(w.get("process", "")).lower().removesuffix(".exe") == bare
    ]