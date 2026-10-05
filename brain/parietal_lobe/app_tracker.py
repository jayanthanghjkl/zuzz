"""Parietal Lobe — running applications and their resource use.

Answers "what is running", which is the question the Hypothalamus cannot answer on its
own: it reports machine-wide RAM, CPU and VRAM, but not *which application is holding
the RAM*. A machine at 91% with Moondream refusing to load has two very different
explanations — a browser with forty tabs, or the user's IDE — and they call for
opposite responses.

**System processes are excluded, and that is a privacy decision, not a tidiness one.**
A full process list on Windows is roughly 250 entries, of which about 200 are
``svchost.exe``, ``RuntimeBroker.exe`` and similar. Returning those to a model wastes
context on information the model cannot act on, and — more to the point — a process
list is a fingerprint of the machine. Reporting "this host runs Windows 11 with a
particular AV product and 40 Chrome renderers" to a cloud provider says more about the
user than any single window title. So:

* Only processes with a **visible window** are listed, plus any explicitly requested
  pid. Those are the applications a person would recognise as "running"; services are
  not applications.
* Processes without one are counted, and the count is reported as ``hidden_count`` with
  no detail. That keeps the answer honest — "37 processes have no window and were
  omitted" is a fact; "here are their names" is the disclosure.

The cross-reference with :mod:`brain.parietal_lobe.window_tracker` is the reason this
module takes a window list rather than enumerating twice: the set of pids that own a
window is already known, and matching against it is what makes "visible" mean the same
thing here as it does there.

Resource figures are sampled, not instantaneous. ``cpu_percent`` on first call returns
``0.0`` for every process because there is no prior sample to compare against — a
well-known ``psutil`` property that is routinely mistaken for "nothing is using any
CPU". This module therefore calls it once and discards that sample, so a caller gets
real numbers, and says so in the docstring rather than letting a user conclude their
machine is idle.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from brain.parietal_lobe.exceptions import SystemAwarenessUnavailable

#: Hard ceiling on how many processes one call returns. Not a safety limit — the
#: filtering above already keeps the list short — but a bound on what a model can be
#: handed in one answer, and one that stops a pathological process table from
#: producing a reply no local model can hold.
MAX_APPS = 50


class AppTracker:
    """Which applications are running, and what they are using.

    Attributes:
        process: Callable returning ``psutil.process_iter`` as ``(pid, name)``.
        usage: Callable returning ``{"cpu_percent", "memory_mb"}`` for a pid.
        windows: Callable returning the visible window list, used to decide which
            processes count as applications.
    """

    def __init__(
        self,
        process: Optional[Callable[[], Iterable[Any]]] = None,
        usage: Optional[Callable[[int], Dict[str, Any]]] = None,
        windows: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    ) -> None:
        """Wire the tracker.

        Args:
            process: Process enumerator, defaulting to ``psutil.process_iter``.
            usage: Per-pid usage reader, defaulting to ``psutil``.
            windows: Visible-window lister, defaulting to
                :func:`body.hands.window_manager.list_windows`. Injected so the region
                is testable with no display attached.
        """
        self.process = process if process is not None else _iter_processes
        self.usage = usage if usage is not None else _usage_for
        self.windows = windows if windows is not None else _list_windows
        #: How many processes the last :meth:`list` omitted for owning no window.
        #: Initialised here rather than only inside ``list`` so :meth:`summary` cannot
        #: read it before the first call and report 0 hidden processes on a machine
        #: running two hundred.
        self._hidden_count = 0

    def visible_pids(self) -> Dict[int, str]:
        """Return the pids that own at least one visible window.

        Returns:
            A mapping of pid to process name, from the window enumeration. Empty when
            the enumeration failed, which is *not* the same as "no applications are
            running" — callers get :meth:`list` with ``window_matched`` unavailable
            rather than a confident empty list.
        """
        from body.hands import window_manager

        try:
            windows = self.windows() or window_manager.list_windows() or []
        except Exception:
            return {}
        return {
            int(w["pid"]): str(w.get("process") or "")
            for w in windows
            if w.get("pid")
        }

    def list(
        self,
        include_hidden: bool = False,
        pids: Optional[Sequence[int]] = None,
        limit: int = MAX_APPS,
    ) -> List[Dict[str, Any]]:
        """Return the running applications.

        Args:
            include_hidden: Also list processes with no visible window. Off by
                default and gated behind an explicit argument, because the full list is
                the fingerprint described in the module docstring.
            pids: Return exactly these pids, ignoring the visibility filter. Used when
                a caller already knows the pid it wants — asking "what is process 4820"
                should not be answered with "it is hidden".
            limit: Maximum entries returned.

        Returns:
            One record per application with ``pid``, ``name``, ``cpu_percent``,
            ``memory_mb`` and ``has_window``.

        Raises:
            SystemAwarenessUnavailable: ``psutil`` is not installed, or the process
                table could not be read. Never returns an empty list in place of an
                error: "no applications are running" and "ZUZZ could not enumerate" are
                different claims, and the second one being reported as the first is how
                an agent talks a user out of a machine that is busy.
        """
        wanted = {int(pid) for pid in pids} if pids else set()
        owners = self.visible_pids()
        records: List[Dict[str, Any]] = []
        hidden = 0
        for pid, name in self._enumerate():
            has_window = pid in owners
            if pids:
                if pid not in wanted:
                    continue
            elif not has_window and not include_hidden:
                # Counted rather than skipped silently, and counted *before* the limit
                # is applied: a capped list that reported the omitted count as a
                # whole-machine figure would understate it by however many processes
                # came after the cut-off.
                hidden += 1
                continue
            if len(records) >= max(1, int(limit)):
                continue
            records.append(
                {
                    "pid": pid,
                    "name": name,
                    "has_window": has_window,
                    **self._usage(pid),
                }
            )
        records.sort(key=lambda record: float(record.get("memory_mb") or 0.0), reverse=True)
        self._hidden_count = hidden
        return records

    def summary(self) -> Dict[str, Any]:
        """Return the application count and the heaviest users.

        Returns:
            ``count`` (visible applications), ``hidden_count`` (processes omitted
            because they own no window), and ``top`` — the five largest by memory,
            which is the answer to "what is making this machine slow".
        """
        apps = self.list()
        total = len(apps)
        heaviest = sorted(
            apps, key=lambda record: float(record.get("memory_mb") or 0.0), reverse=True
        )[:5]
        return {
            "count": total,
            "hidden_count": max(0, getattr(self, "_hidden_count", 0)),
            "top": [
                {
                    "pid": app["pid"],
                    "name": app["name"],
                    "memory_mb": app.get("memory_mb"),
                    "cpu_percent": app.get("cpu_percent"),
                }
                for app in heaviest
            ],
        }

    def _enumerate(self) -> Iterable[Any]:
        """Return the process table, or refuse.

        Raises:
            SystemAwarenessUnavailable: The table could not be read.
        """
        try:
            return list(self.process())
        except ImportError as exc:
            raise SystemAwarenessUnavailable(
                "psutil is not installed, so running applications cannot be "
                "enumerated (pip install psutil==6.1.1)",
                detail="processes",
            ) from exc
        except Exception as exc:
            raise SystemAwarenessUnavailable(
                f"the process table could not be read: {exc}", detail="processes"
            ) from exc

    def _usage(self, pid: int) -> Dict[str, Any]:
        """Return one process's resource use.

        CPU is the awkward one. ``psutil`` computes percent-usage from the interval
        between two calls on the same process object, and returns ``0.0`` for the
        first — so a tracker that primes and immediately measures reports ``0.0`` for
        every process, which reads exactly like an idle machine. Sleeping between
        samples would block the request, so this instead reports ``None`` for a pid it
        has not seen before and the real figure from the next call on. ``None`` means
        "not sampled long enough yet"; ``0.0`` means "measured, genuinely idle", and
        the two are kept apart because only one of them is a claim about the machine.

        Args:
            pid: The process id.

        Returns:
            ``{"cpu_percent": float or None, "memory_mb": float}``. Both zeroed only
            when the process could not be measured at all — which happens when it
            exits mid-enumeration, and is normal on a busy machine.
        """
        try:
            return self.usage(pid)
        except Exception:
            return {"cpu_percent": None, "memory_mb": 0.0}


def _iter_processes() -> Iterable[Any]:
    """Return ``(pid, name)`` for every process.

    Returns:
        A list of ``(pid, name)`` pairs.

    Raises:
        SystemAwarenessUnavailable: ``psutil`` is not importable.
    """
    import psutil

    return [(int(proc.info["pid"]), str(proc.info.get("name") or "")) for proc in psutil.process_iter(["pid", "name"])]


def _usage_for(pid: int) -> Dict[str, Any]:
    """Return one process's CPU and memory use, priming CPU on first sight.

    ``psutil`` reports percent-usage over the gap between two calls on the same
    process, so the first call is always ``0.0``. Reporting that as a figure would be
    the exact defect this module's docstring warns about — a plausible number that is
    really a placeholder — so the first call for a pid returns ``None`` and re-arms,
    and the next call returns the real measurement.

    Args:
        pid: The process id.

    Returns:
        ``{"cpu_percent": float or None, "memory_mb": float}``.
    """
    import psutil

    proc = psutil.Process(pid)
    memory = round(proc.memory_info().rss / (1024 * 1024), 1)
    if pid not in _SAMPLED:
        _SAMPLED.add(pid)
        proc.cpu_percent(None)
        return {"cpu_percent": None, "memory_mb": memory}
    percent = round(float(proc.cpu_percent(None)), 1)
    return {"cpu_percent": percent, "memory_mb": memory}


#: Pids that have been sampled once, so the next call has a baseline to measure
#: against. Process-lifetime rather than per-call on purpose: a fresh set each call
#: would put every process back to ``None`` forever.
_SAMPLED: set = set()


def _list_windows() -> List[Dict[str, Any]]:
    """Return the visible window list.

    Returns:
        The list from :func:`body.hands.window_manager.list_windows`.
    """
    from body.hands import window_manager

    return window_manager.list_windows()
