"""Priority 2 — the bypass audit, made executable.

Session 8's lesson was that a safety chain verified component by component can
still have one entry point that sidesteps the whole thing. Three times, in fact:
the VRAM check on cloud levels, the unguarded fallback rungs, and the streaming
path. This file is the answer to that class of bug — the entries found by the
audit, each pinned so it cannot come back.

Every test here asserts that something *did not happen* (a threshold unchanged, a
local inference skipped, a request refused) rather than that a function was
called. A test that proves a guard ran is not the same as a test that proves the
guard stopped something.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from brain.amygdala.threat_detector import ThreatDetector
from brain.hypothalamus.model_manager import ModelManager
from brain.hypothalamus.vram_budget import VRAMBudget
from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.permission_gate import PermissionGate

TEST_MODELS = {
    "tiny-model": {"vram_mb": 600, "ram_mb": 200, "priority": "on_demand", "role": "vision"},
    "core-model": {"vram_mb": 700, "ram_mb": 300, "priority": "always_loaded", "role": "fast_responses"},
}


@pytest.fixture(autouse=True)
def isolate_persistent_state(kill_switch_app, monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Keep this module off the developer's real trust level and audit log.

    Added after this module broke the suite by writing to both. ``app.state.trust``
    is a ``TrustManager`` built against ``storage/trust_level.json``, so saving a
    level from a test raised the *machine's* trust to 5 and every later test
    inherited it — a safety test that quietly disabled a safety setting, which is
    the worst possible direction for this kind of bug. The audit log had the
    mirror problem: pressure and thermal events from synthetic budgets were
    written into the operator's real audit trail as though they had happened.

    Both are redirected into ``tmp_path`` for the duration of each test here.
    """
    from brain.prefrontal_cortex.trust_manager import TrustManager

    kill_switch_app.state.trust = TrustManager(tmp_path / "trust_level.json")

    recorded: List[Dict[str, Any]] = []

    class RecordingAudit:
        """Captures events instead of writing them to disk."""

        def record(self, event: str, summary: str = "", **fields: Any) -> None:
            """Keep the event in memory."""
            recorded.append({"event": event, "summary": summary, **fields})

    kill_switch_app.state.audit = RecordingAudit()
    kill_switch_app.state.hypothalamus._audit = kill_switch_app.state.audit
    kill_switch_app.state.hypothalamus.models._audit = kill_switch_app.state.audit
    return recorded


class SpyCerebrum:
    """Records which levels reached the backend, so 'no inference' is visible."""

    def __init__(self) -> None:
        """Start unreached."""
        self.asked: List[int] = []
        self.streams: List[int] = []

    async def think(self, message: str, level: int = 1, context: Any = None) -> Dict[str, Any]:
        """Record the level and answer."""
        self.asked.append(level)
        return {
            "reply": "an answer",
            "provider": "local",
            "model": "stub",
            "level": level,
            "chars": 8,
        }

    async def stream(self, message: str, level: int = 1):
        """Record the level and yield one fragment."""
        self.streams.append(level)
        yield "an answer"

    def think_local(self, *a: Any, **k: Any):
        """Present so the object satisfies the router's expectations."""
        raise AssertionError("think_local must not be called in these tests")


# -- Bypass 1: the watchdog could be disarmed by one unauthenticated POST ------


def test_the_watchdog_can_be_tightened_without_permission(kill_switch_app) -> None:
    """Making the watchdog stricter needs no dialog.

    Permitted deliberately: a lower limit fires sooner, so it cannot reduce
    safety, and an operator reacting to something that looks like runaway work
    should not have to raise a permission prompt to respond to it.
    """
    client = TestClient(kill_switch_app)
    before = kill_switch_app.state.watchdog.deletion_limit

    response = client.post("/api/kill-switch/watchdog", json={"deletion_limit": before - 1})

    assert response.status_code == 200, response.text
    assert kill_switch_app.state.watchdog.deletion_limit == before - 1


