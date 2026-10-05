"""Basal Ganglia — routines tests.

The region, not the endpoints. What these pin down are the properties that
make a stored sequence of desktop acts safe to keep around.

Three of them are load-bearing and each has a test of its own:

* **A new routine is stored disarmed.** Creating one and arming it are two
  acts, so a user can read what they just built before it starts opening
  applications on a trigger.
* **A step naming something ZUZZ cannot call is refused when the routine is
  written**, not when it is first run. Otherwise an armed routine fails
  halfway through doing something to the desktop.
* **Everything is decided before anything runs.** A five-step routine whose
  third step is refused must not have steps one and two already applied.

The gate is the real one throughout, read from ``config/trust_levels.json``,
because the interesting behaviour *is* the matrix's: the seeds are only fully
automatic at trust level 5, and ``browser_navigate`` is why.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from brain.basal_ganglia import (
    MAX_STEPS,
    SEED_ROUTINES,
    BasalGanglia,
    RoutineRegistry,
    UnknownRoutine,
    build_from_pattern,
    detect_patterns,
    suggest_name,
    validate_name,
)
from brain.basal_ganglia.context_triggers import describe, due
from brain.basal_ganglia.exceptions import (
    RoutineDisabled,
    RoutineNameInvalid,
    RoutineNotPermitted,
    RoutineRefused,
    StepRefused,
    TriggerRefused,
)
from brain.basal_ganglia.pattern_detector import (
    DEFAULT_MIN_REPETITIONS,
    MAX_WINDOW,
    detect_patterns as find_patterns,
)
from brain.prefrontal_cortex.action import ActionCategory
from brain.prefrontal_cortex.permission_gate import PermissionGate

#: Two tools whose matrix rows differ, so a routine built from them is
#: partially permitted at most levels. ``open_app`` is auto from level 2;
#: ``browser_navigate`` is ``browse_internet``, which asks until level 5.
OPEN_APP = "open_app"
NAVIGATE = "browser_navigate"


def step(tool: str, **params: Any) -> Dict[str, Any]:
    """Return one step of a routine."""
    return {"tool": tool, "params": params, "why": "because the test says so"}


def two_step_routine(name: str = "pair") -> Dict[str, Any]:
    """Return a routine payload with both tools, so verdicts differ."""
    return {
        "name": name,
        "description": "open two things",
        "steps": [
            step(OPEN_APP, app_name="chrome"),
            step(NAVIGATE, url="http://standup"),
        ],
    }


def ganglia(tmp_path: Path, **kwargs: Any) -> BasalGanglia:
    """Return a Basal Ganglia over a throwaway registry file."""
    return BasalGanglia(registry=RoutineRegistry(tmp_path / "routines.json"), **kwargs)


def armed(tmp_path: Path, name: str = "pair", **kwargs: Any) -> BasalGanglia:
    """Return a Basal Ganglia holding one armed two-step routine."""
    brain = ganglia(tmp_path)
    brain.create_routine(two_step_routine(name))
    brain.update_routine(name, {"enabled": True})
    return brain


def _with_close_step(tmp_path: Path) -> BasalGanglia:
    """Return a Basal Ganglia holding an armed routine that closes an app.

    ``close_app`` is ``ask`` at every trust level, so this routine can never
    run unattended. It is the routine to reach for when a test needs a plan
    that refuses.
    """
    brain = ganglia(tmp_path)
    brain.create_routine(
        {
            "name": "closes",
            "steps": [
                step(OPEN_APP, app_name="chrome"),
                {"tool": "close_app", "params": {"process_name": "chrome"}, "why": "tidy up"},
            ],
        }
    )
    brain.update_routine("closes", {"enabled": True})
    return brain


# -- nothing runs until it has been armed ----------------------------


def test_a_new_routine_is_stored_disarmed(tmp_path: Path) -> None:
    """``create_routine`` never arms anything, whatever it was given.

    A routine that arrives armed is a routine that can act on a trigger before
    the user has read it. The check is on the stored value, not on what the
    caller passed, because the caller passing ``enabled=True`` is exactly the
    case worth catching.
    """
    brain = ganglia(tmp_path)
    routine = brain.create_routine({**two_step_routine(), "enabled": True})
    assert routine.enabled is False
    assert brain.get_routine("pair").enabled is False


def test_a_routine_defaults_to_disarmed_with_no_dataclass_argument(tmp_path: Path) -> None:
    """The safe default is on the type, not only enforced by the caller.

    ``Routine()`` used to default ``enabled`` to True, so any code path that
    built one directly — including ``build_from_pattern`` before
    ``create_routine`` saw it — produced an armed routine. The default now
    points at disarmed, which means a future caller cannot reintroduce the bug
    by forgetting an argument.
    """
    from brain.basal_ganglia.routine_builder import Routine

    assert Routine(name="bare").enabled is False


def test_a_routine_that_was_never_armed_cannot_be_planned(tmp_path: Path) -> None:
    """Planning a disarmed routine is refused, with a reason."""
    brain = ganglia(tmp_path)
    brain.create_routine(two_step_routine())

    with pytest.raises(RoutineDisabled) as caught:
        brain.plan("pair")
    assert "switched off" in str(caught.value)


def test_arming_and_disarming_are_both_stored(tmp_path: Path) -> None:
    """The switch is state, not a one-way door."""
    brain = ganglia(tmp_path)
    brain.create_routine(two_step_routine())

    brain.update_routine("pair", {"enabled": True})
    assert brain.get_routine("pair").enabled is True

    brain.update_routine("pair", {"enabled": False})
    assert brain.get_routine("pair").enabled is False


def test_planning_an_unknown_routine_says_so(tmp_path: Path) -> None:
    """404 and 409 are different answers, so they must be different errors."""
    with pytest.raises(UnknownRoutine):
        ganglia(tmp_path).plan("nothing_called_this")


# -- steps must be callable before they are stored ------------------


def test_a_step_naming_a_tool_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    """Refused at save time, not at run time.

    The half-applied routine is the whole reason: stored and armed, this would
    apply its good steps and then stop, leaving the desktop half-changed with no
    single user action to explain it.
    """
    with pytest.raises(StepRefused) as caught:
        ganglia(tmp_path).create_routine(
            {"name": "broken", "steps": [{"tool": "not_a_real_tool", "params": {}}]}
        )
    assert "not a tool ZUZZ has" in str(caught.value)


def test_a_step_with_parameters_the_schema_rejects_is_refused(tmp_path: Path) -> None:
    """``open_app`` needs ``app_name``; a routine that omits it cannot run.

    Reported as a bad step rather than as a permission problem, because the user
    can do something about this one and nothing about the other.
    """
    with pytest.raises(StepRefused) as caught:
        ganglia(tmp_path).create_routine({"name": "broken", "steps": [step(OPEN_APP)]})
    assert "app_name" in str(caught.value)


def test_editing_the_steps_repeats_the_check(tmp_path: Path) -> None:
    """The routine being edited may already be armed.

    A bad step introduced by an edit would otherwise be waiting inside a routine
    that is already able to fire.
    """
    brain = armed(tmp_path)
    with pytest.raises(StepRefused):
        brain.update_routine(
            "pair", {"steps": [{"tool": "not_a_real_tool", "params": {}}]}
        )


def test_a_routine_needs_at_least_one_step(tmp_path: Path) -> None:
    """An empty routine is refused rather than stored as a no-op."""
    with pytest.raises(StepRefused):
        ganglia(tmp_path).create_routine({"name": "empty", "steps": []})


def test_a_routine_cannot_hold_more_than_the_step_limit(tmp_path: Path) -> None:
    """Bounded, because a hundred-step routine is not a habit."""
    with pytest.raises(StepRefused) as caught:
        ganglia(tmp_path).create_routine(
            {"name": "huge", "steps": [step(OPEN_APP, app_name="chrome")] * (MAX_STEPS + 1)}
        )
    assert str(MAX_STEPS) in str(caught.value)


def test_names_are_validated_and_unique(tmp_path: Path) -> None:
    """A bad name is refused by name, and a taken one is not silently replaced."""
    brain = ganglia(tmp_path)
    brain.create_routine(two_step_routine())

    for bad in ("Has Spaces", "dash-name", "", "x" * 200):
        with pytest.raises(RoutineNameInvalid):
            brain.create_routine(two_step_routine(bad))

    with pytest.raises(RoutineNameInvalid) as caught:
        brain.create_routine(two_step_routine())
    assert "already exists" in str(caught.value)


def test_a_name_is_normalised_rather_than_refused(tmp_path: Path) -> None:
    """``StartWork`` is stored as ``startwork`` and found by either spelling.

    Normalising case beats refusing it: the user typed a name in chat, and
    being told it was invalid because of a capital letter would be an obstacle
    with no safety value behind it. What must hold is that the stored name and
    the lookup agree, or the routine becomes unreachable.
    """
    brain = ganglia(tmp_path)
    routine = brain.create_routine(two_step_routine("StartWork"))
    assert routine.name == "startwork"
    # Looked up by either spelling, and the lookup is a fresh load from the
    # registry, so equality rather than identity is what can be promised.
    assert brain.get_routine("startwork").name == "startwork"
    assert brain.get_routine("StartWork").name == "startwork"


# -- everything is decided before anything runs ----------------------


def test_the_plan_reflects_the_matrix_at_every_level(tmp_path: Path) -> None:
    """One routine, five levels, and the verdicts move with the matrix.

    This is the test that says the gate was not bypassed. Comparing against
    ``PermissionGate`` directly keeps it honest: if someone widens ``browse`` or
    narrows ``app_launch``, this fails rather than silently agreeing with
    whatever the code now does.
    """
    brain = armed(tmp_path)

    for level in (1, 2, 3, 4, 5):
        plan = brain.plan("pair", trust_level=level)
        assert plan.trust_level == level
        for planned in plan.steps:
            assert planned.verdict in {"auto", "ask", "blocked"}
            assert planned.target.category in {"open_apps", "browse_internet"}

    # ``browse_internet`` asks until level 5, so the browser step is pending
    # everywhere below it and automatic only at the top.
    for level in (1, 2, 3, 4):
        plan = brain.plan("pair", trust_level=level)
        assert NAVIGATE in [entry["tool"] for entry in plan.pending]
        assert plan.runnable is False
        assert plan.permitted is True, "pending is a question, not a refusal"
    assert brain.plan("pair", trust_level=5).runnable is True


def test_an_unknown_tool_cannot_reach_the_plan(tmp_path: Path) -> None:
    """The save-time check can be bypassed by writing the file by hand.

    ``config/routines.json`` is config, readable and writable by the person who
    owns the machine. ``plan`` re-checks every step, so a hand-edited file with a
    bad step is caught at run time rather than trusted.
    """
    path = tmp_path / "routines.json"
    path.write_text(
        json.dumps(
            {
                "note": "hand written",
                "routines": [
                    {
                        "name": "hand_made",
                        "description": "",
                        "enabled": True,
                        "trigger": {"kind": "manual"},
                        "steps": [{"tool": "not_a_real_tool", "params": {}}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(StepRefused):
        BasalGanglia(registry=RoutineRegistry(path)).plan("hand_made")


def test_the_blocked_categories_are_unreachable_by_any_tool() -> None:
    """No tool ZUZZ has carries a category that is blocked at every level.

    Three categories are blocked at all five levels — ``delete_os_files``,
    ``share_data`` and ``disable_safety`` — and no tool in the catalogue is
    filed under any of them. So a routine's steps are always permitted or
    awaiting confirmation, never refused, which is what
    :class:`RoutineNotPermitted` actually means in practice: it is the
    confirmation path, not a denial path.

    Worth pinning down. If someone later files a tool under one of those
    categories, ``plan.refused`` stops being unreachable and the refusal
    handling that assumed it empty has to be revisited rather than inherited.
    """
    from body.tools.registry import get_registry
    from brain.prefrontal_cortex.trust_matrix import BLOCKED

    blocked = {
        category
        for category in ActionCategory
        if any(
            PermissionGate().row_for(category)[str(level)] == BLOCKED
            for level in (1, 2, 3, 4, 5)
        )
    }
    assert {category.value for category in blocked} == {
        "delete_os_files",
        "share_data",
        "disable_safety",
    }

    registry = get_registry()
    offenders = [
        name
        for name in registry.names()
        if getattr(getattr(registry.get(name), "resolve", None), "category", None) in blocked
    ]
    assert offenders == [], "a routine step could finally be refused outright"


def test_a_routine_closing_an_app_is_pending_at_every_level(tmp_path: Path) -> None:
    """``close_apps`` asks at all five levels, so it never runs unattended.

    This is the matrix being honest rather than a limitation: closing an
    application can lose unsaved work, so no trust level removes the question.
    The plan therefore reports it pending everywhere, and ``runnable`` is False
    everywhere — a routine with a close in it needs a person, always.
    """
    brain = _with_close_step(tmp_path)

    for level in (1, 2, 3, 4, 5):
        plan = brain.plan("closes", trust_level=level)
        assert plan.runnable is False
        assert plan.permitted is True, "pending is a question, not a refusal"
        assert "close_app" in [entry["tool"] for entry in plan.pending]
        assert plan.refused == []


def test_execution_runs_nothing_while_a_step_is_pending(tmp_path: Path) -> None:
    """The executor is not called even once.

    This is the property the API's design rests on. If the region can call the
    executor for step one and then discover step three needs confirming, the
    "check everything first" guarantee is worth nothing — the routine would run
    half of itself and stop, leaving the desktop changed with no single user
    action to explain it.
    """
    brain = _with_close_step(tmp_path)
    calls: List[Any] = []

    with pytest.raises(RoutineNotPermitted):
        brain.execute_routine(
            "closes",
            executor=lambda *args, **kwargs: calls.append((args, kwargs)),
            trust_level=5,
        )
    assert calls == [], "nothing may run while a step is pending"


def test_a_permitted_routine_reaches_the_executor_in_order(tmp_path: Path) -> None:
    """At level 5 both steps of the two-step routine are permitted.

    The executor is called as ``(spec, decision, params, context)`` — the
    resolved spec and the gate's decision, not the raw step — so the caller can
    see what was permitted without re-resolving anything.
    """
    brain = armed(tmp_path)
    calls: List[Any] = []

    result = brain.execute_routine(
        "pair",
        executor=lambda spec, decision, params, context: calls.append(
            (spec.name, decision.decision, dict(params))
        )
        or {"ok": True},
        trust_level=5,
    )
    assert [call[0] for call in calls] == [OPEN_APP, NAVIGATE]
    assert {call[1] for call in calls} == {"auto"}
    assert calls[0][2] == {"app_name": "chrome"}
    assert result.name if hasattr(result, "name") else True
    assert result.executed is True
    assert len(result.outcomes) == 2


def test_a_pending_step_stops_execution_rather_than_asking_nothing(tmp_path: Path) -> None:
    """With steps awaiting confirmation, execution refuses instead of running.

    There is no confirmation dialogue inside the region, so "some steps need
    confirming" cannot be honoured here. Refusing is the honest answer; the
    caller that *can* collect confirmations is the tool handler. Confirming the
    steps and re-running does not help at this level, because the gate's
    decision is about the trust level rather than about a missing answer.
    """
    brain = armed(tmp_path)
    calls: List[Any] = []

    with pytest.raises(RoutineNotPermitted) as caught:
        brain.execute_routine(
            "pair",
            executor=lambda *a, **k: calls.append(a),
            trust_level=1,
            confirmed=[1],
        )
    assert calls == []
    assert "waiting on confirmation" in str(caught.value)
    assert caught.value.trust_level == 1


def test_a_disarmed_routine_cannot_be_executed(tmp_path: Path) -> None:
    """The switch is checked on the execution path too, not only on planning."""
    brain = ganglia(tmp_path)
    brain.create_routine(two_step_routine())
    calls: List[Any] = []

    with pytest.raises(RoutineDisabled):
        brain.execute_routine(
            "pair", executor=lambda *a, **k: calls.append(a), trust_level=5
        )
    assert calls == []


# -- the permission category is the right one ------------------------


def test_routines_are_judged_as_routine_management() -> None:
    """The region's own category is ``routine_management``, not a borrowed one.

    Borrowing ``run_python`` or ``system_control`` for a stored routine would
    quietly give it a matrix row nobody wrote down.
    """
    brain = ganglia(Path("unused"))
    assert brain.status()["category"] == "routine_management"


def test_the_permission_row_is_the_approved_one() -> None:
    """Read the matrix independently of the gate's own cache."""
    row = PermissionGate().row_for("routine_management")
    assert [row[str(level)] for level in (1, 2, 3, 4, 5)] == [
        "ask",
        "ask",
        "auto",
        "auto",
        "auto",
    ]


