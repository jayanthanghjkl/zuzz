"""Parietal Lobe — tests.

Every test in this file builds its region from injected doubles, never from the real
operating system. That is not a preference: three of the properties this region exists
to guarantee cannot be demonstrated against a live machine at all —

* the platform declining to answer (:class:`SystemAwarenessUnavailable`) has to be
  provoked, not waited for;
* a **hidden** cursor cannot be arranged on demand, and the assertion that
  ``(0, 0)`` with ``visible: False`` is not reported as "the pointer is at the origin"
  is the whole reason :meth:`~brain.parietal_lobe.mouse_tracker.MouseTracker.locate`
  carries a visibility flag;
* input-idle behaviour is a clock, so the ``idle`` path is reachable only by injecting
  the reading.

A suite that ran against the real desktop would pass on the one machine it ran on and
prove nothing about any of them.
"""

from __future__ import annotations

import pytest

from brain.parietal_lobe import (
    CODING,
    GAMING,
    IDLE,
    UNKNOWN,
    AppTracker,
    ContextDetector,
    DecisionRefused,
    DecisionReplayRefused,
    KillSwitchRefused,
    MouseTracker,
    NoSuchMonitor,
    ParietalLobe,
    ScreenInfo,
    SystemAwarenessUnavailable,
    WindowTracker,
)
from brain.parietal_lobe.parietal_guards import resolve_monitor, target_for
from brain.prefrontal_cortex.permission_decision import PermissionDecision

ONE_MONITOR = [
    {"left": 0, "top": 0, "width": 1920, "height": 1080, "primary": True}
]

TWO_MONITORS = [
    {"left": 0, "top": 0, "width": 1920, "height": 1080, "primary": True},
    {"left": 1920, "top": 0, "width": 1280, "height": 1024, "primary": False},
]

A_WINDOW = {
    "hwnd": 101,
    "title": "main.py - VS Code",
    "pid": 4242,
    "process": "Code.exe",
    "rect": {"left": 0, "top": 0, "width": 1200, "height": 800},
    "visible": True,
}


class FakeSwitch:
    """A kill switch whose state the test controls."""

    def __init__(self, killed: bool = False) -> None:
        self.killed = killed

    def is_killed(self) -> bool:
        """Return whether the stop is engaged."""
        return self.killed


def make_lobe(
    windows=None,
    monitor: int = 0,
    processes=(),
    apps=(),
    idle: int = 0,
    position=(400, 300),
    cursor_visible: bool = True,
    killed: bool = False,
    states=None,
) -> ParietalLobe:
    """Build a Parietal Lobe from doubles.

    Args:
        windows: The window list every call returns.
        monitor: Monitor index the capture backend reports.
        processes: ``(pid, name)`` pairs the app tracker enumerates.
        apps: Per-pid usage records, as ``{pid: {...}}``.
        idle: Seconds since the last input.
        position: Pointer position, or None to refuse.
        cursor_visible: What the visibility reader reports.
        killed: Whether the kill switch is engaged.
        states: Per-window minimised state, as ``{hwnd: {...}}``.

    Returns:
        The region, wired entirely to doubles.
    """

    class Backend:
        """A capture backend returning a fixed layout."""

        def layout(self):
            """Return the requested monitor layout."""
            return list(TWO_MONITORS if monitor == 1 else ONE_MONITOR)

        def grab(self, index):  # pragma: no cover - never captured here
            """Refuse: this suite never takes a screenshot."""
            raise AssertionError("a system-awareness test must not capture")

    screens = ScreenInfo(
        backend=Backend(),
        desktop=lambda: dict(
            left=min(m["left"] for m in (TWO_MONITORS if monitor == 1 else ONE_MONITOR)),
            top=0,
            width=sum(m["width"] for m in (TWO_MONITORS if monitor == 1 else ONE_MONITOR)),
            height=1080,
        ),
        dpi=lambda: 96,
    )

    def usage(pid: int):
        """Return the recorded usage for a pid."""
        if pid not in apps:
            raise SystemAwarenessUnavailable("process exited", detail="processes")
        return apps[pid]

    return ParietalLobe(
        window_tracker=WindowTracker(
            windows=lambda: list(windows or []),
            foreground=lambda: (windows or [{}])[0].get("hwnd", 0) if windows else 0,
            states=lambda record: (states or {}).get(record.get("hwnd"), {}),
        ),
        screen_info=screens,
        app_tracker=AppTracker(
            process=lambda: list(processes),
            usage=usage,
            windows=lambda: list(windows or []),
        ),
        mouse_tracker=MouseTracker(
            position=lambda: position,
            visible=lambda: cursor_visible,
            monitors=screens.monitors,
        ),
        context_detector=ContextDetector(idle=lambda: idle),
        kill_switch=FakeSwitch(killed),
    )