def test_the_watchdog_cannot_be_loosened(kill_switch_app) -> None:
    """The hole the audit found: raising a limit disarms the signal.

    Every limit is an upper bound the watchdog trips at, so a larger number means
    the signal fires later or never. Before the fix this was a single POST with no
    authentication, no gate and no kill-switch check, which made the mass-deletion
    scenario — the one the watchdog exists for — defeatable on request.
    """
    client = TestClient(kill_switch_app)
    before = kill_switch_app.state.watchdog.deletion_limit

    response = client.post("/api/kill-switch/watchdog", json={"deletion_limit": 100_000})

    assert response.status_code == 403, response.text
    assert kill_switch_app.state.watchdog.deletion_limit == before, "the limit was loosened"
    assert "tightened" in response.json()["detail"]


def test_the_watchdog_cannot_be_loosened_at_full_trust(kill_switch_app) -> None:
    """Even trust level 5 cannot loosen it, because the matrix blocks it.

    This is the part that matters. A refusal that depends on a trust level can be
    raised by anyone who can change the level; ``disable_safety`` is ``blocked``
    at all five, so there is no configuration of this machine on which the
    watchdog can be turned off from here.
    """
    app = kill_switch_app
    app.state.trust.save(5)
    assert app.state.trust.current() == 5

    before = app.state.watchdog.deletion_limit
    response = TestClient(app).post("/api/kill-switch/watchdog", json={"deletion_limit": 100_000})

    assert response.status_code == 403
    assert app.state.watchdog.deletion_limit == before


def test_a_zero_threshold_is_refused_because_it_always_fires(kill_switch_app) -> None:
    """Zero looks like the strictest setting and is the opposite.

    ``deletions >= deletion_limit`` is true for any count once the limit is zero,
    so the watchdog would trip on every single poll — a self-inflicted denial of
    service that an operator could mistake for "safety is very strict".
    """
    client = TestClient(kill_switch_app)
    before = kill_switch_app.state.watchdog.deletion_limit

    response = client.post("/api/kill-switch/watchdog", json={"deletion_limit": 0})

    assert response.status_code == 400
    assert kill_switch_app.state.watchdog.deletion_limit == before


def test_a_negative_threshold_is_still_refused(kill_switch_app) -> None:
    """The pre-existing check still holds."""
    response = TestClient(kill_switch_app).post(
        "/api/kill-switch/watchdog", json={"cpu_percent": -5}
    )
    assert response.status_code == 400


# -- Bypass 2: a forced level reached local inference unchecked ---------------


def _app_with_budget(kill_switch_app, allocated_mb: float):
    """Return ``(app, spy)`` with the VRAM budget filled to a chosen level.

    The manager is rebuilt around the Hypothalamus' *existing* budget object rather
    than a fresh one. The predictor holds a reference to that same object, so
    filling a replacement would leave the prediction reading an empty budget and
    every level would look affordable — a false all-clear in the test rather than
    a finding about the code.
    """
    app = kill_switch_app
    spy = SpyCerebrum()
    app.state.thalamus.cerebrum = spy
    app.state.hypothalamus.models = ModelManager(
        budget=app.state.hypothalamus.budget,
        models=TEST_MODELS,
        transport=_NoTransport(),
        gate=app.state.permission_gate,
        trust_level=3,
    )
    app.state.thalamus.hypothalamus = app.state.hypothalamus
    app.state.hypothalamus.budget.assign("filler", allocated_mb)
    return app, spy


class _NoTransport:
    """Stands in for Ollama; nothing in these tests loads anything."""

    async def load(self, model: str, timeout: float) -> None:
        """No-op."""

    async def unload(self, model: str, timeout: float) -> None:
        """No-op."""