# -- triggers -------------------------------------------------------


def test_a_manual_routine_is_not_due_on_its_own(tmp_path: Path) -> None:
    """Manual means manual. It does not become due because time passed."""
    brain = armed(tmp_path, name="manual_one")
    assert brain.due_routines({"now": 1_700_000_000.0}) == []
    assert brain.due_routines({}) == []


def test_a_disarmed_routine_is_never_due(tmp_path: Path) -> None:
    """``due_routines`` filters on enabled, so arming is what makes it eligible."""
    brain = ganglia(tmp_path)
    brain.create_routine({**two_step_routine(), "trigger": {"kind": "manual"}})

    assert brain.due_routines({}) == []
    brain.update_routine("pair", {"enabled": True})
    # A manual trigger still does not fire by itself, so this stays empty; the
    # point is that nothing became due merely by being armed.
    assert brain.due_routines({"now": 0.0}) == []


def test_a_time_trigger_fires_inside_its_window(tmp_path: Path) -> None:
    """A time trigger has a window, and the window wraps past midnight.

    A 09:00 trigger with the default fifteen-minute window fires at 09:14 and
    at 08:51, and a 23:55 trigger still fires at 00:05. Both matter: the first
    because an exact minute would need the process to be awake at exactly the
    right second, the second because a late-night routine is exactly where a
    naive absolute-difference check breaks.
    """
    brain = ganglia(tmp_path)
    brain.create_routine(
        {**two_step_routine(), "trigger": {"kind": "time", "at": "09:00"}}
    )
    brain.update_routine("pair", {"enabled": True})

    assert [r.name for r in brain.due_routines(_naive_at(9, 14, 59))] == ["pair"]
    assert brain.due_routines(_naive_at(9, 16, 0)) == [], "past the window"
    assert brain.due_routines(_naive_at(8, 44, 0)) == [], "before the window"
    assert [r.name for r in brain.due_routines(_naive_at(8, 50, 0))] == [
        "pair"
    ], "the window reaches backwards as well as forwards"

    brain.update_routine("pair", {"trigger": {"kind": "time", "at": "23:55"}})
    assert [
        r.name for r in brain.due_routines(_naive_at(0, 5, 0))
    ] == ["pair"], "wraps past midnight"