def allow(lobe: ParietalLobe, operation: str, monitor: int = 0) -> PermissionDecision:
    """Mint an allowed decision the way the gate would."""
    return PermissionDecision.make(
        lobe.propose(operation, monitor), 4, "auto", "read-only system awareness"
    )


# ---------------------------------------------------------------------
# 1. Window tracking
# ---------------------------------------------------------------------


def test_window_tracker_detects_active_window() -> None:
    """The focused window is reported with its application and geometry."""
    lobe = make_lobe(windows=[A_WINDOW])
    window = lobe.get_active_window(allow(lobe, "active-window"))

    assert window["title"] == "main.py - VS Code"
    assert window["app"] == "code"
    assert window["pid"] == 4242
    assert window["rect"]["width"] == 1200
    assert window["summary"] == "code: main.py - VS Code"


def test_window_tracker_reports_no_focus_as_none_not_an_empty_record() -> None:
    """"Nothing has focus" must not look like "a window with no title"."""
    lobe = make_lobe(windows=[])
    window = lobe.get_active_window(allow(lobe, "active-window"))

    assert window["window"] is None
    assert "focus" in window["reason"]


def test_window_tracker_handles_minimized_windows() -> None:
    """A minimised window is reported as minimised, and the state is preserved.

    ``None`` and ``False`` are kept apart: "this window is minimised" and "ZUZZ could
    not tell" lead to different decisions, and collapsing them makes a restored window
    look deliberately minimised.
    """
    lobe = make_lobe(
        windows=[A_WINDOW], states={101: {"minimized": True, "maximized": False}}
    )
    window = lobe.get_active_window(allow(lobe, "active-window"))

    assert window["minimized"] is True
    assert window["maximized"] is False

    unknown = make_lobe(windows=[A_WINDOW], states={})
    assert unknown.get_active_window(allow(unknown, "active-window"))["minimized"] is None


def test_window_tracker_falls_back_to_a_handle_when_enumeration_misses_it() -> None:
    """Focus on an untitled window yields a handle record, not ``None``."""
    lobe = make_lobe(windows=[])
    lobe.windows.foreground = lambda: 999
    window = lobe.get_active_window(allow(lobe, "active-window"))

    assert window["hwnd"] == 999
    assert window["title"] == ""
    assert window["app"] == ""


def test_a_platform_that_will_not_answer_is_refused_not_emptied() -> None:
    """A refused window enumeration must not read as an empty desktop."""
    lobe = make_lobe(windows=[A_WINDOW])

    def refuse():
        raise SystemAwarenessUnavailable("EnumWindows failed", detail="windows")

    lobe.windows.windows = refuse
    with pytest.raises(SystemAwarenessUnavailable):
        lobe.get_active_window(allow(lobe, "active-window"))


# ---------------------------------------------------------------------
# 2. Screen geometry
# ---------------------------------------------------------------------


def test_screen_info_handles_multi_monitor() -> None:
    """A two-monitor layout reports both surfaces and a spanning desktop."""
    lobe = make_lobe(monitor=1)
    info = lobe.get_screen_info(allow(lobe, "screen-info", 1))

    assert info["monitor_count"] == 2
    assert [m["index"] for m in info["monitors"]] == [0, 1]
    assert info["desktop"]["width"] == 3200
    assert info["multi_monitor"] is True


