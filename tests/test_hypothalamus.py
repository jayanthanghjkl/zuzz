"""Hypothalamus attack tests.

The resource layer has one job that matters more than the rest: not running the
user's machine out of VRAM. These tests attack that job from the direction the
Step 4 and Step 5 suites use — assume the gate has already been bypassed, hand
the manager a genuinely *allowed* decision forged at trust level 5, and check
whether the budget still holds.

That inversion is the point. A budget that is only ever tested with the gate
intact proves nothing about the budget. What has to be true is that even an
action the user explicitly approved at full trust cannot push allocations past the
cap, because the approval was about *whether* to load a model, not about the
right to exhaust the GPU.

The suite also pins the honesty rules, which are safety rules in disguise:

* a missing thermal sensor is ``unmonitored``, never ``normal``;
* a load refused for lack of budget releases the reservation it took;
* hot-swap either succeeds or leaves the old model loaded;
* VRAM figures say whether they are measured or estimated.

Nothing in here touches real hardware. The transport is a fake, the monitor is a
fake, and the audit log is a list — so the whole suite runs on a laptop with no
GPU, which is most laptops.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any, Dict, List, Optional

import pytest

from brain.hypothalamus.exceptions import (
    BudgetExceededError,
    DecisionRequiredError,
    ModelLoadError,
    ModelNotFoundError,
    PermissionErrorForModel,
)
from brain.hypothalamus.hypothalamus import Hypothalamus
from brain.hypothalamus.model_manager import (
    CONSERVATIVE_VRAM_FACTOR,
    MANAGE_MODELS,
    ModelManager,
    SwapWatch,
)
from brain.hypothalamus.power_manager import PowerManager
from brain.hypothalamus.resource_monitor import ResourceMonitor
from brain.hypothalamus.resource_predictor import ResourcePredictor
from brain.hypothalamus.thermal_guardian import ThermalGuardian
from brain.hypothalamus.vram_budget import VRAMBudget
from brain.prefrontal_cortex.action import Action
from brain.prefrontal_cortex.permission_gate import PermissionGate
from core.audit import (
    EVENT_TYPES,
    MODEL_PROTECTIVE_UNLOAD,
    THERMAL_ESCALATION,
    THERMAL_SENSORS_LOST,
    THREAT_ASSESSED,
)

#: Enough room for two models and not three, so the cap can actually be reached.
TEST_TOTAL_MB = 4608
TEST_HEADROOM_MB = 1500

#: Model footprints chosen so the cap can actually be reached: 3108MB is usable,
#: so two of these fit and three do not. Sizes are deliberately not the real
#: config's — a test that could only reach its boundary on one machine's GPU
#: would be a test that silently stops testing.
MODELS = {
    "fast-model": {
        "vram_mb": 800,
        "ram_mb": 500,
        "priority": "always_loaded",
        "role": "fast_responses",
    },
    "big-model": {
        "vram_mb": 1400,
        "ram_mb": 700,
        "priority": "on_demand",
        "role": "complex_responses",
        "idle_timeout_seconds": 60,
    },
    "vision-model": {
        "vram_mb": 1000,
        "ram_mb": 500,
        "priority": "on_demand",
        "role": "vision",
        "idle_timeout_seconds": 60,
    },
    "huge-model": {
        "vram_mb": 4200,
        "ram_mb": 1200,
        "priority": "on_demand",
        "role": "deep_reasoning",
    },
}

#: MB usable for models once the headroom is reserved.
USABLE_MB = TEST_TOTAL_MB - TEST_HEADROOM_MB


class FakeTransport:
    """A model transport that records instead of touching VRAM.

    Attributes:
        calls: Every load and unload, in order, for asserting on attempts.
        fail_loads: Models whose next load should fail.
    """

    def __init__(self, fail_loads: Optional[List[str]] = None) -> None:
        """Record every call and optionally fail chosen models.

        Args:
            fail_loads: Model names whose load should raise.
        """
        self.calls: List[tuple] = []
        self.resident: set = set()
        self.fail_loads = set(fail_loads or [])

    async def load(self, model: str, timeout: float) -> None:
        """Record a load, raising for models configured to fail."""
        self.calls.append(("load", model))
        if model in self.fail_loads:
            self.fail_loads.discard(model)
            raise ModelLoadError(f"simulated load failure for {model}")
        self.resident.add(model)

    async def unload(self, model: str, timeout: float) -> None:
        """Record an unload."""
        self.calls.append(("unload", model))
        self.resident.discard(model)

    async def resident_models(self) -> List[str]:
        """Return the models currently held."""
        return sorted(self.resident)


class FakeAudit:
    """An audit log that keeps its records in a list.

    Attributes:
        records: Every ``(event, summary, level)`` written.

    It validates event names against the *real* ``EVENT_TYPES`` set. A stub that
    accepted anything would let a typo'd event name pass every test and then
    raise ``ValueError`` in production, at the moment the system is trying to
    record a thermal escalation or a protective eviction.
    """

    def __init__(self) -> None:
        """Start with an empty log."""
        self.records: List[tuple] = []

    def record(self, event: str, summary: str, **fields: Any) -> None:
        """Record one entry, refusing an event the real log would refuse."""
        assert event in EVENT_TYPES, f"{event!r} is not a safety event"
        self.records.append((event, summary, fields.get("level", "")))

    def events(self) -> List[str]:
        """Return just the event names, for readable assertions."""
        return [r[0] for r in self.records]


class FakeLogger:
    """An activity logger that keeps its records in a list.

    Attributes:
        entries: Every ``(region, action, fields)`` written.
    """

    def __init__(self) -> None:
        """Start with an empty log."""
        self.entries: List[tuple] = []

    def log(self, region: str, action: str, **fields: Any) -> Dict[str, Any]:
        """Record one entry."""
        self.entries.append((region, action, fields))
        return {"region": region, "action": action, **fields}


class FakeMonitor:
    """A monitor with fixed readings and a controllable GPU temperature.

    Attributes:
        gpu_temp: The temperature ``get_gpu`` reports.
        gpu_available: Whether the GPU sensor works at all.
    """

    def __init__(self, gpu_temp: Optional[int] = 45, gpu_available: bool = True) -> None:
        """Set the readings this monitor will report.

        Args:
            gpu_temp: GPU temperature in Celsius.
            gpu_available: False to simulate a machine with no GPU sensor.
        """
        self.gpu_temp = gpu_temp
        self.gpu_available = gpu_available
        self.battery: Optional[Dict[str, Any]] = None

    def get_ram(self) -> Dict[str, Any]:
        """Return a fixed healthy RAM reading."""
        return {"total_mb": 16384, "used_mb": 8000, "available_mb": 8384, "percent": 48.8}

    def get_vram(self) -> Dict[str, Any]:
        """Return a fixed VRAM reading sourced from NVML."""
        return {
            "total_mb": 6144,
            "used_mb": 1536,
            "available_mb": 4608,
            "per_model": {},
            "source": "pynvml" if self.gpu_available else "unknown",
        }

    def get_cpu(self) -> Dict[str, Any]:
        """Return a fixed CPU reading."""
        return {"percent": 12.0, "cores": 8, "per_core": [10.0] * 8}

    def get_gpu(self) -> Dict[str, Any]:
        """Return the configured GPU reading, or an unavailable one."""
        if not self.gpu_available:
            return {
                "percent": 0.0,
                "memory_percent": 0.0,
                "temperature_c": None,
                "power_draw_w": 0.0,
                "name": "unknown",
                "available": False,
            }
        return {
            "percent": 10.0,
            "memory_percent": 5.0,
            "temperature_c": self.gpu_temp,
            "power_draw_w": 14.0,
            "name": "NVIDIA GeForce Test GPU",
            "available": True,
        }

    def get_disk(self) -> Dict[str, Any]:
        """Return a fixed disk reading."""
        return {"total_mb": 512000, "used_mb": 200000, "available_mb": 312000, "percent": 39.0}

    def get_network(self) -> Dict[str, Any]:
        """Return a fixed network reading."""
        return {"bytes_sent": 100, "bytes_recv": 200, "rate": 0.0}

    def get_agent_ram(self) -> Dict[str, Any]:
        """Return a fixed agent RAM reading."""
        return {"ram_mb": 320.0, "budget_mb": 3072, "percent_of_budget": 10.4}

    def get_battery(self) -> Optional[Dict[str, Any]]:
        """Return the configured battery state, or None for a desktop."""
        return self.battery


def decision_for(operation: str, model: str, level: int) -> Any:
    """Return a real gate decision for a model-management operation.

    Built through the actual :class:`PermissionGate` rather than forged, so a
    change to the matrix would change these decisions and the tests would notice.

    Args:
        operation: What is being done, recorded in the action detail.
        model: The model being acted on.
        level: Trust level 1-5.

    Returns:
        The gate's verdict.
    """
    action = Action(
        category=MANAGE_MODELS,  # type: ignore[arg-type]
        target=model,
        detail={"operation": operation},
    )
    return PermissionGate().decide(action, level)


def manager(
    transport: Optional[FakeTransport] = None,
    audit: Optional[FakeAudit] = None,
    logger: Optional[FakeLogger] = None,
    total_mb: int = TEST_TOTAL_MB,
    headroom_mb: int = TEST_HEADROOM_MB,
    amygdala: Any = None,
    gate: Any = None,
    trust_level: int = 1,
) -> ModelManager:
    """Return a model manager wired to fakes.

    Args:
        transport: The fake transport, or None for a fresh one.
        audit: The fake audit log, or None for a fresh one.
        logger: The fake activity logger, or None for a fresh one.
        total_mb: The hard cap to enforce.
        headroom_mb: The reserved headroom.
        amygdala: Optional threat detector.
        gate: Optional permission gate.
        trust_level: Trust level used when the manager makes its own decisions.

    Returns:
        A manager that cannot reach real hardware or real Ollama.
    """
    return ModelManager(
        budget=VRAMBudget(total_mb=total_mb, headroom_mb=headroom_mb),
        models=MODELS,
        transport=transport or FakeTransport(),
        audit=audit,
        logger=logger,
        amygdala=amygdala,
        gate=gate,
        trust_level=trust_level,
    )


def orchestrator(
    monitor: Optional[FakeMonitor] = None,
    transport: Optional[FakeTransport] = None,
    audit: Optional[FakeAudit] = None,
    logger: Optional[FakeLogger] = None,
    total_mb: int = TEST_TOTAL_MB,
    headroom_mb: int = TEST_HEADROOM_MB,
) -> Hypothalamus:
    """Return a fully wired orchestrator with every sensor faked.

    Args:
        monitor: The fake monitor, or None for a default one.
        transport: The fake transport, or None for a fresh one.
        audit: The fake audit log, or None for a fresh one.
        logger: The fake activity logger, or None for a fresh one.
        total_mb: The hard cap to enforce.
        headroom_mb: The reserved headroom.

    Returns:
        A Hypothalamus that can be ticked without touching hardware.
    """
    mon = monitor or FakeMonitor()
    audit = audit or FakeAudit()
    logger = logger or FakeLogger()
    budget = VRAMBudget(total_mb=total_mb, headroom_mb=headroom_mb)
    transport = transport or FakeTransport()
    models = ModelManager(
        budget=budget, models=MODELS, transport=transport, audit=audit, logger=logger
    )
    return Hypothalamus(
        monitor=mon,
        budget=budget,
        models=models,
        power=PowerManager(monitor=mon),
        thermal=ThermalGuardian(monitor=mon, audit=audit, logger=logger),
        predictor=ResourcePredictor(budget=budget),
        audit=audit,
        logger=logger,
    )


# ---------------------------------------------------------------------------
# 1-3: the budget is a hard cap
# ---------------------------------------------------------------------------


def test_cannot_exceed_vram_budget_hard_cap() -> None:
    """Allocations can never pass the total budget, at any trust level.

    Loads three models that each fit on their own but not together, every one
    with a genuinely allowed decision at level 5. The approval was about *whether*
    to load a model, not about the right to exhaust the GPU, so the third must
    fail on its size alone.
    """
    mgr = manager()
    full = decision_for("load", "big-model", 5)
    assert full.allowed, "the decision must be genuinely allowed for this test to mean anything"

    async def scenario() -> None:
        await mgr.load("big-model", full)
        await mgr.load("vision-model", full)
        with pytest.raises(BudgetExceededError):
            await mgr.load("fast-model", full)

    asyncio.run(scenario())
    assert mgr.budget.allocated_mb == 2400.0
    assert mgr.budget.allocated_mb <= USABLE_MB
    assert mgr.budget.available_for_load() >= 0
    assert sorted(m["model"] for m in mgr.loaded_models()) == ["big-model", "vision-model"]
    assert ("load", "fast-model") not in mgr.transport.calls, (
        "the refused model must never have been sent to the transport"
    )


def test_cannot_load_model_that_wont_fit() -> None:
    """A model larger than the whole budget is refused, not truncated."""
    mgr = manager()
    verdict = mgr.can_load("huge-model")
    assert verdict["allowed"] is False
    assert "1500MB headroom" in verdict["reason"]

    with pytest.raises(BudgetExceededError):
        asyncio.run(mgr.load("huge-model", decision_for("load", "huge-model", 5)))
    assert mgr.budget.allocated_mb == 0
    assert mgr.transport.calls == []


def test_budget_headroom_always_respected() -> None:
    """Headroom is never allocated to a model, however full the budget looks."""
    mgr = manager()
    full = decision_for("load", "big-model", 5)
    asyncio.run(mgr.load("big-model", full))
    asyncio.run(mgr.load("vision-model", full))

    assert mgr.budget.usable_mb == USABLE_MB
    # Even with most of the usable space consumed, the reserved headroom is intact
    # and nothing may be loaded out of it.
    assert mgr.budget.allocated_mb <= USABLE_MB
    assert mgr.budget.available_for_load() == USABLE_MB - 2400
    # A model that would fit only by dipping into the headroom is refused.
    headroom_only = USABLE_MB - 2400 + TEST_HEADROOM_MB
    assert headroom_only > 0
    assert not mgr.budget.can_fit(headroom_only)


def test_refused_load_releases_its_reservation() -> None:
    """A load that fails after reserving leaves no budget behind.

    Without the release, every failed load would leak its reservation and the
    budget would fill with allocations belonging to no model — ending in a
    refusal that looks like a full disk.
    """
    mgr = manager(transport=FakeTransport(fail_loads=["vision-model"]))
    with pytest.raises(ModelLoadError):
        asyncio.run(mgr.load("vision-model", decision_for("load", "vision-model", 5)))
    assert mgr.budget.allocated_to("vision-model") == 0
    assert mgr.budget.allocated_mb == 0
    # The room it was refused for is still available to the next caller.
    assert mgr.can_load("vision-model")["allowed"] is True


# ---------------------------------------------------------------------------
# 4: the gate is mandatory
# ---------------------------------------------------------------------------


def test_model_unload_requires_permission_at_L1() -> None:
    """At level 1, model management asks, and ``ask`` is not ``blocked``.

    The two refusals need different answers from the API: ``ask`` opens a
    confirmation dialog, ``blocked`` is final and no dialog could change it.
    Collapsing them would force the caller to guess whether a prompt is worth
    showing, so the exception carries the verdict.
    """
    mgr = manager(gate=PermissionGate(), trust_level=1)
    verdict = mgr.decide("unload", "big-model")
    assert verdict.requires_confirmation
    assert not verdict.allowed

    with pytest.raises(PermissionErrorForModel) as caught:
        mgr.enforce(verdict)
    assert caught.value.needs_confirmation is True
    assert str(caught.value.decision.action.category) == MANAGE_MODELS

    # Auto-approving at level 3, which is where routine swaps are allowed.
    mgr.trust_level = 3
    assert mgr.enforce(mgr.decide("unload", "big-model")).allowed is True


def test_a_blocked_verdict_carries_no_confirmation_path() -> None:
    """A blocked verdict is not answered by a dialog, and says so.

    Forged rather than obtained from the matrix, because no configured category
    is currently blocked — the point is that the distinction must hold for one
    that is, and nothing in the code may quietly turn a block into a prompt.
    """
    from brain.prefrontal_cortex.permission_decision import PermissionDecision
    from brain.prefrontal_cortex.trust_matrix import BLOCKED

    mgr = manager()
    blocked = PermissionDecision.make(
        Action(category=MANAGE_MODELS, target="big-model"),  # type: ignore[arg-type]
        5,
        BLOCKED,
        "forged blocked verdict",
    )
    with pytest.raises(PermissionErrorForModel) as caught:
        mgr.enforce(blocked)
    assert caught.value.needs_confirmation is False


def test_operations_without_a_decision_are_refused() -> None:
    """A load with no decision and no gate cannot proceed.

    This is the refusal that makes the gate mandatory rather than advisory: there
    is no code path through the manager that skips it.
    """
    mgr = manager()
    with pytest.raises(DecisionRequiredError):
        asyncio.run(mgr.load("big-model"))
    assert mgr.budget.allocated_mb == 0


def test_a_decision_for_another_category_is_refused() -> None:
    """A decision minted for a different action category does not unlock a load.

    Trusting a decision because it was passed in would make the gate a suggestion:
    any decision the caller happens to hold would become a key.
    """
    mgr = manager()
    wrong = PermissionGate().decide(Action(category="chat", target="hello"), 5)
    with pytest.raises(PermissionErrorForModel) as caught:
        asyncio.run(mgr.load("big-model", wrong))
    assert "manage_models" in str(caught.value)


def test_a_dry_run_decision_may_not_change_what_is_loaded() -> None:
    """A dry run describes a load; it does not perform one."""
    mgr = manager()
    dry = PermissionGate().decide(
        Action(category=MANAGE_MODELS, target="big-model", dry_run=True), 5  # type: ignore[arg-type]
    )
    with pytest.raises(PermissionErrorForModel):
        asyncio.run(mgr.load("big-model", dry))
    assert mgr.budget.allocated_mb == 0


def test_unknown_model_is_refused_rather_than_guessed() -> None:
    """A model with no profile is refused, because its cost is unknown."""
    mgr = manager()
    verdict = mgr.can_load("mystery-model")
    assert verdict["allowed"] is False
    assert "no profile" in verdict["reason"]
    with pytest.raises(ModelNotFoundError):
        asyncio.run(mgr.load("mystery-model", decision_for("load", "mystery-model", 5)))


# ---------------------------------------------------------------------------
# 5-6: thermal and hot-swap
# ---------------------------------------------------------------------------


def test_thermal_throttle_triggers_model_downgrade() -> None:
    """A hot GPU routes work away from the heavy model and evicts the optional ones."""
    audit = FakeAudit()
    mon = FakeMonitor(gpu_temp=86)
    brain = orchestrator(monitor=mon, audit=audit)
    full = decision_for("load", "big-model", 5)

    async def scenario() -> Dict[str, Any]:
        await brain.models.load("big-model", full)
        mon.gpu_temp = 86
        return await brain.tick()

    report = asyncio.run(scenario())
    assert report["thermal"]["level"] == "throttle"
    assert "big-model" in report["thermal"]["unloaded"]
    assert brain.models.cloud_only is True
    # Further local loads are refused even at full trust.
    assert brain.models.can_load("vision-model")["allowed"] is False
    # Throttle is a hardware signal, so it belongs in the audit log.
    assert THERMAL_ESCALATION in audit.events()


def test_cannot_bypass_budget_via_hot_swap() -> None:
    """A swap cannot be used to smuggle in a model that does not fit.

    The outgoing model's slot is released before the check so that a same-size
    swap can proceed; a genuinely oversized one still cannot, and the outgoing
    model's reservation is restored rather than silently dropped.
    """
    mgr = manager()
    full = decision_for("hot-swap", "big-model", 5)

    async def scenario() -> None:
        await mgr.load("big-model", full)
        with pytest.raises(BudgetExceededError):
            await mgr.hot_swap("big-model", "huge-model", full)

    asyncio.run(scenario())
    # The old model is still loaded and still charged: the swap did not happen,
    # so its memory is still genuinely in use.
    assert mgr.budget.allocated_to("big-model") == 1400.0
    assert "big-model" in [m["model"] for m in mgr.loaded_models()]


def test_hot_swap_keeps_the_old_model_when_the_new_one_fails() -> None:
    """A failed swap rolls back rather than leaving the user with no model."""
    transport = FakeTransport(fail_loads=["vision-model"])
    mgr = manager(transport=transport)
    full = decision_for("hot-swap", "vision-model", 5)

    async def scenario() -> None:
        await mgr.load("big-model", full)
        with pytest.raises(ModelLoadError) as caught:
            await mgr.hot_swap("big-model", "vision-model", full)
        assert "big-model was kept loaded" in str(caught.value)

    asyncio.run(scenario())
    assert [m["model"] for m in mgr.loaded_models()] == ["big-model"]
    assert mgr.budget.allocated_to("vision-model") == 0


# ---------------------------------------------------------------------------
# 7: memory pressure
# ---------------------------------------------------------------------------


def test_memory_pressure_auto_unload_logged() -> None:
    """Pressure evicts on-demand models, spares the core, and is audited."""
    audit = FakeAudit()
    logger = FakeLogger()
    mgr = manager(audit=audit, logger=logger)
    full = decision_for("load", "big-model", 5)

    async def scenario() -> Dict[str, Any]:
        await mgr.load("fast-model", full)
        await mgr.load("big-model", full)
        return await mgr.memory_pressure_handler(percent_used=90.0)

    report = asyncio.run(scenario())
    assert report["triggered"] is True
    assert report["unloaded"] == ["big-model"], "on-demand goes first"
    assert [m["model"] for m in mgr.loaded_models()] == ["fast-model"], (
        "the always-loaded model is the core and survives pressure"
    )
    # Evicting without a decision must be visible to an auditor.
    assert MODEL_PROTECTIVE_UNLOAD in audit.events()


def test_memory_pressure_leaves_the_core_alone_and_reports_it() -> None:
    """With nothing evictable, pressure is reported rather than silently ignored."""
    mgr = manager()
    full = decision_for("load", "fast-model", 5)
    asyncio.run(mgr.load("fast-model", full))
    report = asyncio.run(mgr.memory_pressure_handler(percent_used=99.0))
    assert report["triggered"] is True
    assert report["unloaded"] == []
    assert [m["model"] for m in mgr.loaded_models()] == ["fast-model"]


def test_watchdog_detects_resource_hoarding() -> None:
    """A model held with no use is reaped, and the churn is left in the log.

    Hoarding is a model sitting in VRAM with nothing using it. Two things have to
    hold: the reaper must actually evict it once its timeout passes, and every
    allocation must be written to the activity log, which is what the watchdog and
    the operator read. A budget that quietly drifts upward is invisible until it
    refuses a load the user expected to work.
    """
    logger = FakeLogger()
    transport = FakeTransport()
    mgr = manager(transport=transport, logger=logger)
    full = decision_for("load", "big-model", 5)

    async def scenario() -> List[str]:
        await mgr.load("big-model", full)
        # Pretend the model was touched a long time ago, so it is overdue.
        mgr._loaded_at["big-model"] -= 3600
        return await mgr.unload_idle()

    assert asyncio.run(scenario()) == ["big-model"]
    assert ("unload", "big-model") in transport.calls
    actions = [entry[1] for entry in logger.entries]
    assert "load" in actions and "unload" in actions


# ---------------------------------------------------------------------------
# 8: prediction
# ---------------------------------------------------------------------------


def test_predictor_recommendations_consistent() -> None:
    """The predictor's route agrees with its own fit verdict, at every confidence."""
    predictor = ResourcePredictor(budget=VRAMBudget(total_mb=4608, headroom_mb=1500))
    for task in predictor.known_task_types():
        for _ in range(12):
            predictor.record_actual(task, 1000.0)
        verdict = predictor.will_task_fit(task)
        route = predictor.recommend_route(task)
        if verdict["fits"]:
            assert route == "local", f"{task} fits but was routed to {route}"
        else:
            assert route in ("cloud", "defer"), f"{task} does not fit but routes {route}"
            assert route == verdict["route"]
        # The high bound is what routing uses, so it must sit above the estimate.
        prediction = predictor.predict_vram_for_task(task)
        assert prediction["high_mb"] >= prediction["total_mb"] >= prediction["low_mb"]
        assert 0.0 <= prediction["confidence"] <= 0.95