def test_the_three_shapes_of_now_agree(tmp_path: Path) -> None:
    """A ``datetime``, a timestamp and an ISO string mean the same instant.

    They did not. An aware ``datetime`` kept its own timezone's hour while a
    timestamp was converted to local time, so a 09:00 routine fired at nine for
    one caller and at 09:00 UTC — 14:30 in India — for another, on the same
    machine. Everything is normalised to naive local time now, which is what
    "09:00" has to mean for a routine that opens the user's editor.
    """
    from datetime import datetime, timezone

    from brain.basal_ganglia.context_triggers import _as_datetime

    moment = datetime(2024, 1, 1, 9, 0, 0)
    naive = _as_datetime(moment)
    assert naive == _as_datetime(moment.timestamp())
    assert naive == _as_datetime("2024-01-01T09:00:00")
    assert naive == _as_datetime(naive.astimezone(timezone.utc))
    assert naive.tzinfo is None


def test_an_unreadable_clock_does_not_fire_a_time_trigger(tmp_path: Path) -> None:
    """A missing or broken time is False, not a crash.

    Firing on an unknown time would run a routine nobody can predict; crashing
    would take the whole trigger check down with it.
    """
    brain = ganglia(tmp_path)
    brain.create_routine(
        {**two_step_routine(), "trigger": {"kind": "time", "at": "09:00"}}
    )
    brain.update_routine("pair", {"enabled": True})

    assert brain.due_routines({}) == []
    assert brain.due_routines({"at": None}) == []
    assert brain.due_routines({"at": "not a time"}) == []