def test_screen_info_reports_a_single_monitor_machine_honestly() -> None:
    """One monitor is one monitor, not a layout with an absent second entry."""
    lobe = make_lobe()
    info = lobe.get_screen_info(allow(lobe, "screen-info"))

    assert info["monitor_count"] == 1
    assert info["multi_monitor"] is False
    assert info["resolution"] == {"width": 1920, "height": 1080}


def test_screen_info_reports_whether_the_process_is_dpi_aware() -> None:
    """The DPI-awareness flag is reported, because it decides what the numbers mean."""
    lobe = make_lobe()
    info = lobe.get_screen_info(allow(lobe, "screen-info"))

    assert "dpi_aware" in info
    assert "dpi" in info
    assert info["source"] == "capture-backend"


def test_a_layout_nobody_can_read_is_refused_rather_than_invented() -> None:
    """No source, no answer — never a plausible 1920x1080."""
    lobe = make_lobe()
    lobe.screens.backend = None
    lobe.screens.desktop = lambda: None

    with pytest.raises(SystemAwarenessUnavailable):
        lobe.get_screen_info(allow(lobe, "screen-info"))


def test_resolve_monitor_refuses_an_absent_display() -> None:
    """An index that is not attached is a 404, not monitor 0."""
    with pytest.raises(NoSuchMonitor):
        resolve_monitor(3, ONE_MONITOR)
    with pytest.raises(NoSuchMonitor):
        resolve_monitor(0, [])


# ---------------------------------------------------------------------
# 3. Running applications
# ---------------------------------------------------------------------


def test_app_tracker_lists_running_processes() -> None:
    """Applications with windows are listed, with their resources."""
    lobe = make_lobe(
        windows=[A_WINDOW],
        processes=[(4242, "Code.exe"), (900, "chrome.exe")],
        apps={
            4242: {"cpu_percent": 12.5, "memory_mb": 340.0},
            900: {"cpu_percent": None, "memory_mb": 900.0},
        },
    )
    result = lobe.get_running_apps(allow(lobe, "running-apps"))

    assert result["count"] == 1
    assert result["apps"][0]["name"] == "Code.exe"
    assert result["apps"][0]["memory_mb"] == 340.0
    assert result["apps"][0]["has_window"] is True


def test_app_tracker_excludes_system_processes_without_elevation() -> None:
    """Windowless processes are counted, not named — the count stays honest.

    This is the privacy property, not a tidiness one: a full process table is a
    fingerprint of the machine, and 300 entries of ``svchost.exe`` tell the model
    nothing it can act on.
    """
    lobe = make_lobe(
        windows=[A_WINDOW],
        processes=[(4242, "Code.exe")] + [(pid, f"svc-{pid}") for pid in range(50, 350)],
        apps={4242: {"cpu_percent": 1.0, "memory_mb": 100.0}},
    )

    default = lobe.get_running_apps(allow(lobe, "running-apps"))
    assert default["count"] == 1
    assert default["hidden_count"] == 300
    assert all("svc-" not in app["name"] for app in default["apps"])

    # Opting in is possible, which is what makes the default a choice. The limit is
    # raised so this asserts the filter lifted, not that the cap did not apply.
    included = lobe.get_running_apps(
        allow(lobe, "running-apps"), include_hidden=True, limit=1000
    )
    assert included["count"] == 301


def test_app_tracker_reports_a_process_that_exited_mid_query_as_unmeasured() -> None:
    """A vanished process reports no CPU rather than a fabricated zero."""
    lobe = make_lobe(
        windows=[A_WINDOW],
        processes=[(4242, "Code.exe"), (7777, "gone.exe")],
        apps={4242: {"cpu_percent": 5.0, "memory_mb": 50.0}},
    )
    result = lobe.get_running_apps(allow(lobe, "running-apps"), include_hidden=True)
    by_pid = {app["pid"]: app for app in result["apps"]}

    assert by_pid[7777]["cpu_percent"] is None


def test_app_tracker_refuses_when_psutil_is_absent() -> None:
    """A missing dependency is a 503, not an empty process table."""
    lobe = make_lobe(windows=[A_WINDOW])

    def no_psutil():
        raise ImportError("psutil is not installed")

    lobe.apps.process = no_psutil
    with pytest.raises(SystemAwarenessUnavailable):
        lobe.get_running_apps(allow(lobe, "running-apps"))