def test_predictions_learn_from_observation() -> None:
    """Observation moves the estimate toward what tasks really cost."""
    predictor = ResourcePredictor(budget=VRAMBudget(total_mb=4608, headroom_mb=1500))
    before = predictor.predict_vram_for_task("complex_responses")
    assert before["source"] == "assumed"
    assert before["confidence"] == 0.0
    for _ in range(40):
        predictor.record_actual("complex_responses", 2500.0)
    after = predictor.predict_vram_for_task("complex_responses")
    assert after["source"] == "measured"
    assert after["confidence"] > 0.0
    assert after["total_mb"] == pytest.approx(2500.0)


# ---------------------------------------------------------------------------
# 9-10: power
# ---------------------------------------------------------------------------


def test_power_mode_transitions_logged() -> None:
    """Every mode change reaches the activity log."""
    logger = FakeLogger()
    mon = FakeMonitor()
    brain = orchestrator(monitor=mon, logger=logger)
    brain.power = PowerManager(monitor=mon, idle_threshold_seconds=0, logger=logger)

    asyncio.run(brain.tick())  # no user input yet, threshold 0 -> idle
    assert brain.power.current_mode() == "idle"
    asyncio.run(brain.tick())
    assert brain.power.current_mode() == "idle", "an unchanged mode is not re-logged"
    brain.power.record_user_input()
    assert brain.power.current_mode() == "active"

    changes = [
        (entry[2]["previous"], entry[2]["current"])
        for entry in logger.entries
        if entry[1] == "power_mode_changed"
    ]
    assert ("active", "idle") in changes
    assert ("idle", "active") in changes
    assert len(changes) == 2, "one entry per transition, not one per tick"