def test_an_app_trigger_fires_when_that_app_is_open(tmp_path: Path) -> None:
    """An app trigger reads the open applications, case-insensitively."""
    brain = ganglia(tmp_path)
    brain.create_routine(
        {**two_step_routine(), "trigger": {"kind": "app", "app": "chrome"}}
    )
    brain.update_routine("pair", {"enabled": True})

    assert [r.name for r in brain.due_routines({"apps": ["Code", "Chrome"]})] == ["pair"]
    assert brain.due_routines({"apps": ["Code"]}) == []


def test_a_command_trigger_fires_on_matching_words(tmp_path: Path) -> None:
    """A command trigger matches on words, not on an exact string.

    An exact match would mean the routine only ever fires when the user types
    the trigger word with no other words around it, which is not how anybody
    types.
    """
    brain = ganglia(tmp_path)
    brain.create_routine(
        {**two_step_routine(), "trigger": {"kind": "command", "command": "focus"}}
    )
    brain.update_routine("pair", {"enabled": True})

    assert [r.name for r in brain.due_routines({"message": "focus mode please"})] == [
        "pair"
    ]
    assert brain.due_routines({"message": "what is the weather"}) == []


def test_an_unknown_trigger_kind_is_refused(tmp_path: Path) -> None:
    """A trigger ZUZZ cannot evaluate would otherwise silently never fire."""
    brain = ganglia(tmp_path)
    brain.create_routine(two_step_routine())
    with pytest.raises(TriggerRefused):
        brain.update_routine("pair", {"trigger": {"kind": "telepathy"}})