# ---------------------------------------------------------------------
# 4. Pointer position
# ---------------------------------------------------------------------


def test_mouse_tracker_reports_position() -> None:
    """The pointer comes back with the monitor it is on."""
    lobe = make_lobe(position=(100, 200))
    pointer = lobe.get_mouse_position(allow(lobe, "mouse-position"))

    assert (pointer["x"], pointer["y"]) == (100, 200)
    assert pointer["monitor_index"] == 0
    assert pointer["on_desktop"] is True
    assert pointer["visible"] is True


def test_mouse_tracker_handles_hidden_cursor() -> None:
    """A hidden cursor is reported as hidden, and not as "the pointer is at 0,0".

    The pair of assertions matters: both halves of the old failure are pinned, so a
    later "simplification" that drops the flag cannot pass quietly.
    """
    lobe = make_lobe(position=(0, 0), cursor_visible=False)
    pointer = lobe.get_mouse_position(allow(lobe, "mouse-position"))

    assert (pointer["x"], pointer["y"]) == (0, 0)
    assert pointer["visible"] is False

    unknown = make_lobe(position=(0, 0), cursor_visible=None)
    assert unknown.get_mouse_position(allow(unknown, "mouse-position"))["visible"] is None


def test_mouse_tracker_reports_off_desktop_points_as_such() -> None:
    """A point on no attached monitor is reported, not rounded to monitor 0."""
    lobe = make_lobe(position=(5000, 5000))
    pointer = lobe.get_mouse_position(allow(lobe, "mouse-position"))

    assert pointer["on_desktop"] is False
    assert pointer["monitor_index"] is None


def test_mouse_tracker_names_the_second_monitor() -> None:
    """With two displays, the pointer's monitor index is the one it is actually on."""
    lobe = make_lobe(monitor=1, position=(2000, 100))
    pointer = lobe.get_mouse_position(allow(lobe, "mouse-position"))

    assert pointer["monitor_index"] == 1


# ---------------------------------------------------------------------
# 5. Activity inference
# ---------------------------------------------------------------------


def test_context_detector_identifies_vs_code_session() -> None:
    """An IDE in focus is coding, at high confidence, on the process name."""
    lobe = make_lobe(windows=[A_WINDOW], idle=0)
    context = lobe.detect_user_context(allow(lobe, "detect-context"))

    assert context["context"] == CODING
    assert context["confidence"] >= 0.9
    assert context["evidence"]["app"] == "code"


def test_context_detector_identifies_browser_session() -> None:
    """A browser in focus is browsing."""
    browser = dict(A_WINDOW, title="docs.python.org - Chrome", process="chrome.exe")
    lobe = make_lobe(windows=[browser], idle=0)
    context = lobe.detect_user_context(allow(lobe, "detect-context"))

    assert context["context"] == "browsing"
    assert context["confidence"] >= 0.9


def test_context_detector_identifies_idle() -> None:
    """Idle comes from the OS input timer, and says how long."""
    lobe = make_lobe(windows=[A_WINDOW], idle=900)
    context = lobe.detect_user_context(allow(lobe, "detect-context"))

    assert context["context"] == IDLE
    assert context["idle_seconds"] == 900
    assert "900s" in context["reason"]


def test_idleness_outranks_the_focused_window() -> None:
    """An idle user in an IDE is idle — the pointer is the stronger signal."""
    lobe = make_lobe(windows=[A_WINDOW], idle=5000)
    assert lobe.detect_user_context(allow(lobe, "detect-context"))["context"] == IDLE


def test_context_detector_reports_unknown_rather_than_guessing() -> None:
    """An unrecognised window is ``unknown`` with zero confidence.

    This is the assertion that protects a human from the detector. ``unknown`` says
    "I do not know"; ``idle`` says "you are doing nothing", and only the OS's own
    input timer may make the second claim.
    """
    odd = dict(A_WINDOW, title="Namaste - SomeApp", process="someapp.exe")
    lobe = make_lobe(windows=[odd], idle=0)
    context = lobe.detect_user_context(allow(lobe, "detect-context"))

    assert context["context"] == UNKNOWN
    assert context["confidence"] == 0.0
    assert context["reason"] == "no rule matched the focused window"