def test_battery_awareness_forces_idle_on_low_power() -> None:
    """A low battery forces idle and refuses performance, whatever was asked."""
    mon = FakeMonitor()
    mon.battery = {"on_ac": False, "percent": 9}
    power = PowerManager(monitor=mon, idle_threshold_seconds=999)
    brain = orchestrator(monitor=mon)
    brain.power = power

    assert power.set_mode("performance") is False
    assert power.current_mode() != "performance"

    report = asyncio.run(brain.tick())
    assert power.current_mode() == "idle"
    assert report["power"]["mode"] == "idle"
    assert report["power"]["battery_percent"] == 9
    assert report["power"]["on_ac"] is False


def test_desktop_with_no_battery_is_treated_as_on_ac() -> None:
    """A machine with no battery may use performance mode freely."""
    power = PowerManager(monitor=FakeMonitor())
    assert power.set_mode("performance") is True
    assert power.current_mode() == "performance"


# ---------------------------------------------------------------------------
# 11: missing hardware
# ---------------------------------------------------------------------------


def test_resource_monitor_handles_missing_gpu_gracefully() -> None:
    """The monitor reports a missing GPU as missing, and still serves a snapshot.

    Written so it passes on a machine with a GPU and on one without, because a
    test that asserts "the GPU is absent" only ever runs on the second kind. The
    invariant checked is the one that matters either way: a reading that says the
    sensor is unavailable never carries a temperature of 0.
    """
    monitor = ResourceMonitor()
    gpu = monitor.get_gpu()
    if gpu["available"]:
        assert gpu["temperature_c"] is not None
    else:
        assert gpu["temperature_c"] is None, "a missing sensor is not 0°C"
    snapshot = monitor.get_snapshot()
    for key in ("ram", "vram", "cpu", "gpu", "disk", "network"):
        assert key in snapshot


