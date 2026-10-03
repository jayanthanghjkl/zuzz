"""Hypothalamus integration tests.

The unit suites prove each component is correct in isolation. These prove they are
*connected* — that the Thalamus really consults the Hypothalamus, that the
reaper really respects the budget, that the Amygdala is really asked about swap
bursts, and that the API really wires them together.

An integration that is designed but never called is the failure mode this file
exists to catch. Each test therefore asserts on the observable effect on the
*caller's* side, not on that a method was invoked.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from brain.brainstem.process_manager import ProcessManager
from brain.hypothalamus.hypothalamus import Hypothalamus
from brain.hypothalamus.model_manager import ModelManager
from brain.hypothalamus.vram_budget import VRAMBudget
from brain.thalamus.router import Router
from core.config import get_resource_limits

TEST_MODELS = {
    "fast": {"vram_mb": 800, "ram_mb": 400, "priority": "always_loaded", "role": "fast_responses"},
    "medium": {"vram_mb": 1400, "ram_mb": 600, "priority": "on_demand", "role": "complex_responses", "idle_timeout_seconds": 60},
    "vision": {"vram_mb": 1000, "ram_mb": 400, "priority": "on_demand", "role": "vision", "idle_timeout_seconds": 60},
    "enormous": {"vram_mb": 4200, "ram_mb": 900, "priority": "on_demand", "role": "deep_reasoning"},
}


class FakeTransport:
    """Records loads instead of touching VRAM."""

    def __init__(self) -> None:
        """Start empty."""
        self.calls: List[tuple] = []
        self.resident: set = set()

    async def load(self, model: str, timeout: float) -> None:
        """Record a load."""
        self.calls.append(("load", model))
        self.resident.add(model)

    async def unload(self, model: str, timeout: float) -> None:
        """Record an unload."""
        self.calls.append(("unload", model))
        self.resident.discard(model)


class StubCerebrum:
    """A Cerebrum that records which level it was asked for."""

    def __init__(self, fail_levels: tuple = ()) -> None:
        """Optionally fail chosen levels.

        Args:
            fail_levels: Levels whose ``think`` should raise.
        """
        self.asked: List[int] = []
        self.fail_levels = set(fail_levels)
        self.last_provider = "local"
        self.history: List[str] = []
        self.bridge = self._Bridge()

    async def think(self, message: str, level: int) -> Dict[str, Any]:
        """Record the level and answer, or fail if configured to."""
        self.asked.append(level)
        if level in self.fail_levels:
            from brain.cerebrum.cerebrum import CerebrumError

            raise CerebrumError(f"stub failure at level {level}")
        return {"reply": f"answer at {level}", "provider": "local", "model": "stub", "level": level}

    async def stream(self, message: str, level: int) -> Any:
        """Record the level and yield two fragments, like the real backend."""
        self.asked.append(level)
        yield f"streamed at {level} "
        yield "done"

    class _Bridge:
        """A bridge reporting one configured provider."""

        def configured_providers(self) -> List[str]:
            """Return provider names, so the fallback ladder builds."""
            return ["groq"]

        kill_switch: Any = None


def brain_with(total_mb: int = 4608, headroom_mb: int = 1500) -> Hypothalamus:
    """Return an orchestrator wired to fakes.

    Args:
        total_mb: The hard cap.
        headroom_mb: The reserved headroom.

    Returns:
        A Hypothalamus that cannot reach hardware.
    """
    from tests.test_hypothalamus import FakeAudit, FakeLogger, FakeMonitor

    budget = VRAMBudget(total_mb=total_mb, headroom_mb=headroom_mb)
    return Hypothalamus(
        monitor=FakeMonitor(),
        budget=budget,
        models=ModelManager(
            budget=budget,
            models=TEST_MODELS,
            transport=FakeTransport(),
            audit=FakeAudit(),
            logger=FakeLogger(),
        ),
    )


def test_the_router_is_consulted_and_skips_a_local_level_that_will_not_fit() -> None:
    """A task that cannot fit is answered by the next level instead.

    The observable assertion is that the Cerebrum was never asked for the local
    level at all. Checking that ``request_resources`` was called would prove only
    that a spy was called; what matters is that the expensive local call did not
    happen.
    """
    cerebrum = StubCerebrum()
    brain = brain_with()
    router = Router(cerebrum=cerebrum)
    router.hypothalamus = brain

    # Fill the usable space so nothing local can be attempted.
    brain.models.budget.assign("filler", brain.budget.usable_mb - 1)

    result = asyncio.run(router.route("explain the architecture of this project"))
    assert cerebrum.asked[0] >= 3, "a local level was attempted with no VRAM room"
    assert result["level"] == cerebrum.asked[0]


def test_a_fitting_task_is_still_routed_locally() -> None:
    """Wiring the Hypothalamus in must not degrade normal routing.

    A resource check that always says no is indistinguishable from a broken
    router: every answer would go to the cloud and the user's free local models
    would never be used.
    """
    cerebrum = StubCerebrum()
    brain = brain_with()
    router = Router(cerebrum=cerebrum)
    router.hypothalamus = brain

    result = asyncio.run(router.route("what is 2 + 2"))
    assert cerebrum.asked[0] <= 2, "an ordinary question went to the cloud"
    assert result["provider"] == "local"


def test_thermal_cloud_only_stops_local_inference() -> None:
    """A hot machine stops local work rather than adding to the heat."""
    from tests.test_hypothalamus import FakeMonitor

    cerebrum = StubCerebrum()
    mon = FakeMonitor(gpu_temp=95)
    brain = Hypothalamus(
        monitor=mon,
        budget=VRAMBudget(total_mb=4608, headroom_mb=1500),
        models=ModelManager(
            budget=VRAMBudget(total_mb=4608, headroom_mb=1500),
            models=TEST_MODELS,
            transport=FakeTransport(),
        ),
    )
    router = Router(cerebrum=cerebrum)
    router.hypothalamus = brain

    asyncio.run(brain.tick())
    assert brain.models.cloud_only is True

    asyncio.run(router.route("summarise this codebase for me"))
    assert cerebrum.asked[0] >= 3, "local inference was attempted on a hot machine"


def test_a_failing_resource_check_does_not_block_the_answer() -> None:
    """An unreadable resource layer must not take routing down with it.

    The check is advisory: if it cannot answer, the router proceeds as it did
    before it was attached. Losing the answer entirely because a sensor was
    unhappy is a worse failure than losing the optimisation.
    """

    class BrokenHypothalamus:
        """A resource layer that raises on every question."""

        models: Any = None

        def request_resources(self, task_type: str) -> Dict[str, Any]:
            """Fail, as an unreadable sensor would."""
            raise RuntimeError("sensor unavailable")

    cerebrum = StubCerebrum()
    router = Router(cerebrum=cerebrum)
    router.hypothalamus = BrokenHypothalamus()

    result = asyncio.run(router.route("hello there"))
    assert cerebrum.asked[0] <= 2
    assert result["provider"] == "local"


def test_a_cloud_level_is_never_refused_for_vram() -> None:
    """A busy GPU does not block cloud work, and must not push it back to local.

    Levels 3 and above run on a provider and use no local VRAM. If the budget
    were consulted for them, a full card would refuse the cloud answer and fall
    straight through to a local attempt — burning the very VRAM that was full,
    and losing the user's privacy guarantee in the process.
    """
    cerebrum = StubCerebrum()
    brain = brain_with()
    router = Router(cerebrum=cerebrum)
    router.hypothalamus = brain
    brain.models.budget.assign("filler", 9999)

    assert router._resource_block(3) == ""
    assert router._resource_block(4) == ""
    assert router._resource_block(2) != "", "a local level was allowed with no VRAM"

    asyncio.run(router.route("explain the architecture of this project"))
    assert cerebrum.asked[0] >= 3, "a full GPU pushed the answer back to local"


def test_resource_skips_are_reported_as_degradation() -> None:
    """A skipped level is visible to the caller, not silently swallowed.

    The dashboard shows what answered the question. If the first choice was
    skipped for resources and a later level answered, the caller has to be able
    to see that it was not answered the way it was asked.
    """
    cerebrum = StubCerebrum()
    brain = brain_with()
    router = Router(cerebrum=cerebrum)
    router.hypothalamus = brain
    brain.models.budget.assign("filler", 9999)

    result = asyncio.run(router.route("what is 2 + 2"))
    assert result["classified_level"] == 1
    assert cerebrum.asked == [3], "the ladder did not fall through to the cloud"
    assert result["degraded"] is True
    assert len(result["resource_skips"]) == 2, "a skip went unreported"
    assert all("not attempting local inference" in s for s in result["resource_skips"])


def test_a_ladder_of_only_local_levels_says_why_it_refused() -> None:
    """Refusing every local rung raises with the reasons, not an empty error."""
    cerebrum = StubCerebrum()
    brain = brain_with()
    router = Router(cerebrum=cerebrum)
    router.hypothalamus = brain
    brain.models.budget.assign("filler", 9999)

    with pytest.raises(Exception) as caught:
        asyncio.run(router.route("what is 2 + 2", internet_available=False))
    assert cerebrum.asked == []
    assert "not attempting local inference" in str(caught.value)


async def _drain(agen: Any) -> list:
    """Collect every event an async generator yields."""
    return [event async for event in agen]


def test_streaming_consults_the_hypothalamus_too() -> None:
    """The chat UI's path is the one that must not bypass the resource layer.

    Streaming is what the UI actually calls, so a check applied only to
    ``route()`` would leave the primary path through the brain running local
    inference on a machine that is too hot or out of room.
    """
    cerebrum = StubCerebrum()
    brain = brain_with()
    router = Router(cerebrum=cerebrum)
    router.hypothalamus = brain
    brain.models.budget.assign("filler", 9999)

    events = asyncio.run(_drain(router.route_stream("what is 2 + 2")))
    done = events[-1]
    assert cerebrum.asked == [3], "a full GPU still streamed from local"
    assert done["type"] == "done"
    assert done["streamed_level"] == 3
    assert done["degraded"] is True
    assert done["resource_skips"]


def test_streaming_uses_local_inference_when_the_budget_allows_it() -> None:
    """The stream check must not push ordinary traffic to the cloud either."""
    cerebrum = StubCerebrum()
    brain = brain_with()
    router = Router(cerebrum=cerebrum)
    router.hypothalamus = brain

    events = asyncio.run(_drain(router.route_stream("what is 2 + 2")))
    assert cerebrum.asked == [1]
    assert events[-1]["streamed_level"] == 1
    assert events[-1]["degraded"] is False
    assert events[-1]["resource_skips"] == []


def test_streaming_says_why_it_refused_when_there_is_no_cloud() -> None:
    """With nowhere to escalate to, streaming explains itself instead of breaking.

    A stream that raises before its first token reaches the user as a failed
    response. Saying "the machine is too hot and no provider is configured" is
    more use than that, and is the same information the non-streaming path gives.
    """
    cerebrum = StubCerebrum()
    cerebrum.bridge.configured_providers = lambda: []
    brain = brain_with()
    router = Router(cerebrum=cerebrum)
    router.hypothalamus = brain
    brain.models.budget.assign("filler", 9999)

    with pytest.raises(Exception) as caught:
        asyncio.run(_drain(router.route_stream("what is 2 + 2", internet_available=False)))
    assert cerebrum.asked == []
    assert "no cloud provider is configured" in str(caught.value)


def test_a_pressure_sweep_escalates_rather_than_only_evicting() -> None:
    """Eviction alone is not always enough, and the config says what comes next.

    Two failures eviction cannot fix, handled at two different tiers: a card that
    frees models but is still full will simply be refilled by the next request,
    and a card with nothing left to evict needs local inference switched off
    rather than attempted and failed inside the driver.
    """
    budget = VRAMBudget(total_mb=4608, headroom_mb=1500)
    manager = ModelManager(
        budget=budget,
        models=TEST_MODELS,
        transport=FakeTransport(),
    )

    # Between emergency (95) and panic (98): evict, then hold off on new loads.
    budget.assign("hog", 4500)
    result = asyncio.run(manager.memory_pressure_handler())
    assert result["triggered"] is True
    assert result.get("emergency_cloud") is True
    assert manager.cloud_only is True
    assert "panic" not in result

    # At panic: nothing left to give, so local inference goes off entirely.
    budget.assign("hog", 4600)
    result = asyncio.run(manager.memory_pressure_handler())
    assert result.get("panic") is True
    assert manager.cloud_only is True


def test_a_healthy_card_never_escalates() -> None:
    """The escalation tiers stay out of the way below the eviction line.

    Without this, a machine at 40% could still find itself in cloud-only mode from
    a stale flag left by an earlier spike.
    """
    budget = VRAMBudget(total_mb=4608, headroom_mb=1500)
    manager = ModelManager(
        budget=budget, models=TEST_MODELS, transport=FakeTransport()
    )
    budget.assign("small", 800)
    result = asyncio.run(manager.memory_pressure_handler())
    assert result["triggered"] is False
    assert "panic" not in result and "emergency_cloud" not in result
    assert manager.cloud_only is False


def test_the_pressure_tier_matches_the_configured_bands() -> None:
    """The dashboard bands and the eviction logic read the same thresholds.

    If these drifted apart the operator would be told a card was fine at the
    moment the machine was evicting from it.
    """
    budget = VRAMBudget(total_mb=4608, headroom_mb=1500)
    assert budget.pressure_tier() == "normal"
    budget.assign("a", 4608 * 0.50)
    assert budget.pressure_tier() == "normal"
    budget.assign("a", 4608 * 0.80)
    assert budget.pressure_tier() == "warning"
    budget.assign("a", 4608 * 0.90)
    assert budget.pressure_tier() == "evicting"
    budget.assign("a", 4608 * 0.96)
    assert budget.pressure_tier() == "emergency"
    budget.assign("a", 4608 * 0.99)
    assert budget.pressure_tier() == "panic"


def test_ram_is_judged_against_the_ram_budget_not_the_vram_cap() -> None:
    """Host RAM and VRAM are separate pools and need separate verdicts.

    The VRAM cap on this machine is smaller than the RAM budget the config
    declares, so comparing a RAM figure against the card would call every task
    oversized and the answer would never mean anything.
    """
    from brain.hypothalamus.resource_predictor import ResourcePredictor

    predictor = ResourcePredictor()
    prediction = predictor.predict_ram_for_task("chat")
    limits = get_resource_limits()["ram"]
    assert prediction["budget_mb"] == float(limits["total_budget_mb"])
    assert prediction["usable_mb"] == float(
        limits["total_budget_mb"] - limits["reserved_headroom_mb"]
    )
    assert prediction["budget_mb"] != 4608, "the VRAM cap leaked back in"
    # 2048MB usable against a 500MB task is a fit, which the VRAM cap would also
    # have said -- the point is that the number comes from the right pool.
    assert prediction["fits"] is True
    assert prediction["percent_of_budget"] == pytest.approx(500 / 2048 * 100, abs=0.1)


def test_the_snapshot_publishes_the_ram_budget_verdict(kill_switch_app) -> None:
    """The RAM verdict is published, not merely computed.

    A prediction nothing reports is a prediction nobody can act on, and the
    snapshot is where the host reading already lives — so the budget the agent
    spends from belongs beside the memory it is spending.
    """
    body = TestClient(kill_switch_app).get("/api/resources/snapshot").json()["status"]
    budget = body["ram_budget"]
    limits = get_resource_limits()["ram"]
    assert budget["budget_mb"] == float(limits["total_budget_mb"])
    assert budget["usable_mb"] == float(
        limits["total_budget_mb"] - limits["reserved_headroom_mb"]
    )
    assert budget["host_total_mb"] > 0
    assert "fits" in budget


def test_the_brainstem_reaper_defers_to_the_budget() -> None:
    """The Brainstem reaper delegates to the Hypothalamus when one is attached.

    Asserted through the returned list rather than by checking that a method was
    called: the reaper's contract is which models came back, and that contract is
    what the scheduler and the dashboard consume.
    """
    brain = brain_with()
    manager = ProcessManager()
    manager.hypothalamus = brain

    from brain.prefrontal_cortex.action import Action
    from brain.prefrontal_cortex.permission_gate import PermissionGate

    decision = PermissionGate().decide(
        Action(category="manage_models", target="medium"), 5
    )
    asyncio.run(brain.models.load("medium", decision))
    brain.models._loaded_at["medium"] -= 3600

    unloaded = asyncio.run(manager.unload_idle())
    assert unloaded == ["medium"]
    assert brain.models.budget.allocated_mb == 0


def test_the_reaper_without_a_hypothalamus_behaves_as_before() -> None:
    """The reaper's original behaviour survives with nothing attached.

    Keeps the resource layer a genuinely optional dependency rather than one the
    Brainstem quietly requires.
    """
    manager = ProcessManager()
    manager.unload_after = 0
    assert manager.hypothalamus is None
    assert asyncio.run(manager.unload_idle()) == []


def test_the_api_wires_the_resource_layer_into_the_router(kill_switch_app) -> None:
    """The app hands its own Hypothalamus to the router and the reaper.

    Asserted as identity rather than as behaviour: the point is that there is one
    resource layer in the process, so a model evicted by the reaper frees space in
    the same budget the router consults.
    """
    app = kill_switch_app
    assert app.state.thalamus.hypothalamus is app.state.hypothalamus
    assert app.state.process_manager.hypothalamus is app.state.hypothalamus
    assert app.state.hypothalamus.models.gate is app.state.permission_gate


def test_the_amygdala_is_consulted_about_a_swap_burst(kill_switch_app) -> None:
    """Rapid swapping reaches the threat detector through the real wiring."""
    app = kill_switch_app
    amygdala = app.state.hypothalamus.models.amygdala
    audit_seen: List[str] = []

    class RecordingAudit:
        """Captures event names."""

        def record(self, event: str, summary: str, **fields: Any) -> None:
            """Record the event name."""
            audit_seen.append(event)

    manager = ModelManager(
        budget=VRAMBudget(total_mb=4608, headroom_mb=1500),
        models=TEST_MODELS,
        transport=FakeTransport(),
        audit=RecordingAudit(),
        amygdala=amygdala,
    )
    # Above the burst threshold, so the next swap is reported.
    manager.swap_watch.threshold = 2
    manager.swap_watch.record()
    manager.swap_watch.record()

    from brain.prefrontal_cortex.action import Action
    from brain.prefrontal_cortex.permission_gate import PermissionGate

    decision = PermissionGate().decide(Action(category="manage_models", target="medium"), 5)
    asyncio.run(manager.hot_swap("", "medium", decision))
    assert "threat_assessed" in audit_seen
    assert amygdala is not None


@pytest.fixture
def wired(kill_switch_app):
    """Return ``(app, transport)`` with a model manager that cannot reach hardware.

    The app's own Hypothalamus, but with its model catalogue swapped for a small
    synthetic one so a load does not need Ollama and does not depend on which
    models happen to be in ``config/models.json``.
    """
    app = kill_switch_app
    transport = FakeTransport()
    app.state.hypothalamus.models = ModelManager(
        budget=app.state.hypothalamus.budget,
        models=TEST_MODELS,
        transport=transport,
        gate=app.state.permission_gate,
        trust_level=1,
        audit=app.state.audit,
        logger=app.state.logger,
    )
    return app, transport


def _allow():
    """Return a permission decision that permits loading a model."""
    from brain.prefrontal_cortex.action import Action
    from brain.prefrontal_cortex.permission_gate import PermissionGate

    return PermissionGate().decide(Action(category="manage_models", target="fast"), 5)


def test_the_snapshot_endpoint_reports_the_whole_chain(kill_switch_app) -> None:
    """One call returns the budget, the models, the power mode and the sensors."""
    client = TestClient(kill_switch_app)
    status = client.get("/api/resources/snapshot").json()["status"]
    assert status["budget"]["total_mb"] > 0
    assert status["budget"]["headroom_mb"] > 0
    assert status["budget"]["allocated_mb"] <= status["budget"]["usable_mb"]
    assert status["power"]["mode"] in ("idle", "active", "performance")
    assert status["thermal"]["level"] in (
        "normal", "warning", "throttle", "critical", "unmonitored"
    )
    assert status["reporting"] in ("measured", "estimated")


def test_a_tick_run_by_the_scheduler_relieves_pressure(wired) -> None:
    """The scheduled cycle is the thing that actually protects the machine.

    Runs the same coroutine the scheduler runs, so this covers the wiring as well
    as the behaviour: if the scheduler stopped calling ``tick``, nothing else in
    the codebase would notice until the machine ran out of VRAM.
    """
    app, _ = wired
    brain = app.state.hypothalamus

    asyncio.run(brain.models.load("medium", _allow()))
    # Above auto_unload_percent (85) of the 4608MB total, not merely above the
    # usable space — the threshold is measured against the whole card.
    brain.models.budget.assign("hog", 3900)

    report = asyncio.run(brain.tick())
    assert report["pressure"]["triggered"] is True
    assert "medium" in report["pressure"]["unloaded"]
    # Our own models must now fit inside the usable space. The foreign "hog"
    # allocation deliberately still overshoots it: someone else's VRAM is not
    # ours to reclaim, which is exactly why the budget admits the load.
    ours = sum(
        spec["vram_mb"] for name, spec in TEST_MODELS.items()
        if name in brain.models.loaded_models()
    )
    assert ours <= brain.budget.usable_mb


def test_shutdown_releases_everything_through_the_orchestrator(wired) -> None:
    """``cleanup`` empties the budget and stops publishing."""
    app, _ = wired
    brain = app.state.hypothalamus

    asyncio.run(brain.models.load("fast", _allow()))
    assert brain.budget.allocated_mb > 0

    report = asyncio.run(brain.cleanup())
    assert report["budget_fully_released"] is True
    assert brain.budget.allocated_mb == 0
    assert brain.models.loaded_models() == []