def test_context_detector_never_claims_idle_with_no_evidence() -> None:
    """No window and no idle reading is ``unknown``, not ``idle``."""
    lobe = make_lobe(windows=[], idle=None)
    context = lobe.detect_user_context(allow(lobe, "detect-context"))

    assert context["context"] == UNKNOWN
    assert context["idle_seconds"] is None


def test_context_detector_infers_from_a_title_it_has_no_process_rule_for() -> None:
    """A PDF in an unknown reader is still reading."""
    pdf = dict(A_WINDOW, title="contract.pdf - SumatraPDF", process="sumatra.exe")
    lobe = make_lobe(windows=[pdf], idle=0)

    assert lobe.detect_user_context(allow(lobe, "detect-context"))["context"] == "reading"


def test_context_detector_identifies_a_game() -> None:
    """A game is gaming, and is not confused with browsing."""
    game = dict(A_WINDOW, title="Steam", process="steam.exe")
    lobe = make_lobe(windows=[game], idle=0)

    assert lobe.detect_user_context(allow(lobe, "detect-context"))["context"] == GAMING


def test_context_evidence_truncates_the_window_title() -> None:
    """The evidence carries a bounded title, not the whole document name."""
    long_title = "x" * 400 + ".py"
    window = dict(A_WINDOW, title=long_title)
    lobe = make_lobe(windows=[window], idle=0)
    context = lobe.detect_user_context(allow(lobe, "detect-context"))

    assert len(context["evidence"]["title"]) <= 80


# ---------------------------------------------------------------------
# 6. The snapshot
# ---------------------------------------------------------------------


def test_snapshot_combines_all_signals() -> None:
    """One call returns every read, with its own key each."""
    lobe = make_lobe(
        windows=[A_WINDOW],
        processes=[(4242, "Code.exe")],
        apps={4242: {"cpu_percent": 2.0, "memory_mb": 120.0}},
        position=(10, 20),
    )
    snapshot = lobe.get_full_snapshot(allow(lobe, "snapshot"))

    assert set(snapshot) == {
        "active_window",
        "screen_info",
        "running_apps",
        "mouse_position",
        "context",
        "workspaces",
    }
    assert snapshot["active_window"]["app"] == "code"
    assert snapshot["screen_info"]["monitor_count"] == 1
    assert snapshot["running_apps"]["count"] == 1
    assert snapshot["mouse_position"]["x"] == 10
    assert snapshot["context"]["context"] == CODING
    assert snapshot["workspaces"]["surface_count"] == 1


def test_snapshot_degrades_one_slot_without_losing_the_others() -> None:
    """A failed read is a named error slot; the rest of the snapshot still answers.

    The Dashboard polls this and a user asking "what am I working on" gets it. Failing
    the whole call because one dependency is missing would throw away the active-window
    answer over a subsystem the question was not about.
    """
    lobe = make_lobe(windows=[A_WINDOW])

    def no_psutil():
        raise ImportError("psutil is not installed")

    lobe.apps.process = no_psutil
    snapshot = lobe.get_full_snapshot(allow(lobe, "snapshot"))

    assert snapshot["running_apps"]["failed"] is True
    assert "psutil" in snapshot["running_apps"]["error"]
    assert snapshot["active_window"]["app"] == "code"
    assert snapshot["context"]["context"] == CODING


def test_snapshot_never_mistakes_an_error_slot_for_a_window() -> None:
    """A refused window read does not become a window whose title is the error.

    The error slot is a dict, so an ``isinstance(window, dict)`` check passes it
    straight through to the context detector. This asserts the marker is honoured.
    """
    lobe = make_lobe(windows=[A_WINDOW])

    def refuse():
        raise SystemAwarenessUnavailable("EnumWindows failed", detail="windows")

    lobe.windows.windows = refuse
    snapshot = lobe.get_full_snapshot(allow(lobe, "snapshot"))

    assert snapshot["active_window"]["window"] is None
    assert snapshot["context"]["context"] == UNKNOWN
    # The detector is handed nothing and would truthfully say "nothing has focus",
    # which is false of the machine. The reason has to name the failed read instead.
    assert "the window read failed" in snapshot["context"]["reason"]