def test_a_broken_gpu_sensor_does_not_zero_the_temperature() -> None:
    """A GPU that cannot be initialised yields None, and the reason is recorded.

    The failure this pins is the one that would have shipped: a blanket
    ``except`` returning zeros, which the thermal guardian reads as a GPU sitting
    at 0°C. Every threshold compares greater-than, so 0°C reports ``normal``
    forever while the hardware heats with nothing watching.
    """
    class ExplodingNvml:
        """An NVML that fails the way a missing driver does."""

        @staticmethod
        def nvmlInit() -> None:
            """Fail, as an absent driver would."""
            raise RuntimeError("could not initialise NVML")

    monitor = ResourceMonitor(nvml_handle=None)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setitem(sys.modules, "pynvml", ExplodingNvml())
    try:
        gpu = monitor.get_gpu()
    finally:
        monkeypatch.undo()
    assert gpu["available"] is False
    assert gpu["temperature_c"] is None
    assert "NVML" in gpu["reason"] or "nvml" in gpu["reason"]


def test_missing_thermal_sensor_reports_unmonitored_not_normal() -> None:
    """A guardian that cannot read temperature says so rather than claiming cool.

    The failure this prevents is quiet and severe: a 0°C reading passes every
    threshold, so the guardian would report ``normal`` indefinitely while the
    hardware heats with nothing watching.
    """
    mon = FakeMonitor(gpu_temp=45, gpu_available=False)
    guardian = ThermalGuardian(monitor=mon, audit=FakeAudit(), logger=FakeLogger())
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(guardian, "_read_cpu_temp", lambda: None)
    try:
        status = guardian.check()
    finally:
        monkeypatch.undo()
    assert status["level"] == "unmonitored"
    assert status["sensors"] == {"gpu": False, "cpu": False}
    assert guardian.current_level == "unmonitored"