def test_a_forced_local_level_is_refused_when_the_card_is_full(kill_switch_app) -> None:
    """Naming a level must not be a way around the resource check.

    ``POST /api/chat`` with an explicit level calls the Cerebrum directly instead
    of going through the routing ladder, so it skipped the ladder's per-rung
    resource check entirely. Forcing ``level: 1`` was therefore a complete bypass
    of the VRAM budget and the thermal guardian — the same defect class as the
    three Session 8 found, reached by a different door.
    """
    app, spy = _app_with_budget(kill_switch_app, 4608)
    response = TestClient(app).post("/api/chat", json={"message": "hi", "level": 1})

    assert response.status_code == 409, response.text
    assert spy.asked == [], "local inference happened with the card full"


def test_a_forced_local_level_is_refused_when_the_machine_is_too_hot(kill_switch_app) -> None:
    """The thermal guard must hold on the forced path too.

    Cloud-only mode is set by the guardian with no gate and no permission — it is
    the system's own evidence about the hardware — so a route that skips the
    resource check ignores it entirely.
    """
    app, spy = _app_with_budget(kill_switch_app, 0)
    app.state.hypothalamus.models.cloud_only = True

    response = TestClient(app).post("/api/chat", json={"message": "hi", "level": 2})

    assert response.status_code == 409
    assert spy.asked == [], "local inference happened on a machine in cloud-only mode"


def test_a_forced_local_level_still_works_when_there_is_room(kill_switch_app) -> None:
    """The new check must not turn a valid request into a refusal.

    Without this the fix would be indistinguishable from breaking the feature.
    """
    app, spy = _app_with_budget(kill_switch_app, 600)
    response = TestClient(app).post("/api/chat", json={"message": "hi", "level": 1})

    assert response.status_code == 200, response.text
    assert spy.asked == [1]


def test_a_forced_cloud_level_is_not_refused_for_vram(kill_switch_app) -> None:
    """Levels 3 and above use no local VRAM, so the budget has nothing to say.

    The mirror of the Session 8 bug: refusing a cloud request because the GPU is
    busy would push the work back to a local attempt, burning the very VRAM that
    was full.
    """
    app, spy = _app_with_budget(kill_switch_app, 4608)
    response = TestClient(app).post("/api/chat", json={"message": "hi", "level": 3})

    assert response.status_code == 200, response.text
    assert spy.asked == [3]


def test_the_refusal_names_the_level_and_suggests_the_alternative(kill_switch_app) -> None:
    """The message has to be actionable, not just a rejection.

    Refused rather than quietly rerouted, because the caller named a level and
    silently answering from a cloud provider instead would answer a different
    request — and would send the text off the machine without saying so.
    """
    app, _ = _app_with_budget(kill_switch_app, 4608)
    detail = TestClient(app).post(
        "/api/chat", json={"message": "hi", "level": 1}
    ).json()["detail"]

    assert "level 1" in detail
    assert "Omit `level`" in detail


def test_the_router_exposes_the_check_the_route_uses(kill_switch_app) -> None:
    """The route asks the Router rather than reaching into a private method.

    Otherwise the resource rules would live in two places and drift, which is how
    the original bypass happened: the route knew about levels and the Router knew
    about VRAM, and nothing connected them.
    """
    app, _ = _app_with_budget(kill_switch_app, 4608)
    router = app.state.thalamus

    assert router.check_level_allowed(1) != ""
    assert router.check_level_allowed(3) == ""
    assert callable(router.check_level_allowed)


# -- Bypass 3: the emergency stop button in the UI did not work --------------