def test_workspaces_report_that_virtual_desktops_are_not_readable() -> None:
    """The virtual desktop count is reported unavailable, not guessed."""
    lobe = make_lobe(windows=[A_WINDOW])
    snapshot = lobe.get_full_snapshot(allow(lobe, "snapshot"))

    virtual = snapshot["workspaces"]["virtual_desktops"]
    assert virtual["available"] is False
    assert "virtual desktop" in virtual["reason"].lower()


# ---------------------------------------------------------------------
# 7. The permission boundary
# ---------------------------------------------------------------------


def test_parietal_requires_system_awareness_permission() -> None:
    """No decision, or the wrong one, refuses the read."""
    lobe = make_lobe(windows=[A_WINDOW])

    with pytest.raises(DecisionRefused):
        lobe.get_active_window(None)
    with pytest.raises(DecisionRefused):
        lobe.get_active_window("not a decision")
    with pytest.raises(DecisionRefused):
        lobe.get_full_snapshot({"allowed": True})


def test_a_dry_run_does_not_read() -> None:
    """A dry run describes the read; it does not perform one."""
    from brain.parietal_lobe import DryRunRefused

    lobe = make_lobe(windows=[A_WINDOW])
    action = lobe.propose("active-window")
    dry = PermissionDecision(
        action=action,
        allowed=True,
        requires_confirmation=False,
        decision="auto",
        reason="dry run",
        trust_level=4,
        dry_run=True,
    )

    # ``allowed`` is True, so the refusal cannot be coming from the verdict.
    assert dry.allowed is True
    with pytest.raises(DryRunRefused):
        lobe.get_active_window(dry)


def test_parietal_respects_kill_switch() -> None:
    """A stop engaged before the call blocks every read in the region.

    All six, because a subsystem that honours the stop on five of six methods is a
    subsystem with a sixth method that does not — and a read that bypasses the stop is
    the worst kind of bug in a perception layer, since it is the one that keeps working
    after the user asked for nothing to work.
    """
    lobe = make_lobe(windows=[A_WINDOW], killed=True)
    calls = {
        "active-window": lobe.get_active_window,
        "screen-info": lobe.get_screen_info,
        "running-apps": lobe.get_running_apps,
        "mouse-position": lobe.get_mouse_position,
        "detect-context": lobe.detect_user_context,
        "snapshot": lobe.get_full_snapshot,
    }

    for operation, call in calls.items():
        with pytest.raises(KillSwitchRefused):
            call(allow(lobe, operation))


def test_a_decision_for_another_operation_cannot_be_replayed() -> None:
    """An approval for one read is not an approval for the others.

    Both reads are ``auto`` at every level, so if the gate were the only check this
    could not be caught. The audit log is the only record of which read was approved,
    which is why the targets have to be distinct.
    """
    lobe = make_lobe(windows=[A_WINDOW])
    for granted, attempted in (
        ("active-window", lobe.get_running_apps),
        ("running-apps", lobe.get_active_window),
        ("detect-context", lambda decision: lobe.get_full_snapshot(decision)),
        ("snapshot", lobe.get_active_window),
    ):
        with pytest.raises(DecisionReplayRefused):
            attempted(allow(lobe, granted))


def test_a_read_cannot_be_answered_by_another_category() -> None:
    """A ``visual_capture`` decision does not authorise a system read."""
    from brain.prefrontal_cortex.action import Action, ActionCategory

    lobe = make_lobe(windows=[A_WINDOW])
    wrong = PermissionDecision.make(
        Action(category=ActionCategory.VISUAL_CAPTURE, target="capture:monitor=0"),
        4,
        "auto",
        "approved for a screenshot",
    )

    with pytest.raises(DecisionReplayRefused):
        lobe.get_active_window(wrong)