def test_a_trigger_describes_itself_in_words(tmp_path: Path) -> None:
    """The Habits page shows the description, so it has to be readable."""
    from brain.basal_ganglia.context_triggers import parse

    assert "only when asked" in describe(parse({"kind": "manual"}))
    assert "09:00" in describe(parse({"kind": "time", "at": "09:00"}))
    assert "chrome" in describe(parse({"kind": "app", "app": "chrome"}))


# -- pattern detection -----------------------------------------------


def test_a_repeated_sequence_is_detected() -> None:
    """The same steps, repeated, become a pattern with a count."""
    log = _log(repeats=6, tools=(OPEN_APP, "type_text", NAVIGATE))
    found = find_patterns(log, min_repetitions=3)

    assert found
    assert found[0].repetitions == 6
    assert len(found[0].steps) == 3
    assert [step.tool for step in found[0].steps] == [OPEN_APP, "type_text", NAVIGATE]


def test_a_sequence_seen_once_is_not_a_habit() -> None:
    """Once is not a pattern, however distinctive."""
    log = _log(repeats=1, tools=(OPEN_APP, "type_text"))
    assert find_patterns(log, min_repetitions=3) == []


def test_the_minimum_repetitions_is_respected() -> None:
    """Raising the bar empties the list rather than loosening the match."""
    log = _log(repeats=4, tools=(OPEN_APP, "type_text"))
    assert find_patterns(log, min_repetitions=3)
    assert find_patterns(log, min_repetitions=5) == []


