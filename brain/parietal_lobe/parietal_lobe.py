"""Parietal Lobe — the orchestrator.

The high-level API every other region and the HTTP layer talk to. Its shape is one
rule inherited unchanged from the Occipital Lobe: **every public method takes a
:class:`~brain.prefrontal_cortex.permission_decision.PermissionDecision` as its first
argument**, and the target that decision names must match what the method is about to
do.

That rule reads like overkill for a subsystem whose trust row is ``auto`` at every
level, and it is worth saying plainly why it is still here. ``system_awareness`` being
auto means *the gate does not ask the user* — it does not mean *nothing is checked*.
The audit log is the only record of what the agent looked at, the Amygdala still scores
every target, and the kill switch still has to catch a decision that was issued before
someone hit the stop. All three of those are properties of the decision being real, and
a method that could be called with ``None`` could be called with ``None`` by the one
caller nobody wrote a test for.

The six capabilities, in the order a person would ask for them:

* :meth:`ParietalLobe.get_active_window` — which window am I in.
* :meth:`ParietalLobe.get_screen_info` — what is my screen setup.
* :meth:`ParietalLobe.get_running_apps` — what is running.
* :meth:`ParietalLobe.get_mouse_position` — where is the pointer.
* :meth:`ParietalLobe.detect_user_context` — what am I working on.
* :meth:`ParietalLobe.get_full_snapshot` — all of the above.

**What this class owns.** It owns the invariants, not the platform:

* **One monitor reader.** :class:`~brain.parietal_lobe.screen_info.ScreenInfo` is
  constructed once and shared with the mouse and workspace readers. Two readers of the
  display layout is how a screenshot of monitor 1 and a click validated against
  monitor 2 come to disagree, and there is then no way to tell which one the safety
  check used.
* **One window reader.** The trackers consume
  :func:`body.hands.window_manager.list_windows` rather than enumerating themselves,
  for the same reason and with the same consequence.
* **The decision is checked here and again in the reader**, so a stop that engages
  between the two is caught by the second — the same defence-in-depth the Occipital
  Lobe uses.
* **No method returns a fabricated value.** Every tracker distinguishes "the OS said
  nothing" from "there is nothing there", and this class propagates that rather than
  converting a refusal into an empty answer. A context detector that answered
  ``"idle"`` because it recognised nothing would be making a claim about a person.

:mod:`brain.parietal_lobe.parietal_guards` is where the checks live, so that "did you
check?" has one answer to grep for.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Optional

from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.permission_decision import PermissionDecision
from brain.spinal_cord.kill_switch import KillSwitch, get_kill_switch

from brain.parietal_lobe import parietal_guards
from brain.parietal_lobe.app_tracker import AppTracker
from brain.parietal_lobe.context_detector import Context, ContextDetector
from brain.parietal_lobe.mouse_tracker import MouseTracker
from brain.parietal_lobe.screen_info import ScreenInfo
from brain.parietal_lobe.window_tracker import WindowTracker
from brain.parietal_lobe.workspace_detector import WorkspaceDetector

#: Re-exported so a caller can catch it after importing only the orchestrator.
__all__ = ["ParietalLobe"]


class ParietalLobe:
    """Spatial and system awareness, behind the permission gate.

    Attributes:
        windows: Window and application tracking.
        screens: The single monitor reader, shared with ``mouse`` and ``workspaces``.
        mouse: Pointer position.
        context: Activity inference.
        workspaces: Desktop surface layout.
        kill_switch: Re-checked on every call.
    """

    def __init__(
        self,
        window_tracker: Optional[WindowTracker] = None,
        screen_info: Optional[ScreenInfo] = None,
        app_tracker: Optional[AppTracker] = None,
        mouse_tracker: Optional[MouseTracker] = None,
        context_detector: Optional[ContextDetector] = None,
        kill_switch: Optional[KillSwitch] = None,
        audit: Optional[Any] = None,
        logger: Optional[Any] = None,
        capture_backend: Optional[Any] = None,
    ) -> None:
        """Wire the region together.

        Every collaborator is injectable, which is what lets the whole region be
        exercised with no display, no GPU and no Ollama — the only way the safety paths
        are testable on a machine that is not the target machine.

        Args:
            window_tracker: Window tracking.
            screen_info: Monitor layout. Constructed with ``capture_backend`` when not
                supplied, so the layout defaults to the one that decides which pixels a
                capture grabs.
            app_tracker: Running applications.
            mouse_tracker: Pointer position. Given ``screen_info``'s layout when not
                supplied, so a pointer always arrives with the monitor it is on.
            context_detector: Activity inference.
            kill_switch: The switch to re-check, or the process-wide one.
            audit: Audit log.
            logger: Activity logger.
            capture_backend: The Occipital Lobe's capture backend, used only when
                ``screen_info`` is not given.
        """
        self.kill_switch = kill_switch if kill_switch is not None else get_kill_switch()
        self.audit = audit
        self.logger = logger
        self.screens = (
            screen_info
            if screen_info is not None
            else ScreenInfo(backend=capture_backend)
        )
        self.windows = window_tracker if window_tracker is not None else WindowTracker()
        self.apps = app_tracker if app_tracker is not None else AppTracker()
        self.mouse = (
            mouse_tracker
            if mouse_tracker is not None
            else MouseTracker(monitors=self.screens.monitors)
        )
        self.context = (
            context_detector if context_detector is not None else ContextDetector()
        )
        self.workspaces = WorkspaceDetector(self.screens, active_window=self.windows.active)

    # -- the five reads -------------------------------------------------------

    def get_active_window(self, decision: Any) -> Dict[str, Any]:
        """Return the window that currently has focus.

        Args:
            decision: An allowed ``system_awareness`` decision for an
                ``active-window``.

        Returns:
            The window record with ``hwnd``, ``title``, ``pid``, ``process``,
            ``rect``, ``minimized`` and ``maximized``, plus ``app`` and ``summary``.
            ``window`` is None when nothing has focus — a locked screen, or the UAC
            prompt — which is a real state and not a failure.

        Raises:
            DecisionRefused: No decision, or one that does not permit this.
            DecisionReplayRefused: The decision was for another operation.
            KillSwitchRefused: The stop was engaged after the decision was issued.
            SystemAwarenessUnavailable: The OS would not say which window has focus.
        """
        self._verify("active-window", decision)
        window = self.windows.active()
        self._log("active_window", found=window is not None)
        return self._window_record(window)

    def get_screen_info(self, decision: Any) -> Dict[str, Any]:
        """Return the display layout.

        Args:
            decision: An allowed ``system_awareness`` decision for a ``screen-info``.

        Returns:
            ``resolution``, ``primary``, ``desktop``, ``monitors``,
            ``monitor_count``, ``dpi``, ``scale``, ``dpi_aware`` and ``source``.

        Raises:
            DecisionRefused: No decision, or one that does not permit this.
            KillSwitchRefused: The stop was engaged after the decision was issued.
            SystemAwarenessUnavailable: No source would report a layout.
        """
        self._verify("screen-info", decision)
        info = self.screens.describe()
        self._log(
            "screen_info",
            monitors=info.get("monitor_count"),
            dpi_aware=info.get("dpi_aware"),
        )
        return info

    def get_running_apps(
        self,
        decision: Any,
        include_hidden: bool = False,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Return the running applications.

        Args:
            decision: An allowed ``system_awareness`` decision for a ``running-apps``.
            include_hidden: Also list processes that own no visible window. Off by
                default; see :mod:`brain.parietal_lobe.app_tracker` for why.
            limit: Maximum applications returned.

        Returns:
            ``count``, ``hidden_count`` and ``apps``.

        Raises:
            DecisionRefused: No decision, or one that does not permit this.
            KillSwitchRefused: The stop was engaged after the decision was issued.
            SystemAwarenessUnavailable: ``psutil`` is missing, or the process table
                could not be read.
        """
        self._verify("running-apps", decision)
        apps = self.apps.list(include_hidden=include_hidden, limit=limit)
        summary = self.apps.summary()
        self._log("running_apps", count=len(apps), hidden=summary["hidden_count"])
        return {"count": len(apps), "hidden_count": summary["hidden_count"], "apps": apps}

    def get_mouse_position(self, decision: Any) -> Dict[str, Any]:
        """Return the pointer's position and which display it is on.

        Args:
            decision: An allowed ``system_awareness`` decision for a ``mouse-position``.

        Returns:
            ``x``, ``y``, ``monitor``, ``monitor_index``, ``on_desktop`` and ``visible``.

        Raises:
            DecisionRefused: No decision, or one that does not permit this.
            KillSwitchRefused: The stop was engaged after the decision was issued.
            SystemAwarenessUnavailable: The OS reported no pointer position.
        """
        self._verify("mouse-position", decision)
        position = self.mouse.locate()
        self._log("mouse_position", on_desktop=position.get("on_desktop"))
        return position

    def detect_user_context(self, decision: Any) -> Dict[str, Any]:
        """Infer what the user is doing right now.

        Args:
            decision: An allowed ``system_awareness`` decision for a ``detect-context``.

        Returns:
            The :class:`~brain.parietal_lobe.context_detector.Context` as data:
            ``context``, ``confidence``, ``reason``, ``evidence`` and ``idle_seconds``.
            ``context`` is ``unknown`` when no rule matched and ``idle`` only when the
            OS input timer says so — see :mod:`brain.parietal_lobe.context_detector`.

        Raises:
            DecisionRefused: No decision, or one that does not permit this.
            KillSwitchRefused: The stop was engaged after the decision was issued.
            SystemAwarenessUnavailable: The OS would not report the focused window.
        """
        self._verify("detect-context", decision)
        window = self.windows.active()
        context = self.context.detect(window)
        self._log("detect_context", context=context.name, confidence=context.confidence)
        return context.to_dict()

    # -- the combined read ----------------------------------------------------

    def get_full_snapshot(self, decision: Any) -> Dict[str, Any]:
        """Return every signal at once.

        Args:
            decision: An allowed ``system_awareness`` decision for a ``snapshot``.

        Returns:
            ``active_window``, ``screen_info``, ``running_apps``, ``mouse_position``,
            ``context`` and ``workspaces``, each either its own record or, where the
            platform declined, a record carrying ``error``, ``detail`` and an explicit
            ``failed: true``. See :meth:`_snapshot_window` for why a failed
            ``active_window`` keeps ``window: None`` alongside that marker.

            Partial results rather than an all-or-nothing answer, and deliberately so.
            A snapshot is what the Dashboard polls and what a user asking "what am I
            working on" gets; failing the whole call because ``psutil`` is missing would
            mean losing the active-window answer too, over a subsystem the question was
            not about. Each failure names what it was, so a partial answer is never
            mistaken for a complete one.
        """
        self._verify("snapshot", decision)

        def safely(read: Any, name: str) -> Dict[str, Any]:
            """Run one read, converting a refusal into a named error slot.

            Args:
                read: A zero-argument callable performing the read.
                name: The slot name, used in the error.

            Returns:
                The read's record, or ``{"error": ..., "detail": ..., "failed": True}``.
                The ``failed`` marker is explicit rather than left implicit, because
                the caller has to be able to tell a real record from an error slot
                without knowing the shape of either — and an error slot that looks
                like a record is how "the window list could not be read" ends up being
                classified as a window with a title saying so.
            """
            try:
                return read()
            except Exception as exc:
                self._log("snapshot_partial", failed=name, reason=str(exc))
                return {"error": str(exc), "detail": getattr(exc, "detail", name), "failed": True}

        window = safely(self.windows.active, "active_window")
        # An error slot is a dict, so it must be rejected explicitly before the
        # context detector is handed it.
        failed = bool(window.get("failed"))
        focused = None if failed else window
        context = self.context.detect(focused)
        if failed:
            # The detector's own answer here is "nothing has focus", which is true
            # of what it was given and false of the machine. Saying it unmodified
            # would report a failed read as a fact about the user's desktop, which is
            # the specific confusion this region's whole design is against.
            context = replace(context, reason=f"the window read failed: {context.reason}")
        return {
            "active_window": self._snapshot_window(focused, window, failed),
            "screen_info": safely(self.screens.describe, "screen_info"),
            "running_apps": safely(self.apps.summary, "running_apps"),
            "mouse_position": safely(self.mouse.locate, "mouse_position"),
            "context": context.to_dict(),
            "workspaces": safely(self.workspaces.describe, "workspaces"),
        }

    def _snapshot_window(
        self,
        focused: Optional[Dict[str, Any]],
        error_slot: Dict[str, Any],
        failed: bool,
    ) -> Dict[str, Any]:
        """Return the snapshot's active-window slot, real record or named failure.

        A failed read keeps the *shape* of the window record — ``window: None`` — so a
        caller reading ``active_window["window"]`` does not have to know in advance
        whether the platform answered, and adds ``failed`` plus the error on top.
        Overwriting the slot with the bare error dict instead would be tidier and
        strictly worse: every existing reader of the snapshot would break the first
        time a window enumeration failed on the user's machine and nowhere else.

        The ``reason`` is replaced as well as added to. Left alone it would read
        "nothing has focus", which is a claim about the desktop that a failed read
        cannot support.

        Args:
            focused: The window record, or None when the read failed or nothing has
                focus.
            error_slot: What :meth:`get_full_snapshot`'s ``safely`` produced.
            failed: Whether the read actually failed.

        Returns:
            The slot, as the snapshot reports it.
        """
        record = self._window_record(focused)
        if not failed:
            return record
        message = str(error_slot.get("error", "the window read failed"))
        return {
            **record,
            "reason": f"the window read failed: {message}",
            "error": message,
            "detail": error_slot.get("detail", "active_window"),
            "failed": True,
        }

    # -- permissions ----------------------------------------------------------

    def propose(self, operation: str, monitor: Optional[int] = None) -> Action:
        """Build the action a caller must get decided before calling this region.

        Args:
            operation: One of :data:`brain.parietal_lobe.parietal_guards.OPERATIONS`.
            monitor: The monitor a caller is interested in. Recorded in
                ``detail``, **not** folded into the target: every read here covers the
                whole layout, so a target naming a monitor would claim a narrowness
                the read does not have — and would reintroduce the spellings drift that
                ``target_for`` now refuses to permit.

        Returns:
            The proposed action. Passing it through the gate is the caller's job — the
            region does not decide, it only states what it is about to do. The target
            carries the operation name and never a window title; see
            :mod:`brain.parietal_lobe.parietal_guards` for why that distinction is the
            one that matters for privacy here.
        """
        detail: Dict[str, Any] = {"operation": operation}
        if monitor is not None:
            detail["monitor"] = int(monitor)
        return Action(
            category=ActionCategory.SYSTEM_AWARENESS,
            target=parietal_guards.target_for(operation),
            detail=detail,
        )

    def status(self) -> Dict[str, Any]:
        """Return the region's configuration for the UI.

        Returns:
            The category, the operations, which optional libraries are present, and
            whether the layout can be read at all. Nothing here needs a decision: it is
            the same class of read as the trust level, and the Dashboard needs it to
            decide whether to show the context widget at all.
        """
        from brain.parietal_lobe.parietal_guards import OPERATIONS

        missing = parietal_guards.missing(["psutil", "pygetwindow", "mss"])
        layout: Any
        try:
            layout = {"monitors": len(self.screens.monitors()), "ok": True}
        except Exception as exc:
            layout = {"ok": False, "reason": str(exc)}
        return {
            "category": parietal_guards.SYSTEM_AWARENESS,
            "operations": list(OPERATIONS),
            "optional_libraries_missing": missing,
            "layout": layout,
            "kill_switch_engaged": bool(self.kill_switch.is_killed()),
        }

    # -- internals ------------------------------------------------------------

    def _verify(self, operation: str, decision: Any) -> None:
        """Check the decision names this exact operation.

        Args:
            operation: The operation about to run.
            decision: What the caller was handed.

        Returns:
            Nothing. Raises rather than returning a verdict, matching every other
            region: the orchestrator does not branch on permissions. Takes no monitor
            argument on purpose — see :func:`brain.parietal_lobe.parietal_guards.target_for`
            for why a monitor must never reach the target string.
        """
        parietal_guards.verify_decision(
            decision, parietal_guards.target_for(operation), self.kill_switch
        )

    def _window_record(self, window: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Return a window record with the derived fields callers actually use.

        Args:
            window: A raw window record, or None.

        Returns:
            The record with ``app`` and ``summary`` added, or a ``{"window": None}``
            stub when nothing has focus. ``None`` is preserved rather than replaced by
            an empty dict: "no window has focus" and "a window with no title" are
            different states, and collapsing them is how an agent ends up confidently
            describing a desktop that has no idea what it is looking at.
        """
        if window is None:
            return {"window": None, "reason": "nothing has focus"}
        app = self.windows.app_of(window)
        return {
            **window,
            "app": app,
            "summary": (
                f"{app}: {self.windows.redacted_title(window)}" if app else self.windows.redacted_title(window)
            ),
        }

    def _log(self, action: str, **fields: Any) -> None:
        """Write one record to the activity log, if there is one.

        Args:
            action: Short verb phrase.
            **fields: Extra structured detail.

        Returns:
            Nothing. Errors are swallowed so logging cannot fail a system read — the
            answer has already been produced and the caller is waiting for it.
        """
        if self.logger is None:
            return
        try:
            self.logger.log("parietal_lobe", action, **fields)
        except Exception:
            pass
