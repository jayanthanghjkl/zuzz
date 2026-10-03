"""Hypothalamus — Resource Orchestrator.

The one object the rest of the brain talks to when it needs to know whether
something can run. It owns the monitor, the budget, the model manager, the power
manager, the thermal guardian and the predictor, and it is what makes them agree
with one another.

The orchestration that matters is the chain between them, not any single part:

```
   user activity ──► power mode ──► what may load, and how eagerly
        │
   temperature ──► thermal level ──► downgrade, evict, or cloud-only
        │
   VRAM used   ──► pressure     ──► evict before the cap is reached
        │
   task request ──► prediction   ──► local, cloud, or defer
```

Each link is one-directional and each is re-evaluated on the same tick, so a
thermal event and a memory-pressure event arriving together produce one coherent
response instead of two competing ones.

The orchestrator never loads or unloads a model on its own authority: it routes
every such request through the model manager, which requires a permission
decision. What it *does* do on its own is escalate — engaging cloud-only mode
when the hardware is too hot, which is the response a temperature reading leaves
no room to negotiate.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict, List, Optional

from brain.hypothalamus.model_manager import ModelManager
from brain.hypothalamus.power_manager import PowerManager
from brain.hypothalamus.resource_monitor import ResourceMonitor
from brain.hypothalamus.resource_predictor import ResourcePredictor
from brain.hypothalamus.thermal_guardian import ThermalGuardian
from brain.hypothalamus.vram_budget import VRAMBudget


class Hypothalamus:
    """Coordinates resource monitoring, budgeting, thermal and power policy.

    Attributes:
        monitor: Reads the machine's current state.
        budget: The hard VRAM cap every load is judged against.
        models: Loads and unloads models inside that cap.
        power: Selects the idle/active/performance mode.
        thermal: Escalates as the hardware heats.
        predictor: Estimates what a task will cost.
    """

    def __init__(
        self,
        monitor: Any = None,
        budget: Optional[VRAMBudget] = None,
        models: Optional[ModelManager] = None,
        power: Optional[PowerManager] = None,
        thermal: Optional[ThermalGuardian] = None,
        predictor: Optional[ResourcePredictor] = None,
        gate: Any = None,
        trust_level: int = 1,
        audit: Any = None,
        logger: Any = None,
        amygdala: Any = None,
    ) -> None:
        """Wire the region together.

        Every component is injectable so the whole orchestrator can be exercised
        on a machine with no GPU, no battery and no Ollama — which is what makes
        the safety paths testable at all.

        Args:
            monitor: A :class:`ResourceMonitor`, or None to build one.
            budget: The VRAM cap, or None to read the config.
            models: A :class:`ModelManager`, or None to build one.
            power: A :class:`PowerManager`, or None to build one.
            thermal: A :class:`ThermalGuardian`, or None to build one.
            predictor: A :class:`ResourcePredictor`, or None to build one.
            gate: A ``PermissionGate`` for model-management decisions.
            trust_level: Trust level used for those decisions.
            audit: Audit log for thermal and pressure events.
            logger: Activity logger.
            amygdala: Threat detector consulted on swap bursts.
        """
        self.monitor = monitor or ResourceMonitor()
        self.budget = budget or VRAMBudget()
        self.power = power or PowerManager(monitor=self.monitor, logger=logger)
        self.thermal = thermal or ThermalGuardian(
            monitor=self.monitor, audit=audit, logger=logger
        )
        self.models = models or ModelManager(
            budget=self.budget,
            transport=None,
            gate=gate,
            trust_level=trust_level,
            audit=audit,
            logger=logger,
            amygdala=amygdala,
        )
        self.predictor = predictor or ResourcePredictor(budget=self.budget)
        self._audit = audit
        self._logger = logger
        self._subscribers: List[Callable[[Dict[str, Any]], Any]] = []
        self._poll_task: Optional[asyncio.Task] = None
        self._interval = 1.0
        self._last_snapshot: Dict[str, Any] = {}
        self._degraded: List[str] = []

    # -- Sensor honesty -------------------------------------------------------

    def calibrate(self) -> Dict[str, Any]:
        """Decide whether VRAM figures are measured or estimated, once.

        Called at startup rather than per load, because the answer is a property
        of the machine, not of the moment. The UI needs it: a budget shown in
        measured figures on a machine that is only estimating is a budget the
        user will trust more than it deserves.

        Returns:
            ``reporting`` (``measured`` or ``estimated``), ``vram_source`` and
            ``integrated_gpu``.
        """
        vram = self.monitor.get_vram()
        source = str(vram.get("source", "unknown"))
        measured = source in ("pynvml", "gputil")
        self.models.set_measured(measured)
        # An integrated GPU shares its memory with the desktop compositor, so
        # the cap has to come down or Windows will start paging the compositor
        # out to make room for a model.
        integrated = self._is_integrated()
        if integrated:
            self._reduce_budget_for_shared_vram()
            self._note("integrated GPU detected: VRAM budget reduced for shared memory")
        if not measured:
            self._note(
                "no VRAM sensor available (pynvml and GPUtil both absent): using "
                f"conservative estimates at {1.5}x documented model size"
            )
        return {
            "reporting": self.models.reporting_mode(),
            "vram_source": source,
            "integrated_gpu": integrated,
            "total_mb": self.budget.total_mb,
            "headroom_mb": self.budget.headroom_mb,
        }

    def _is_integrated(self) -> bool:
        """Return True when the GPU shares memory with the system."""
        name = str(self.monitor.get_gpu().get("name", "")).lower()
        return any(
            marker in name
            for marker in ("intel", "uhd", "iris", "apple", "mali", "adreno", "vega")
        )

    def _reduce_budget_for_shared_vram(self) -> None:
        """Shrink the cap for a shared-memory GPU."""
        from core.config import get_resource_limits

        overhead = float(
            get_resource_limits().get("integrated_gpu_overhead_percent", 25)
        )
        self.budget.total_mb = int(self.budget.total_mb * (1 - overhead / 100))

    # -- Status ---------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Return the region-wide health of the resource layer.

        Returns:
            ``healthy``, ``reporting``, ``power``, ``thermal``, ``budget``,
            ``models``, ``ram``, ``gpu`` and ``degraded``. ``degraded`` lists
            what could not be measured, which is more useful than a zero.
        """
        ram = self.monitor.get_ram()
        gpu = self.monitor.get_gpu()
        thermal = {
            "level": self.thermal.current_level,
            "gpu_warn": self.thermal.gpu_warn,
            "gpu_throttle": self.thermal.gpu_throttle,
            "gpu_critical": self.thermal.gpu_critical,
            "cpu_warn": self.thermal.cpu_warn,
            "cpu_throttle": self.thermal.cpu_throttle,
            "cpu_critical": self.thermal.cpu_critical,
        }
        percent_used = self.models.percent_used()
        healthy = (
            self.thermal.current_level not in ("critical",)
            and not self.models.cloud_only
            and ram.get("percent", 0) < 95
        )
        return {
            "healthy": healthy,
            "reporting": self.models.reporting_mode(),
            "degraded": list(self._degraded),
            "power": {
                "mode": self.power.current_mode(),
                "uptime_seconds": self.power.mode_uptime(),
            },
            "thermal": thermal,
            "budget": {
                "total_mb": self.budget.total_mb,
                "headroom_mb": self.budget.headroom_mb,
                "usable_mb": self.budget.usable_mb,
                "allocated_mb": self.budget.allocated_mb,
                "available_mb": self.budget.available_for_load(),
                "percent_used": percent_used,
                "tier": self.budget.pressure_tier(),
            },
            "models": {
                "loaded": self.models.loaded_models(),
                "count": len(self.models.loaded_models()),
                "cloud_only": self.models.cloud_only,
            },
            "ram": ram,
            # What an ordinary task would cost against the agent's own RAM
            # budget. Published next to the measurement so the two can be
            # compared: a machine with plenty free still has a smaller budget to
            # spend it from, and that is not visible from the reading alone.
            "ram_budget": {
                **self.predictor.predict_ram_for_task("chat"),
                "host_total_mb": ram.get("total_mb"),
                "host_available_mb": ram.get("available_mb"),
            },
            "gpu": gpu,
            "vram": self.monitor.get_vram(),
        }

    def request_resources(self, task_type: str, model: Optional[str] = None) -> Dict[str, Any]:
        """Ask whether a task can run locally, and what it would cost.

        The Thalamus calls this before routing. It is a plan, not a decision: it
        says where the task should go and what it will cost, and loading
        anything is a separate, permission-gated operation.

        Args:
            task_type: The kind of work, e.g. ``complex_responses``.
            model: An explicit model to check instead of the task's role.

        Returns:
            The plan: ``route``, ``fits``, ``reason``, ``prediction``,
            ``can_load`` and ``permission``.
        """
        prediction = self.predictor.predict_vram_for_task(task_type)
        if model:
            verdict: Dict[str, Any] = self.models.can_load(model)
            # An explicit model's verdict uses ``allowed``; the predictor's uses
            # ``fits``. Reading only one of them silently turns a refusal into a
            # local route — the caller is told to go local while being handed a
            # verdict that says it does not fit.
            ok = bool(verdict.get("allowed", verdict.get("fits", False)))
            route = "local" if ok else str(verdict.get("route", "cloud"))
            reason = verdict.get("reason", "")
        else:
            verdict = self.predictor.will_task_fit(task_type)
            ok = bool(verdict.get("fits", False))
            route = str(verdict.get("route", "local"))
            reason = verdict.get("action_needed", "")
        reason = reason or ("fits locally" if ok else "does not fit locally")
        if self.models.cloud_only:
            route, reason = "cloud", "thermal state is critical; running cloud-only"
        permission: Dict[str, Any] = {"required": True, "category": "manage_models"}
        if not route == "local":
            # A cloud route touches nothing local, so there is no model to
            # manage and no decision to ask for. Saying otherwise would make
            # every cloud fallback wait on a popup about a model it will not load.
            permission = {"required": False, "category": None}
        return {
            "task_type": task_type,
            "model": model,
            "route": route,
            "fits": ok,
            "reason": reason,
            "prediction": prediction,
            "can_load": verdict,
            "permission": permission,
            "budget_percent_used": self.models.percent_used(),
        }

    # -- The tick -------------------------------------------------------------

    async def tick(self) -> Dict[str, Any]:
        """Run one coordination cycle and act on what it finds.

        Returns:
            A report of everything the cycle decided, for the activity log and
            the live WebSocket.

        Raises:
            Nothing. A failure to read one sensor must not stop the others from
            being read, and must not take the agent down.
        """
        report: Dict[str, Any] = {
            "timestamp": time.time(),
            "power": None,
            "thermal": None,
            "pressure": None,
            "recovery": None,
        }
        report["power"] = self._safe(self.power.auto_mode_switcher, default={})
        report["thermal"] = await self._handle_thermal()
        report["pressure"] = await self._handle_pressure()
        report["recovery"] = self._handle_recovery()
        snapshot = self.snapshot()
        self._last_snapshot = snapshot
        for subscriber in list(self._subscribers):
            await self._notify(subscriber, snapshot)
        return report

    async def _handle_thermal(self) -> Dict[str, Any]:
        """Apply the thermal level to what is loaded.

        Throttle and critical both go to cloud-only; they differ in that the
        critical tier is written to the audit log as ``forbidden`` rather than
        ``dangerous``. Warning is advisory and loads nothing — the downgrade it
        implies happens naturally when the next task is routed through
        :meth:`request_resources`, which already recommends the smaller model
        once memory is short.
        """
        status = self.thermal.check()
        level = status["level"]
        if level == "normal":
            return {"level": level, "action": status["action"], "unloaded": []}
        unloaded: List[str] = []
        if level in ("throttle", "critical"):
            unloaded = await self.models.enter_cloud_only(
                f"thermal {level}: gpu={status['gpu_temp']}C cpu={status['cpu_temp']}C"
            )
        return {"level": level, "action": status["action"], "unloaded": unloaded}

    async def _handle_pressure(self) -> Dict[str, Any]:
        """Evict on-demand models when VRAM usage crosses the pressure line."""
        return await self.models.memory_pressure_handler()

    def _handle_recovery(self) -> Dict[str, Any]:
        """Lift cloud-only mode once the hardware is back inside limits.

        Restored only after the level has come back to ``normal``, not merely
        below the critical line: re-enabling local inference at 88°C would trip
        the guardian again within seconds, and a flapping safety state is worse
        than one that stays engaged.
        """
        level = self.thermal.current_level
        if level == "normal" and self.models.cloud_only:
            self.models.cloud_only = False
            self._log("thermal_recovery", from_level="critical")
            return {"recovered": True, "cloud_only": False}
        return {"recovered": False, "cloud_only": self.models.cloud_only}

    # -- Live updates ---------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """Return one resource reading for every sensor."""
        return {
            "timestamp": time.time(),
            "ram": self.monitor.get_ram(),
            "vram": self.monitor.get_vram(),
            "cpu": self.monitor.get_cpu(),
            "gpu": self.monitor.get_gpu(),
            "disk": self.monitor.get_disk(),
            "network": self.monitor.get_network(),
            "agent_ram": self.monitor.get_agent_ram(),
            "budget": {
                "total_mb": self.budget.total_mb,
                "allocated_mb": self.budget.allocated_mb,
                "available_mb": self.budget.available_for_load(),
                "percent_used": self.models.percent_used(),
                "tier": self.budget.pressure_tier(),
            },
            "power_mode": self.power.current_mode(),
            "thermal_level": self.thermal.current_level,
            "cloud_only": self.models.cloud_only,
            "loaded_models": self.models.loaded_models(),
            "reporting": self.models.reporting_mode(),
        }

    def subscribe(self, callback: Callable[[Dict[str, Any]], Any]) -> Callable[[], None]:
        """Register a live-update listener.

        Args:
            callback: Called with each snapshot. May be a coroutine function.

        Returns:
            A function that removes the subscription.
        """

        def unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        self._subscribers.append(callback)
        return unsubscribe

    async def poll_loop(self, interval: float = 1.0) -> None:
        """Tick forever, publishing to subscribers.

        Args:
            interval: Seconds between cycles. 1 Hz for a live chart; the caller
                may raise it when the UI is idle.
        """
        self._interval = interval
        while True:
            await asyncio.sleep(interval)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                # The loop is the thing keeping the dashboard alive. A bad
                # reading in one cycle is not a reason to stop publishing.
                continue

    def start_polling(self, interval: float = 1.0) -> asyncio.Task:
        """Start :meth:`poll_loop` in the background.

        Args:
            interval: Seconds between cycles.

        Returns:
            The task, so the caller can cancel it on shutdown.
        """
        if self._poll_task is not None and not self._poll_task.done():
            return self._poll_task
        self._poll_task = asyncio.ensure_future(self.poll_loop(interval))
        return self._poll_task

    async def stop_polling(self) -> None:
        """Cancel the background poll loop."""
        if self._poll_task is None:
            return
        self._poll_task.cancel()
        try:
            await self._poll_task
        except asyncio.CancelledError:
            pass
        self._poll_task = None

    # -- Shutdown -------------------------------------------------------------

    async def cleanup(self) -> Dict[str, Any]:
        """Release every resource and stop publishing.

        Returns:
            The model manager's release report plus whether polling has stopped.
        """
        await self.stop_polling()
        report = await self.models.cleanup()
        self._log("hypothalamus_shutdown", **report)
        return {**report, "polling_stopped": self._poll_task is None}

    # -- Internals ------------------------------------------------------------

    def _note(self, message: str) -> None:
        """Record a degradation reason, once each."""
        if message not in self._degraded:
            self._degraded.append(message)
        self._log("degraded", reason=message)

    def _safe(self, func: Any, default: Any) -> Any:
        """Call a reader, returning ``default`` if it raises.

        A sensor that cannot be read is reported as missing rather than as zero,
        because zero reads as a healthy measurement.
        """
        try:
            return func()
        except Exception as exc:
            self._note(f"{getattr(func, '__name__', 'sensor')} unavailable: {exc}")
            return default

    async def _notify(self, subscriber: Callable, snapshot: Dict[str, Any]) -> None:
        """Send one snapshot to a subscriber, isolating its failures.

        A subscriber that raises must not stop the others from being told, or
        stop the loop: a broken browser tab cannot be allowed to silence the
        dashboard on every other tab.
        """
        try:
            result = subscriber(snapshot)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            pass

    def _log(self, action: str, **fields: Any) -> None:
        """Write one record to the activity log, if there is one."""
        if self._logger is None:
            return
        try:
            self._logger.log("hypothalamus", action, **fields)
        except Exception:
            pass