def test_the_routes_the_frontend_calls_actually_exist(kill_switch_app) -> None:
    """Every kill-switch URL in the frontend resolves to a real route.

    Found by reading the audit's output rather than by running the browser: the
    Desktop tab's Stop button posted to ``/api/kill-switch/trigger`` and polled
    ``/api/kill-switch/status``, neither of which exists. Both 404'd, so the
    emergency stop control in the UI silently did nothing.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent
    frontend = root / "face" / "frontend"
    routes_file = root / "face" / "api" / "routes_kill_switch.py"

    declared = set()
    for match in re.finditer(
        r'@router\.(?:get|post|websocket)\("([^"]+)"', routes_file.read_text(encoding="utf-8")
    ):
        declared.add(match.group(1))

    called: List[str] = []
    for script in frontend.rglob("*.js"):
        for line in script.read_text(encoding="utf-8").splitlines():
            # Comment lines are skipped: this test is about URLs the browser
            # actually requests, and prose that *names* a wrong URL — as the
            # comment explaining this very fix does — is not a call.
            stripped = line.strip()
            if stripped.startswith(("//", "*", "/*")):
                continue
            called.extend(
                re.findall(r"""['"`](/api/kill-switch[^'"`]*)['"`]""", line)
            )

    assert called, "no kill-switch calls found in the frontend; the scan is wrong"
    missing = sorted({url for url in called if url not in declared})
    assert not missing, f"frontend calls routes that do not exist: {missing}"


def test_the_stop_route_engages_the_switch(kill_switch_app) -> None:
    """And the route the button now calls does what the button claims."""
    app = kill_switch_app
    assert app.state.kill_switch.is_killed() is False

    response = TestClient(app).post(
        "/api/kill-switch/stop", json={"reason": "pressed in the Desktop tab", "source": "ui"}
    )

    assert response.status_code == 200, response.text
    assert app.state.kill_switch.is_killed() is True
    assert "status" in response.json(), "the UI reads result.data.engaged from the status route"


# -- The gate itself, as the audit found it relied on -----------------------


def test_disable_safety_is_blocked_at_every_trust_level() -> None:
    """The row the watchdog fix leans on, asserted directly.

    The fix depends on a config value, and config values change. If someone
    relaxes this row the watchdog becomes adjustable at some level, and this test
    says so rather than leaving it to be discovered in production.
    """
    from core.config import get_trust_levels

    row = get_trust_levels()["actions"]["disable_safety"]
    assert set(row.values()) == {"blocked"}, (
        f"disable_safety is no longer blocked everywhere: {row}"
    )
    for level in range(1, 6):
        decision = PermissionGate().decide(
            Action(category=ActionCategory.DISABLE_SAFETY, target="deletion_limit"), level
        )
        assert decision.allowed is False, f"level {level} could disable safety"


def test_inference_is_gated_for_every_cerebrum_entry_point(tmp_path) -> None:
    """All three inference entry points consult the switch, not just one.

    The audit's most important structural finding: ``think_local`` was protected
    while ``think_cloud`` and ``stream`` were not, because the guard had been
    added to the one function someone happened to be looking at.
    """
    from brain.cerebrum.cerebrum import Cerebrum, InferenceRefused
    from brain.spinal_cord.kill_switch import KillSwitch
    from brain.spinal_cord.stop_state import SOURCE_UI

    switch = KillSwitch(paths=[tmp_path / "stop.flag"])
    switch.trigger(source=SOURCE_UI, reason="audit")
    cerebrum = Cerebrum(gate=PermissionGate(kill_switch=switch), trust_level=5)

    async def drain() -> List[str]:
        """Consume the stream."""
        return [f async for f in cerebrum.stream("hi", 1)]

    with pytest.raises(InferenceRefused):
        asyncio.run(cerebrum.think_local("hi", 1))
    with pytest.raises(InferenceRefused):
        asyncio.run(cerebrum.think_cloud("hi", 3))
    with pytest.raises(InferenceRefused):
        asyncio.run(drain())


def test_the_threat_detector_is_reachable_only_through_a_gate() -> None:
    """The Amygdala scores; it never grants. Confirmed rather than assumed.

    Worth pinning because a detector that could allow something would invert the
    safety argument: the whole design is that scoring can only ever make an action
    more restricted.
    """
    detector = ThreatDetector()
    action = Action(category=ActionCategory.DELETE_OS_FILES, target=r"C:\Windows")

    assessment = detector.assess(action)
    assert assessment.level in ("dangerous", "forbidden")

    for level in range(1, 6):
        decision = PermissionGate().decide(action, level, assessment)
        assert decision.allowed is False, f"level {level} allowed an OS delete"