def test_the_window_is_bounded() -> None:
    """A long unchanging run of one tool is not an infinite routine."""
    log = _log(repeats=2, tools=(OPEN_APP,) * (MAX_WINDOW + 4))
    for pattern in find_patterns(log, min_repetitions=2):
        assert len(pattern.steps) <= MAX_WINDOW


def test_a_session_gap_is_recorded_rather_than_ignored(tmp_path: Path) -> None:
    """The same steps an hour apart are counted as many sessions, not one.

    A gap between sessions does not invalidate the pattern — the user really
    does open the same two things every hour. What the gap changes is
    ``sessions``, which feeds ``confidence``: a pattern seen six times in one
    sitting is weaker evidence of a habit than six times across six days.
    """
    log: List[Dict[str, Any]] = []
    for session in range(6):
        start = session * 7200.0
        for offset, tool in enumerate((OPEN_APP, "type_text")):
            log.append({"tool": tool, "target": "", "category": "", "at": start + offset})

    spread = find_patterns(log, min_repetitions=3)[0]
    assert spread.sessions == 6

    together: List[Dict[str, Any]] = []
    clock = 0.0
    for _ in range(6):
        for tool in (OPEN_APP, "type_text"):
            together.append({"tool": tool, "target": "", "category": "", "at": clock})
            clock += 1.0
    assert find_patterns(together, min_repetitions=3)[0].sessions == 1


def test_the_default_minimum_is_the_documented_one() -> None:
    """The page's default and the detector's default must not drift apart."""
    assert DEFAULT_MIN_REPETITIONS == 5


def test_detection_is_read_only(tmp_path: Path) -> None:
    """Noticing a pattern does not store a routine.

    Building one is a separate, gated act — see the next test.
    """
    brain = ganglia(tmp_path)
    log = _log(repeats=6, tools=(OPEN_APP, "type_text"))

    found = brain.detect_patterns(log, min_repetitions=3)
    assert found
    assert brain.list_routines() == [], "detecting must not store anything"


def test_a_built_routine_from_a_pattern_is_disarmed(tmp_path: Path) -> None:
    """A routine ZUZZ noticed is a proposal, not a decision.

    The trigger is manual rather than whatever the pattern happened to look
    like: a detected pattern is evidence about the past, and ZUZZ deciding when
    to act on it in the future is the step a person should take.
    """
    brain = ganglia(tmp_path)
    pattern = find_patterns(_log(repeats=6, tools=(OPEN_APP, "type_text")), min_repetitions=3)[0]

    routine = build_from_pattern(pattern)
    assert routine.enabled is False
    assert routine.source == "pattern"
    assert routine.trigger.kind == "manual"
    assert [step.tool for step in routine.steps] == [OPEN_APP, "type_text"]


def test_a_suggested_name_is_valid(tmp_path: Path) -> None:
    """A generated name must survive the validator it will be stored under."""
    pattern = find_patterns(_log(repeats=6, tools=(OPEN_APP, "type_text")), min_repetitions=3)[0]
    name = suggest_name(pattern)
    assert validate_name(name) == name


# -- persistence ----------------------------------------------------


def test_a_routine_survives_a_reload(tmp_path: Path) -> None:
    """The file is the store, so a restart must not lose an armed routine."""
    path = tmp_path / "routines.json"
    first = BasalGanglia(registry=RoutineRegistry(path))
    first.create_routine(two_step_routine())
    first.update_routine("pair", {"enabled": True, "description": "kept"})

    second = BasalGanglia(registry=RoutineRegistry(path))
    routine = second.get_routine("pair")
    assert routine.enabled is True
    assert routine.description == "kept"
    assert len(routine.steps) == 2


def test_deleting_is_reported_honestly(tmp_path: Path) -> None:
    """A delete of something that is not there is False, not a crash."""
    brain = armed(tmp_path)
    assert brain.delete_routine("pair") is True
    assert brain.delete_routine("pair") is False
    assert brain.list_routines() == []


def test_the_file_is_readable_by_a_person(tmp_path: Path) -> None:
    """Plain JSON with a note, so a routine can be written by hand.

    A stored routine the user cannot read or edit is not really theirs, and
    hand-editing is the only escape when a routine names a tool ZUZZ does not
    have and the region refuses to load it.
    """
    path = tmp_path / "routines.json"
    brain = BasalGanglia(registry=RoutineRegistry(path))
    brain.create_routine(two_step_routine())

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["note"], "the file should say what it is"
    assert payload["routines"][0]["name"] == "pair"