def test_sensor_loss_is_audited() -> None:
    """Losing a thermal sensor is the loss of a safety mechanism, so it is logged."""
    audit = FakeAudit()
    guardian = ThermalGuardian(monitor=FakeMonitor(), audit=audit)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(guardian, "_read_gpu_temp", lambda: None)
    monkeypatch.setattr(guardian, "_read_cpu_temp", lambda: None)
    try:
        guardian.check()
    finally:
        monkeypatch.undo()
    assert THERMAL_SENSORS_LOST in audit.events()


def test_conservative_mode_charges_more_than_the_config_says() -> None:
    """With no sensor, a model is charged 1.5x its documented size."""
    measured = manager()
    measured.set_measured(True)
    assert measured.vram_for("big-model") == 1400.0
    assert measured.reporting_mode() == "measured"

    estimated = manager()
    estimated.set_measured(False)
    assert estimated.vram_for("big-model") == pytest.approx(
        1400.0 * CONSERVATIVE_VRAM_FACTOR
    )
    assert estimated.reporting_mode() == "estimated"


# ---------------------------------------------------------------------------
# 12-13: swap bursts and core models
# ---------------------------------------------------------------------------


def test_rapid_model_swapping_triggers_amygdala() -> None:
    """More than ten swaps in a minute is scored as a suspicious pattern."""
    watch = SwapWatch(threshold=10, window=60)
    for _ in range(10):
        watch.record()
    assert watch.is_suspicious() is False
    watch.record()
    assert watch.is_suspicious() is True

    class RecordingAmygdala:
        """Records every assessment it is asked for."""

        def __init__(self) -> None:
            """Start with no assessments."""
            self.seen: List[Any] = []

        def assess(self, action: Any) -> Any:
            """Record the action and return a dangerous assessment."""
            self.seen.append(action)

            class Assessment:
                """A dangerous verdict."""

                level = "dangerous"
                score = 8
                explanation = "11 model swaps in 60s"

            return Assessment()

    amygdala = RecordingAmygdala()
    audit = FakeAudit()
    mgr = manager(amygdala=amygdala, audit=audit)
    mgr.swap_watch = watch
    mgr._report_swap_burst(11, "big-model")
    assert len(amygdala.seen) == 1
    assert THREAT_ASSESSED in audit.events()