def test_targets_carry_the_operation_and_never_a_window_title() -> None:
    """The decision target is an operation name.

    A title in the target would be pattern-matched by the Amygdala, written to the
    audit log as though it were a path, and rendered into a confirmation popup — all of
    which would put a document name into the safety layer before anyone approved it.
    """
    action = make_lobe(windows=[A_WINDOW]).propose("active-window")

    assert snapshot["active_window"]["window"] is None
    assert snapshot["context"]["context"] == UNKNOWN
    # The detector is handed nothing and would truthfully say "nothing has focus",
    # which is false of the machine. The reason has to name the failed read instead.
    assert "the window read failed" in snapshot["context"]["reason"]


def test_targets_carry_the_operation_and_never_a_window_title() -> None:
    """The decision target is an operation name, and nothing else.

    A title in the target would be pattern-matched by the Amygdala, written to the
    audit log as though it were a path, and rendered into a confirmation popup — all of
    which would put a document name into the safety layer before anyone approved it.
    A *monitor* in the target is refused outright, because ``propose`` and ``_verify``
    building different spellings of the same read is how Session 13's vision subsystem
    became registered-but-unreachable.
    """
    lobe = make_lobe(windows=[A_WINDOW])
    action = lobe.propose("active-window")

    assert action.target == "active-window"
    assert action.category.value == "system_awareness"
    assert target_for("snapshot") == "snapshot"

    # The monitor is recorded, in detail, where it is not compared.
    addressed = lobe.propose("screen-info", 1)
    assert addressed.target == "screen-info"
    assert addressed.detail["monitor"] == 1

    # And an operation nobody performs cannot produce a target at all.
    with pytest.raises(ValueError):
        target_for("no-such-operation")


def test_the_trust_row_auto_approves_at_every_level() -> None:
    """The one row in the matrix that does not ask, pinned so it stays deliberate."""
    from brain.prefrontal_cortex.permission_gate import PermissionGate
    from brain.prefrontal_cortex.trust_manager import TrustManager  # noqa: F401

    gate = PermissionGate()
    row = gate.row_for("system_awareness")

    assert [row[str(level)] for level in (1, 2, 3, 4, 5)] == ["auto"] * 5


# ---------------------------------------------------------------------
# 8. The catalogue
# ---------------------------------------------------------------------


def test_parietal_tools_registered_in_catalogue() -> None:
    """All six are described to the model and all six are implemented."""
    from brain.cerebrum.tool_catalogue import TOOL_NAMES
    from body.tools.registry import catalogue_mismatches, get_registry

    expected = {
        "parietal_get_active_window",
        "parietal_get_screen_info",
        "parietal_get_running_apps",
        "parietal_get_mouse_position",
        "parietal_detect_context",
        "parietal_get_snapshot",
    }
    assert expected.issubset(set(TOOL_NAMES)), expected - set(TOOL_NAMES)
    assert catalogue_mismatches() == []

    registry = get_registry()
    for name in expected:
        spec = registry.get(name)
        assert spec is not None, name
        assert spec.summary, name
        assert callable(spec.resolve) and callable(spec.execute), name


def test_parietal_tools_refuse_when_the_region_is_absent() -> None:
    """An unattached region refuses rather than reporting an empty desktop."""
    from body.tools import ToolContext
    from body.tools.parietal_tools import _resolve_active_window
    from body.tools.tool_spec import ToolRefused

    context = ToolContext(hands=None, files=None, directories=None)

    with pytest.raises(ToolRefused):
        _resolve_active_window({}, context)


def test_parietal_tool_targets_are_system_awareness() -> None:
    """Every tool resolves to the system_awareness category, not a borrowed one."""
    from body.tools import ToolContext
    from body.tools.parietal_tools import PARIETAL_TOOLS

    context = ToolContext(
        hands=None,
        files=None,
        directories=None,
        parietal_lobe=make_lobe(windows=[A_WINDOW]),
    )

    for spec in PARIETAL_TOOLS:
        target = spec.resolve({}, context)
        assert target.category == "system_awareness", spec.name
        assert target.target, spec.name