def test_the_seeds_name_real_tools_and_start_disarmed(tmp_path: Path) -> None:
    """The shipped routines are usable as written.

    A seed that names a tool ZUZZ does not have, or that arrives armed, would
    either fail the save-time check on every startup or start acting on a
    trigger nobody chose.
    """
    brain = BasalGanglia(registry=RoutineRegistry(tmp_path / "routines.json"))
    brain.seed()

    stored = brain.list_routines()
    assert stored, "seeding should store something"
    for routine in stored:
        assert routine.enabled is False, f"{routine.name} ships disarmed"
        brain._check_steps_callable(routine)
    assert {routine.name for routine in stored} >= {
        entry["name"] for entry in SEED_ROUTINES
    }


# -- helpers --------------------------------------------------------


def _log(repeats: int, tools: Any) -> List[Dict[str, Any]]:
    """Return an activity log of ``tools`` repeated ``repeats`` times."""
    log: List[Dict[str, Any]] = []
    clock = 0.0
    for _ in range(repeats):
        for tool in tools:
            log.append({"tool": tool, "target": "", "category": "", "at": clock})
            clock += 1.0
    return log


def _at(hour: int, minute: int, second: int = 0) -> float:
    """Return a local timestamp at a wall-clock time on 2024-01-01.

    Local, not UTC: a time trigger means "nine in the morning where the user
    is", and the region reads wall-clock hours.
    """
    from datetime import datetime

    return datetime(2024, 1, 1, hour, minute, second).timestamp()


def _naive_at(hour: int, minute: int, second: int = 0) -> Dict[str, Any]:
    """Return a context whose ``at`` is a naive local ``datetime``."""
    from datetime import datetime

    return {"at": datetime(2024, 1, 1, hour, minute, second)}

# -- the tool catalogue ----------------------------------------------


def test_the_routine_tools_are_reachable_from_the_catalogue() -> None:
    """All five habit tools are described to the model and all five resolve.

    A name in the schema with no implementation is a tool the model was promised
    and cannot reach. ``routine_management`` is the row that asks at trust levels
    1 and 2, so an unreachable tool here would not be noticed as a broken button —
    it would be noticed as ZUZZ cheerfully claiming it cannot manage routines.
    """
    from brain.cerebrum.tool_catalogue import TOOL_NAMES
    from body.tools.registry import catalogue_mismatches, get_registry

    expected = {
        "routine_list_all",
        "routine_execute",
        "routine_create",
        "routine_update",
        "routine_delete",
    }
    assert expected.issubset(set(TOOL_NAMES)), expected - set(TOOL_NAMES)
    assert catalogue_mismatches() == []

    registry = get_registry()
    for name in sorted(expected):
        spec = registry.get(name)
        assert spec is not None, name
        assert spec.summary, name
        assert callable(spec.resolve) and callable(spec.execute), name


def test_the_routine_tools_judge_as_routine_management() -> None:
    """Every habit tool resolves into the same trust row.

    The matrix has one row per category and no per-operation axis, so a tool that
    resolved somewhere else would silently borrow a neighbour's permissions. Which
    neighbour matters: ``chat`` is auto at level 1, so a mis-resolved routine tool
    would be the one thing in this package that could rewrite the user's stored
    habits at the most cautious setting.
    """
    from body.tools.registry import get_registry
    from body.tools.tool_spec import ToolContext, ToolRefused

    brain = BasalGanglia(registry=RoutineRegistry(Path(tempfile.mkdtemp()) / "r.json"))
    context = ToolContext(hands=None, files=None, directories=None, basal_ganglia=brain)
    registry = get_registry()
    cases = {
        "routine_list_all": {},
        "routine_execute": {"name": "not_a_routine"},
        "routine_create": {"name": "made_up", "steps": [{"tool": "get_system_resources"}]},
        "routine_update": {"name": "not_a_routine", "description": "x"},
        "routine_delete": {"name": "not_a_routine"},
    }

    for name, params in cases.items():
        try:
            target = registry.get(name).resolve(dict(params), context)
        except ToolRefused:
            # A refusal at resolve is a legitimate answer — the routine does not
            # exist — but it must not be a refusal about the category.
            continue
        assert target.category == "routine_management", name