def test_cannot_unload_core_models_without_elevated_permission() -> None:
    """An always-loaded model needs trust level 3+ to be evicted.

    The decision here is *forged* at level 2 and marked allowed, which the real
    gate would never issue for this category. That is the point: it attacks the
    core-model rule on its own, with the gate's own refusal taken off the table.
    Testing it through the gate instead would prove only that the gate works.
    """
    from brain.prefrontal_cortex.permission_decision import PermissionDecision
    from brain.prefrontal_cortex.trust_matrix import AUTO

    mgr = manager()
    asyncio.run(mgr.load("fast-model", decision_for("load", "fast-model", 5)))
    assert mgr.is_core("fast-model") is True

    forged = PermissionDecision.make(
        Action(category=MANAGE_MODELS, target="fast-model"),  # type: ignore[arg-type]
        2,
        AUTO,
        "forged: allowed at level 2 to attack the core rule on its own",
    )
    with pytest.raises(PermissionErrorForModel) as caught:
        asyncio.run(mgr.unload("fast-model", forged))
    assert "always-loaded" in str(caught.value)
    assert [m["model"] for m in mgr.loaded_models()] == ["fast-model"]

    elevated = decision_for("unload", "fast-model", 3)
    assert asyncio.run(mgr.unload("fast-model", elevated))["unloaded"] is True


def test_protective_unload_cannot_evict_a_core_model() -> None:
    """Neither the reaper nor pressure relief may touch the always-loaded model.

    The bypass that lets the system relieve pressure without a dialog is
    deliberately narrow. If it could reach a core model, then a memory spike
    would silently remove the one model the user configured to always have, and
    the bypass would become the hole rather than the safety valve.
    """
    mgr = manager()
    asyncio.run(mgr.load("fast-model", decision_for("load", "fast-model", 5)))
    with pytest.raises(PermissionErrorForModel):
        asyncio.run(mgr.unload("fast-model", protective=True))
    assert [m["model"] for m in mgr.loaded_models()] == ["fast-model"]


def test_protective_unload_is_audited() -> None:
    """An eviction with no decision behind it lands in the audit log."""
    audit = FakeAudit()
    mgr = manager(audit=audit)
    asyncio.run(mgr.load("big-model", decision_for("load", "big-model", 5)))
    asyncio.run(mgr.unload("big-model", protective=True))
    assert MODEL_PROTECTIVE_UNLOAD in audit.events()


# ---------------------------------------------------------------------------
# 14-15: accounting
# ---------------------------------------------------------------------------


def test_vram_reporting_matches_actual_allocation() -> None:
    """What the budget reports allocated is what the manager says it loaded."""
    mgr = manager()
    full = decision_for("load", "big-model", 5)
    asyncio.run(mgr.load("big-model", full))
    loaded = mgr.loaded_models()[0]
    assert loaded["vram_mb"] == mgr.budget.allocated_to("big-model") == 1400.0
    assert mgr.budget.allocated_mb == sum(m["vram_mb"] for m in mgr.loaded_models())
    forecast = mgr.budget.forecast_after_load(1000)
    assert forecast["allocated_after"] == pytest.approx(2400.0)
    # available_after is clamped at zero: a negative "room" reads as room.
    assert forecast["available_after"] == pytest.approx(USABLE_MB - 2400)


def test_shutdown_releases_all_vram() -> None:
    """Cleanup leaves nothing allocated, and reports what it had to force."""
    mgr = manager()
    full = decision_for("load", "big-model", 5)

    async def scenario() -> Dict[str, Any]:
        await mgr.load("fast-model", full)
        await mgr.load("big-model", full)
        assert mgr.budget.allocated_mb > 0
        return await mgr.cleanup()

    report = asyncio.run(scenario())
    assert sorted(report["released"]) == ["big-model", "fast-model"]
    assert report["forced"] == []
    assert mgr.budget.allocated_mb == 0
    assert report["budget_fully_released"] is True
    assert mgr.loaded_models() == []


def test_shutdown_releases_core_models_the_transport_will_not_give_up() -> None:
    """A transport that refuses an unload still has its budget released.

    A shutdown that reported "0MB freed" while models stayed resident would leave
    the next process believing a model is loaded, which is how a stale budget
    turns into a load that is refused for no visible reason.
    """
    class StubbornTransport(FakeTransport):
        """A transport that accepts loads and refuses one unload."""

        async def unload(self, model: str, timeout: float) -> None:
            """Refuse to unload anything."""
            self.calls.append(("unload", model))
            raise ModelLoadError(f"simulated refusal to unload {model}")

    mgr = manager(transport=StubbornTransport())
    full = decision_for("load", "big-model", 5)
    asyncio.run(mgr.load("fast-model", full))
    asyncio.run(mgr.load("big-model", full))

    report = asyncio.run(mgr.cleanup())
    assert sorted(report["forced"]) == ["big-model", "fast-model"]
    assert mgr.budget.allocated_mb == 0
    assert mgr.loaded_models() == []