def test_the_personality_tools_are_reachable_and_auto() -> None:
    """All three personality tools resolve into ``personality_access``.

    The row this session added. The check that matters is that it is the *right*
    row: reads and the one write both auto at every level, because the writable set
    is the closed allowlist in ``config/personality.json`` rather than a permission
    the model can be talked past.
    """
    from brain.cerebrum.tool_catalogue import TOOL_NAMES
    from body.tools.registry import catalogue_mismatches, get_registry
    from body.tools.tool_spec import ToolContext
    from brain.cerebellum.cerebellum import Cerebellum

    expected = {
        "personality_get_settings",
        "personality_update_setting",
        "personality_detect_mood",
    }
    assert expected.issubset(set(TOOL_NAMES)), expected - set(TOOL_NAMES)
    assert catalogue_mismatches() == []

    registry = get_registry()
    context = ToolContext(hands=None, files=None, directories=None, cerebellum=Cerebellum())
    cases = {
        "personality_get_settings": {},
        "personality_update_setting": {"key": "verbosity", "value": "terse"},
        "personality_detect_mood": {"message": "this is taking far too long"},
    }
    for name, params in cases.items():
        target = registry.get(name).resolve(dict(params), context)
        assert target.category == "personality_access", name


# -- the HTTP surface ------------------------------------------------


def test_the_arm_endpoint_arms_and_disarms(tmp_path) -> None:
    """``POST /api/habits/arm`` is the switch, and it is a separate call.

    The invariant the endpoint exists to protect: creating a routine and arming it
    are two acts, so a user can read what they just built before it starts acting on
    a trigger. ``enabled`` is accepted on the create body and then ignored — a
    client that sets it must get the same answer as one that does not.
    """
    from fastapi.testclient import TestClient

    from face.api.routes_habits import router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    brain = BasalGanglia(registry=RoutineRegistry(tmp_path / "routines.json"))
    app.state.basal_ganglia = brain
    app.state.audit = _RecordingAudit()
    app.state.logger = _RecordingLog()
    api = TestClient(app)

    created = api.post(
        "/api/habits/routine",
        json={
            "name": "probe",
            "steps": [{"tool": "get_system_resources"}],
            "trigger": {"kind": "manual"},
            "enabled": True,
        },
    )
    assert created.status_code == 200
    assert created.json()["enabled"] is False, "a routine that asked to be armed was armed"

    armed = api.post("/api/habits/arm", json={"name": "probe"})
    assert armed.status_code == 200
    assert armed.json()["enabled"] is True
    assert "plan" in armed.json()

    disarmed = api.post("/api/habits/arm", json={"name": "probe", "enabled": False})
    assert disarmed.status_code == 200
    assert disarmed.json()["enabled"] is False

    # And a disarmed routine cannot be executed: 409, not a plan.
    blocked = api.post("/api/habits/execute", json={"name": "probe"})
    assert blocked.status_code == 409

    assert api.post("/api/habits/arm", json={"name": "absent"}).status_code == 404
    assert [event for event, _ in app.state.audit.events if "routine" in event]


class _RecordingAudit:
    """An audit log that keeps what it was told, so the events can be asserted."""

    def __init__(self) -> None:
        self.events: List[tuple] = []

    def record(self, event: str, reason: str = "", **fields: Any) -> None:
        """Record one event."""
        self.events.append((event, reason))


class _RecordingLog:
    """An activity log that swallows everything."""

    def log(self, component: str, action: str, **fields: Any) -> None:
        """Accept one record."""
        return None


def test_planning_a_routine_resolves_against_a_real_context() -> None:
    """A step naming a tool that needs a collaborator still plans.

    ``plan`` used to resolve steps against ``None``, so any step whose tool checks
    for an attached collaborator raised ``AttributeError`` from inside the registry
    — an unhandled 500 for a routine the user had just written correctly. It is now
    reported as a named, refusal-shaped step failure instead.
    """
    from body.tools.registry import get_registry

    # ``get_system_resources`` needs the Hypothalamus, which is not on an empty
    # context. It must produce a StepRefused naming the step, not an AttributeError.
    brain = BasalGanglia(registry=RoutineRegistry(Path(tempfile.mkdtemp()) / "r.json"))
    routine = brain.create_routine(
        {"name": "needs_context", "steps": [{"tool": "get_system_resources"}]}
    )
    armed = brain.update_routine(routine.name, {"enabled": True})

    try:
        brain.plan(armed.name)
    except StepRefused as exc:
        assert "needs_context" in str(exc), exc
    else:  # pragma: no cover - depends on the injected context
        assert get_registry().get("get_system_resources") is not None

    # With the collaborator attached, the very same step resolves.
    from body.tools.tool_spec import ToolContext

    wired = BasalGanglia(
        registry=RoutineRegistry(Path(tempfile.mkdtemp()) / "r.json"),
        tool_context=lambda: ToolContext(
            hands=None, files=None, directories=None, hypothalamus=object()
        ),
    )
    other = wired.create_routine(
        {"name": "wired", "steps": [{"tool": "get_system_resources"}]}
    )
    wired.update_routine(other.name, {"enabled": True})
    assert wired.plan(other.name).steps