# ---------------------------------------------------------------------------
# 16: concurrency
# ---------------------------------------------------------------------------


def test_concurrent_load_requests_queued_safely() -> None:
    """Simultaneous loads cannot both pass the budget check and breach the cap.

    Each caller sees room for itself before either has reserved anything. Without
    a single critical section covering check-and-reserve, both proceed and the cap
    is breached by exactly the amount the lock was supposed to prevent.
    """
    transport = FakeTransport()
    mgr = manager(transport=transport)
    full = decision_for("load", "big-model", 5)

    async def scenario() -> List[Any]:
        return await asyncio.gather(
            mgr.load("big-model", full),
            mgr.load("vision-model", full),
            mgr.load("huge-model", full),
            return_exceptions=True,
        )

    results = asyncio.run(scenario())
    assert mgr.budget.allocated_mb <= mgr.budget.usable_mb
    successes = [r for r in results if isinstance(r, dict) and r.get("loaded")]
    failures = [r for r in results if isinstance(r, Exception)]
    assert len(successes) + len(failures) == 3
    # The reservations on disk match the successes reported to the callers.
    for result in successes:
        assert mgr.budget.allocated_to(result["model"]) == result["vram_mb"]
    for model, call in transport.calls:
        if call == "load":
            assert mgr.budget.allocated_to(model) >= 0


# ---------------------------------------------------------------------------
# 17: cloud fallback
# ---------------------------------------------------------------------------


def test_cloud_fallback_triggered_when_vram_full() -> None:
    """A full budget produces a cloud route and a reason, not a failed request."""
    brain = orchestrator()
    full = decision_for("load", "big-model", 5)
    asyncio.run(brain.models.load("big-model", full))
    asyncio.run(brain.models.load("vision-model", full))

    plan = brain.request_resources("vision")
    assert plan["route"] in ("cloud", "defer")
    assert plan["fits"] is False
    assert plan["permission"]["required"] is False, (
        "a cloud route touches nothing local, so it must not demand a "
        "manage_models confirmation"
    )
    assert "vision" in brain.request_resources("vision", model="vision-model")["reason"]

    # And the explicit model check agrees with the task-level prediction.
    assert brain.request_resources("vision", model="huge-model")["route"] == "cloud"


def test_cloud_only_mode_refuses_every_local_load() -> None:
    """Thermal cloud-only is absolute until the hardware recovers."""
    brain = orchestrator(monitor=FakeMonitor(gpu_temp=95))
    full = decision_for("load", "big-model", 5)

    async def scenario() -> Dict[str, Any]:
        await brain.tick()
        return await brain.tick()

    report = asyncio.run(scenario())
    assert report["thermal"]["level"] == "critical"
    assert brain.models.cloud_only is True
    with pytest.raises(BudgetExceededError):
        asyncio.run(brain.models.load("big-model", full))

    mon = brain.monitor
    mon.gpu_temp = 40
    recovery = asyncio.run(brain.tick())["recovery"]
    assert recovery["recovered"] is True
    assert brain.models.cloud_only is False


def test_thermal_recovery_waits_for_normal_not_merely_cooler() -> None:
    """Cloud-only lifts only at normal, so the guardian cannot flap.

    83°C is a *warning*, not a recovery. Lifting cloud-only there would put local
    inference back into the range that caused the trip, and the guardian would
    re-trip within seconds — a safety state that oscillates is worse than one that
    stays engaged.
    """
    brain = orchestrator(monitor=FakeMonitor(gpu_temp=95))
    asyncio.run(brain.tick())
    assert brain.models.cloud_only is True
    brain.monitor.gpu_temp = 83
    report = asyncio.run(brain.tick())
    assert report["thermal"]["level"] == "warning"
    assert report["recovery"]["recovered"] is False
    assert brain.models.cloud_only is True


# ---------------------------------------------------------------------------
# Cross-cutting invariants
# ---------------------------------------------------------------------------


def test_budget_reserves_headroom_even_when_it_exceeds_the_total() -> None:
    """A budget smaller than its own headroom reports zero, never a negative."""
    budget = VRAMBudget(total_mb=1000, headroom_mb=1500)
    assert budget.usable_mb == 0
    assert budget.available_for_load() == 0
    assert budget.can_fit(1) is False
    assert budget.forecast_after_load(500)["available_after"] == 0


def test_every_budget_number_is_constructor_overridable() -> None:
    """The hard cap is exercised in tests only because it is injectable.

    Without this, the cap could only be tested against a real GPU, and would
    therefore only be tested on the developer's own machine.
    """
    budget = VRAMBudget(total_mb=9999, headroom_mb=111)
    assert budget.total_mb == 9999
    assert budget.headroom_mb == 111
    budget.assign("m", 500)
    assert budget.allocated_to("m") == 500
    budget.release("m")
    assert budget.allocated_to("m") == 0
    budget.release("never-allocated")  # must not raise
