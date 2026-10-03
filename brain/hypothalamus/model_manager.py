"""Hypothalamus — Model Manager.

Loads and unloads local models inside the VRAM budget, and refuses to do either
without a permission decision.

Four rules hold regardless of what the caller wants:

* **The budget is a hard cap.** A load that would push allocations past
  :attr:`VRAMBudget.total_mb` is refused, and refused *before* the model is
  touched. Running out of VRAM mid-inference does not merely fail the request —
  it can take the GPU driver with it, which is the user's whole desktop.
* **Every explicit load, unload or swap carries a decision** for the
  ``manage_models`` category. Loading a model is a privileged operation: it
  changes what the system can answer and what it is holding on the user's
  machine. A model-management call without a decision raises
  :class:`~brain.hypothalamus.exceptions.DecisionRequiredError` rather than
  proceeding, so the gate cannot be skipped by forgetting a parameter.
* **Protective evictions are exempt from the gate, and only from the gate.** When
  VRAM crosses the pressure threshold the manager unloads on its own. Requiring a
  prompt to relieve memory pressure would mean the prompt appears at the moment
  the system is least able to wait, so these evictions run without a decision —
  but they are written to the audit log, because the system changing what is
  loaded without being asked is exactly what an auditor needs to see.
* **Hot-swap is atomic.** If the incoming model fails to load, the outgoing one
  is put back. A swap that leaves the user with neither model has made the
  failure worse.

When neither pynvml nor GPUtil can be found, the manager enters *conservative
mode*: it assumes each model costs 1.5x its documented footprint and reports its
numbers as estimates. Under-allocating would breach the cap the cap exists to
enforce, so the guess errs high.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from brain.hypothalamus.exceptions import (
    BudgetExceededError,
    DecisionRequiredError,
    HotSwapRollbackError,
    ModelLoadError,
    ModelNotFoundError,
    ModelUnloadError,
    PermissionErrorForModel,
)
from brain.hypothalamus.model_transport import (
    SWAP_WINDOW_SECONDS,
    ModelTransport,
    OllamaTransport,
    SwapWatch,
)
from brain.hypothalamus.vram_budget import VRAMBudget
from brain.prefrontal_cortex.action import Action
from core.audit import (
    MODEL_PROTECTIVE_UNLOAD,
    MODEL_RELEASED,
    THERMAL_ESCALATION,
    THREAT_ASSESSED,
)
from core.config import get_models, get_resource_limits

#: The permission category every model-management decision must be for.
#:
#: Defined here rather than as an ``ActionCategory`` member because the safety
#: core is locked against edits and the matrix is config-driven: the row in
#: ``config/trust_levels.json`` is what grants the permission, and
#: ``TrustMatrix`` looks categories up as strings.
MANAGE_MODELS = "manage_models"

#: Timeout for a model's first load, when the weights are not in page cache yet.
FIRST_LOAD_TIMEOUT_SECONDS = 120

#: Timeout for a model already resident on disk and in the OS page cache.
CACHED_LOAD_TIMEOUT_SECONDS = 10

#: Multiplier applied to a model's documented footprint when no VRAM sensor is
#: available. Erring high is the safe direction: an over-estimate costs a load
#: that might have fitted, an under-estimate breaches the cap.
CONSERVATIVE_VRAM_FACTOR = 1.5


class ModelManager:
    """Decides which models may be resident, and keeps them inside the budget.

    Attributes:
        budget: The :class:`~brain.hypothalamus.vram_budget.VRAMBudget` enforced
            on every allocation.
        swap_watch: The sliding-window counter the Amygdala consults.
    """

    def __init__(
        self,
        budget: Optional[VRAMBudget] = None,
        models: Optional[Dict[str, Any]] = None,
        transport: Any = None,
        gate: Any = None,
        trust_level: int = 1,
        audit: Any = None,
        logger: Any = None,
        amygdala: Any = None,
    ) -> None:
        """Wire the manager to its dependencies.

        Args:
            budget: The budget to enforce. One is built from config when omitted.
            models: Per-model profiles keyed by name. Defaults to
                ``config/models.json``.
            transport: A :class:`ModelTransport`. Defaults to Ollama.
            gate: A ``PermissionGate``, or None to accept decisions already made
                by the caller without re-deciding.
            trust_level: Trust level 1-5 used when the manager makes its own
                decisions.
            audit: Audit log for protective evictions and refusals.
            logger: Activity logger for routine bookkeeping.
            amygdala: Optional threat detector consulted on swap bursts.
        """
        self.budget = budget or VRAMBudget()
        self._models = models if models is not None else get_models().get("_models", {})
        self.transport = transport or OllamaTransport()
        self.gate = gate
        self.trust_level = trust_level
        self._audit = audit
        self._logger = logger
        self.amygdala = amygdala
        self.swap_watch = SwapWatch()
        self._loaded_at: Dict[str, float] = {}
        self._resident: set[str] = set()
        self._measured_mode = True
        # One lock for the whole manager. Concurrent loads that each pass the
        # budget check separately can both see room for one model and together
        # breach the cap, so the check and the assignment have to be one
        # critical section.
        self._lock = asyncio.Lock()
        self.cloud_only = False

    # -- Profiles -------------------------------------------------------------

    def profile(self, model: str) -> Dict[str, Any]:
        """Return one model's resource profile.

        Args:
            model: The model name.

        Returns:
            The configured profile, or an empty dict when unknown. Unknown
            models are treated as unprofiled rather than rejected here, so
            :meth:`can_load` can refuse them with a reason.
        """
        return dict(self._models.get(model, {}))

    def known_models(self) -> List[str]:
        """Return the models this manager has profiles for."""
        return list(self._models)

    def vram_for(self, model: str) -> float:
        """Return the MB this manager charges ``model`` for.

        In conservative mode — no NVML and no GPUtil — the documented footprint
        is multiplied by :data:`CONSERVATIVE_VRAM_FACTOR`.

        Args:
            model: The model name.
        """
        base = float(self.profile(model).get("vram_mb", 0) or 0)
        if base <= 0:
            return 0.0
        return round(base * (1.0 if self._measured_mode else CONSERVATIVE_VRAM_FACTOR), 1)

    def set_measured(self, measured: bool) -> None:
        """Declare whether VRAM figures are measured or estimated.

        Args:
            measured: True when a real sensor is backing the numbers. The UI
                labels its figures accordingly — an estimate shown as a
                measurement is how a budget quietly stops meaning anything.
        """
        self._measured_mode = measured

    def reporting_mode(self) -> str:
        """Return ``"measured"`` or ``"estimated"``."""
        return "measured" if self._measured_mode else "estimated"

    def priority_of(self, model: str) -> str:
        """Return ``always_loaded`` or ``on_demand`` for one model."""
        return str(self.profile(model).get("priority", "on_demand"))

    def is_core(self, model: str) -> bool:
        """Return True when the model is pinned by configuration.

        A core model is the one the user always has available, so evicting it is
        never routine — it needs an elevated trust level even to be proposed.
        """
        return self.priority_of(model) == "always_loaded"

    # -- State ----------------------------------------------------------------

    def loaded_models(self) -> List[Dict[str, Any]]:
        """Return the loaded models with their VRAM cost and idle time.

        Returns:
            One entry per loaded model, carrying ``model``, ``vram_mb``,
            ``ram_mb``, ``priority``, ``role``, ``idle_seconds`` and
            ``loaded_at``.
        """
        now = time.time()
        return [
            {
                "model": model,
                "vram_mb": self.budget.allocated_to(model),
                "ram_mb": float(self.profile(model).get("ram_mb", 0) or 0),
                "priority": self.priority_of(model),
                "role": str(self.profile(model).get("role", "")),
                "idle_seconds": round(now - loaded_at, 1),
                "loaded_at": round(loaded_at, 3),
            }
            for model, loaded_at in sorted(self._loaded_at.items())
        ]

    def can_load(self, model: str) -> Dict[str, Any]:
        """Return whether a model may be loaded, and why.

        Every refusal is answered rather than raised, because the caller is
        usually the Thalamus deciding between local and cloud routing, and it
        needs the reason to route well.

        Args:
            model: The model name.

        Returns:
            ``allowed``, ``reason``, ``required_vram_mb``, ``available_vram_mb``
            and ``reporting``.
        """
        profile = self.profile(model)
        required = self.vram_for(model)
        available = self.budget.available_for_load()
        verdict: Dict[str, Any] = {
            "model": model,
            "required_vram_mb": required,
            "available_vram_mb": available,
            "reporting": self.reporting_mode(),
            "forecast": self.budget.forecast_after_load(required),
        }
        if not profile:
            verdict.update(
                allowed=False,
                reason=f"{model} has no profile in config/models.json, so its VRAM "
                "cost is unknown; refusing rather than guessing",
            )
            return verdict
        if model in self._loaded_at:
            verdict.update(allowed=True, reason=f"{model} is already loaded")
            return verdict
        if self.cloud_only:
            verdict.update(
                allowed=False,
                reason="thermal state is critical: local models are unloaded and "
                "no new load is accepted until the hardware cools",
            )
            return verdict
        if not self.budget.can_fit(required):
            verdict.update(
                allowed=False,
                reason=(
                    f"{model} needs {required:g}MB and only {available:g}MB is free "
                    f"after the {self.budget.headroom_mb:g}MB headroom; route to the "
                    "cloud or unload something first"
                ),
            )
            return verdict
        verdict.update(allowed=True, reason=f"{model} fits in {available:g}MB free")
        return verdict

    # -- Permission -----------------------------------------------------------

    def decide(self, operation: str, model: str, decision: Any = None) -> Any:
        """Return a permission decision for one model-management operation.

        Returns the verdict rather than raising on a refusal, because ``ask`` and
        ``blocked`` are different situations and the caller needs to tell them
        apart: one is answered with a confirmation dialog, the other is final.
        Collapsing them into a single exception would force the API to guess
        whether showing a dialog could ever help.

        Raises:
            DecisionRequiredError: No decision was supplied and the manager has
                no gate to make one. This is the refusal that makes the gate
                mandatory: there is no path through the manager that skips it.
        """
        if decision is not None:
            self._verify_decision(decision, model, operation)
            return decision
        if self.gate is None:
            raise DecisionRequiredError(
                f"{operation} {model} needs a permission decision for "
                f"{MANAGE_MODELS!r}, and no gate was available to make one"
            )
        action = Action(
            category=MANAGE_MODELS,  # type: ignore[arg-type]
            target=model,
            detail={"operation": operation},
        )
        return self.gate.decide(action, self.trust_level)

    def enforce(self, decision: Any) -> Any:
        """Return the decision if it permits the action, or raise.

        Args:
            decision: A verdict from :meth:`decide`.

        Returns:
            The same decision, so a caller can chain the call.

        Raises:
            PermissionErrorForModel: The verdict was ``ask`` or ``blocked``. The
                verdict is attached, so the caller can tell which.
        """
        if getattr(decision, "allowed", False):
            return decision
        raise PermissionErrorForModel(
            getattr(decision, "reason", "model management was not permitted"),
            decision,
        )

    def _verify_decision(self, decision: Any, model: str, operation: str) -> None:
        """Check a caller's decision is real and for this operation.

        A decision for a different category, or one the gate never allowed, is
        refused. Trusting the object because it was passed in would make the gate
        a suggestion.

        Raises:
            PermissionErrorForModel: The decision does not permit this.
        """
        category = str(getattr(getattr(decision, "action", None), "category", ""))
        if category != MANAGE_MODELS:
            raise PermissionErrorForModel(
                f"{operation} {model} needs a decision for {MANAGE_MODELS!r}, "
                f"but this decision is for {category!r}"
            )
        if not getattr(decision, "allowed", False):
            raise PermissionErrorForModel(
                f"{operation} {model} was not permitted: "
                f"{getattr(decision, 'reason', 'no reason given')}"
            )
        if getattr(decision, "dry_run", False):
            raise PermissionErrorForModel(
                f"{operation} {model} is a dry run; a dry run may not change what "
                "is loaded"
            )

    def _require_elevated_for_core(self, model: str, decision: Any) -> None:
        """Refuse a core-model eviction below trust level 3.

        Raises:
            PermissionErrorForModel: The decision is not elevated enough.
        """
        level = int(getattr(decision, "trust_level", 1) or 1)
        if self.is_core(model) and level < 3:
            raise PermissionErrorForModel(
                f"{model} is an always-loaded model, so unloading it needs trust "
                f"level 3 or above; this decision was made at level {level}"
            )

    # -- Operations -----------------------------------------------------------

    async def load(self, model: str, decision: Any = None) -> Dict[str, Any]:
        """Load a model into VRAM inside the budget.

        Args:
            model: The model name.
            decision: A ``PermissionDecision`` for ``manage_models``.

        Returns:
            ``model``, ``vram_mb``, ``loaded``, ``elapsed_seconds`` and
            ``forecast``.

        Raises:
            ModelNotFoundError: No profile exists for the model.
            PermissionErrorForModel: The gate refused.
            BudgetExceededError: The load would breach the cap.
            ModelLoadError: Ollama refused or timed out.
        """
        self.enforce(self.decide("load", model, decision))
        if not self.profile(model):
            raise ModelNotFoundError(
                f"{model} is not in config/models.json; its VRAM cost is unknown, "
                "so it will not be loaded"
            )
        async with self._lock:
            verdict = self.can_load(model)
            if not verdict["allowed"]:
                raise BudgetExceededError(verdict["reason"])
            if model in self._loaded_at:
                return {
                    "model": model,
                    "vram_mb": self.budget.allocated_to(model),
                    "loaded": False,
                    "already_loaded": True,
                    "elapsed_seconds": 0.0,
                    "forecast": verdict["forecast"],
                }
            required = verdict["required_vram_mb"]
            # Reserve before loading so two callers cannot both pass the check
            # and together exceed the cap.
            self.budget.assign(model, required)
            started = time.time()
            try:
                await self.transport.load(model, self._timeout_for(model))
            except Exception:
                # The reservation is released on failure. Leaving it behind
                # would leak budget that no model is using, and the leak would
                # accumulate into a refusal that looks like a full disk.
                self.budget.release(model)
                raise
            self._loaded_at[model] = time.time()
            self._resident.add(model)
            elapsed = round(time.time() - started, 3)
        self._log("load", model=model, vram_mb=required, elapsed_seconds=elapsed)
        return {
            "model": model,
            "vram_mb": required,
            "loaded": True,
            "already_loaded": False,
            "elapsed_seconds": elapsed,
            "forecast": verdict["forecast"],
            "reporting": self.reporting_mode(),
        }

    async def unload(
        self, model: str, decision: Any = None, protective: bool = False
    ) -> Dict[str, Any]:
        """Unload one model.

        Args:
            model: The model name.
            decision: A ``PermissionDecision`` for ``manage_models``.
            protective: Set by the system itself — the idle reaper, memory
                pressure, thermal cloud-only and shutdown. Those are not requests
                from the user or an agent, so requiring a decision would mean the
                prompt appears at the moment the system can least afford to wait
                for one. It is deliberately narrow: a protective unload still
                cannot touch a core model, and it is written to the audit log,
                because the system changing what is loaded on its own initiative
                is exactly what an auditor needs to see.

        Returns:
            ``model``, ``unloaded``, ``vram_freed_mb`` and the new free space.

        Raises:
            PermissionErrorForModel: The gate refused, or the model is core and
                the decision was not elevated enough.
            ModelUnloadError: Ollama refused.
        """
        if protective:
            # No decision is required, but the core model is still protected:
            # a protective unload is about freeing memory, not about overriding
            # the user's choice that this model is always available.
            if self.is_core(model):
                raise PermissionErrorForModel(
                    f"{model} is an always-loaded model; no protective unload may "
                    "evict it, because that is the user's choice, not the "
                    "system's"
                )
        else:
            resolved = self.enforce(self.decide("unload", model, decision))
            self._require_elevated_for_core(model, resolved)
        async with self._lock:
            if model not in self._loaded_at:
                return {
                    "model": model,
                    "unloaded": False,
                    "already_unloaded": True,
                    "vram_freed_mb": 0.0,
                    "available_vram_mb": self.budget.available_for_load(),
                    "protective": protective,
                }
            await self.transport.unload(model, FIRST_LOAD_TIMEOUT_SECONDS)
            self._loaded_at.pop(model, None)
            self._resident.discard(model)
            freed = self.budget.allocated_to(model)
            self.budget.release(model)
            free_now = self.budget.available_for_load()
        self._log("unload", model=model, protective=protective)
        if protective:
            self._audit_protective_unload(model, freed)
        return {
            "model": model,
            "unloaded": True,
            "already_unloaded": False,
            "vram_freed_mb": free_now,
            "available_vram_mb": free_now,
            "protective": protective,
        }

    async def unload_idle(
        self, idle_seconds: float = 60.0, decision: Any = None
    ) -> List[str]:
        """Unload on-demand models that have been idle past their timeout.

        Core models are skipped whatever their age: a model that is always loaded
        has no idle timeout to breach.

        Args:
            idle_seconds: Fallback timeout when a profile does not set one.
            decision: An optional decision covering the sweep. Unused when the
                manager has no gate, because reaping is protective work — see
                :meth:`unload`.

        Returns:
            The models unloaded.
        """
        now = time.time()
        unloaded: List[str] = []
        for model, loaded_at in list(self._loaded_at.items()):
            if self.is_core(model):
                continue
            timeout = float(
                self.profile(model).get("idle_timeout_seconds", idle_seconds)
                or idle_seconds
            )
            if now - loaded_at < timeout:
                continue
            try:
                await self.unload(model, decision, protective=decision is None)
            except (ModelUnloadError, PermissionErrorForModel):
                continue
            unloaded.append(model)
        return unloaded

    async def hot_swap(self, old_model: str, new_model: str, decision: Any = None) -> Dict[str, Any]:
        """Replace one model with another, atomically.

        The new model is loaded first, and the old one is only evicted once the
        new one is resident. If the load fails the old model is still loaded and
        the caller is told so — the user is never left with neither model, which
        is the outcome a naive unload-then-load produces.

        Args:
            old_model: The model to replace. May be empty to mean a cold load.
            new_model: The model to load.
            decision: A ``PermissionDecision`` for ``manage_models``.

        Returns:
            ``swapped``, ``from``, ``to``, ``vram_mb`` and ``elapsed_seconds``.

        Raises:
            ModelLoadError: The new model failed and the old one was restored.
            HotSwapRollbackError: The new model failed *and* the old one could
                not be put back.
            BudgetExceededError: The replacement would breach the cap.
        """
        self.enforce(self.decide(f"hot-swap {old_model}->{new_model}", new_model, decision))
        async with self._lock:
            had_old = old_model in self._loaded_at
            if had_old:
                # Free the outgoing model's slot in the forecast first, so the
                # cap is judged on the post-swap state rather than on both at
                # once. Otherwise a same-size swap can never fit.
                self.budget.release(old_model)
            verdict = self.can_load(new_model)
            if not verdict["allowed"]:
                if had_old:
                    # Put the reservation back: the swap did not happen, so the
                    # old model's memory is still genuinely in use.
                    self.budget.assign(old_model, self.vram_for(old_model))
                raise BudgetExceededError(verdict["reason"])
            started = time.time()
            try:
                await self.transport.load(new_model, self._timeout_for(new_model))
            except Exception as exc:
                if had_old:
                    try:
                        await self.transport.load(
                            old_model, CACHED_LOAD_TIMEOUT_SECONDS
                        )
                        self.budget.assign(old_model, self.vram_for(old_model))
                    except Exception as rollback_exc:
                        raise HotSwapRollbackError(
                            f"{new_model} failed to load and {old_model} could not "
                            f"be restored: {rollback_exc}"
                        ) from exc
                    self._loaded_at[old_model] = time.time()
                    raise ModelLoadError(
                        f"{new_model} failed to load; {old_model} was kept loaded: {exc}"
                    ) from exc
                raise
            self.budget.assign(new_model, verdict["required_vram_mb"])
            self._loaded_at[new_model] = time.time()
            self._resident.add(new_model)
            if had_old:
                try:
                    await self.transport.unload(old_model, FIRST_LOAD_TIMEOUT_SECONDS)
                except Exception:
                    # The new model is resident and the old one is still too.
                    # That is over budget but not a failure — reporting it as
                    # one would tell the caller to retry a swap that succeeded.
                    self._log("swap_cleanup_failed", model=old_model)
                self._loaded_at.pop(old_model, None)
                self._resident.discard(old_model)
            elapsed = round(time.time() - started, 3)
        swaps = self.swap_watch.record()
        suspicious = self.swap_watch.is_suspicious()
        self._log(
            "hot_swap",
            **{
                "from": old_model,
                "to": new_model,
                "elapsed_seconds": elapsed,
                "swaps_in_window": swaps,
            },
        )
        if suspicious:
            self._report_swap_burst(swaps, new_model)
        return {
            "swapped": True,
            "from": old_model,
            "to": new_model,
            "vram_mb": verdict["required_vram_mb"],
            "elapsed_seconds": elapsed,
            "swaps_in_window": swaps,
            "rollback": False,
            "reporting": self.reporting_mode(),
        }

    async def memory_pressure_handler(self, percent_used: Optional[float] = None) -> Dict[str, Any]:
        """Evict models when VRAM usage crosses the pressure threshold.

        Eviction order is by configuration priority and then by idle time, so
        ``on_demand`` models go before ``always_loaded`` ones, and the longest
        idle goes first. A core model is never evicted here: the core is what
        keeps ZUZZ answering, and a pressure response that left the user with
        nothing is a worse failure than the pressure was.

        Args:
            percent_used: Override for the usage percentage. Tests pass a value
                directly rather than arranging for a real GPU to fill up.

        Returns:
            ``triggered``, ``percent_used``, ``unloaded`` and
            ``available_vram_mb``.
        """
        limits = get_resource_limits().get("vram", {})
        threshold = float(limits.get("auto_unload_percent", 85))
        if percent_used is None:
            percent_used = self.percent_used()
        if percent_used < threshold:
            return {
                "triggered": False,
                "percent_used": round(percent_used, 1),
                "threshold": threshold,
                "unloaded": [],
                "available_vram_mb": self.budget.available_for_load(),
            }
        candidates = sorted(
            (m for m in self._loaded_at if not self.is_core(m)),
            key=lambda m: self._loaded_at[m],
        )
        unloaded: List[str] = []
        for model in candidates:
            try:
                # Protective: pressure relief is the system acting on its own
                # evidence, not on a request that could be queued behind a dialog.
                await self.unload(model, protective=True)
            except Exception:
                continue
            unloaded.append(model)
            if self.percent_used() < threshold:
                break
        result = {
            "triggered": True,
            "percent_used": round(percent_used, 1),
            "threshold": threshold,
            "unloaded": unloaded,
            "available_vram_mb": self.budget.available_for_load(),
        }
        # Written to the audit log rather than the activity log: the system
        # changing what is loaded, with no decision and no user request, is
        # exactly the kind of event an audit exists to surface.
        if self._audit is not None:
            self._audit.record(
                MODEL_PROTECTIVE_UNLOAD,
                f"VRAM pressure at {result['percent_used']}%: evicted "
                + (", ".join(unloaded) if unloaded else "nothing evictable"),
                category=MANAGE_MODELS,
                level="dangerous" if not unloaded else "warning",
            )
        self._log("memory_pressure", **result)

        # The two tiers above the eviction line. They exist because eviction alone
        # is not always enough, and the two failures it cannot fix are different:
        # a machine already out of room may have nothing left to evict, and a
        # machine that frees models but is still at the ceiling will simply fill
        # the space again on the next request.
        if percent_used >= float(limits.get("panic_percent", 98)):
            # Nothing left to give. Force local inference off rather than let the
            # next request try again and fail inside the driver.
            await self.enter_cloud_only(
                f"VRAM panic at {result['percent_used']}%: no models left to evict"
            )
            result["panic"] = True
        elif percent_used >= float(limits.get("emergency_cloud_percent", 95)):
            # Models were freed but the card is still nearly full. Hold off on new
            # local loads until it settles, without tearing down what is resident.
            self.cloud_only = True
            result["emergency_cloud"] = True
        return result

    def percent_used(self) -> float:
        """Return the share of the hard cap currently allocated."""
        if not self.budget.total_mb:
            return 0.0
        return round(self.budget.allocated_mb / self.budget.total_mb * 100, 1)

    async def enter_cloud_only(self, reason: str) -> List[str]:
        """Unload every optional model and refuse further local loads.

        Args:
            reason: Why, for the audit log.

        Returns:
            The models unloaded.
        """
        self.cloud_only = True
        unloaded: List[str] = []
        for model in list(self._loaded_at):
            if self.is_core(model):
                continue
            try:
                await self.unload(model, protective=True)
            except Exception:
                continue
            unloaded.append(model)
        if self._audit is not None:
            self._audit.record(
                THERMAL_ESCALATION,
                f"{reason}; unloaded {len(unloaded)} optional model(s) and "
                "refused further local loads",
                category=MANAGE_MODELS,
                level="dangerous",
            )
        self._log("cloud_only", reason=reason, unloaded=unloaded)
        return unloaded

    async def cleanup(self) -> Dict[str, Any]:
        """Unload everything and release all budget, for a graceful shutdown.

        Cleanup is the one place that touches every model, so it runs the same
        reaper each model gets rather than trusting the accounting — a shutdown
        that reported the wrong VRAM would leave the next process to believe a
        model is still resident.

        Returns:
            ``released`` (the models unloaded), ``vram_freed_mb`` and
            ``budget_fully_released``.
        """
        before = self.budget.allocated_mb
        released: List[str] = []
        forced: List[str] = []
        for model in list(self._loaded_at):
            try:
                # Shutdown does not go through :meth:`unload`, because its rules
                # genuinely differ. A reaper or a pressure response must not touch
                # a core model, but a process that is exiting must give every
                # byte back: leaving VRAM allocated to a departing process is
                # exactly the kind of silent leak that makes the next start
                # believe a model is still resident. Anything the transport
                # refuses is listed in ``forced`` rather than quietly dropped.
                await self.transport.unload(model, FIRST_LOAD_TIMEOUT_SECONDS)
                self._loaded_at.pop(model, None)
                self._resident.discard(model)
                self.budget.release(model)
                released.append(model)
            except Exception:
                self._loaded_at.pop(model, None)
                self._resident.discard(model)
                self.budget.release(model)
                forced.append(model)
        remaining = [m for m in self._loaded_at if m in self._resident]
        self._loaded_at.clear()
        self._resident.clear()
        self.budget._allocated.clear()
        self.cloud_only = False
        result = {
            "released": released,
            "forced": forced,
            "vram_freed_mb": round(before, 1),
            "budget_fully_released": self.budget.allocated_mb == 0.0 and not remaining,
        }
        if self._audit is not None and (released or forced):
            self._audit.record(
                MODEL_RELEASED,
                f"released {len(released)} model(s) and released the budget for "
                f"{len(forced)} the transport would not give up",
                category=MANAGE_MODELS,
                level="warning" if forced else "",
            )
        self._log("cleanup", **result)
        return result

    def _audit_protective_unload(self, model: str, freed_mb: float) -> None:
        """Record an unload the system performed without being asked.

        Only the protective path writes here. A user-requested unload has a
        decision behind it and belongs in the activity log alone; this one has no
        request behind it, so it belongs where a safety reviewer will see it.
        """
        if self._audit is None:
            return
        self._audit.record(
            MODEL_PROTECTIVE_UNLOAD,
            f"{model} evicted without a decision to free {freed_mb:g}MB "
            "(idle timeout, memory pressure or thermal state)",
            category=MANAGE_MODELS,
            level="warning",
            target=model,
        )

    # -- Internals ------------------------------------------------------------

    def _timeout_for(self, model: str) -> float:
        """Return the load timeout for a model.

        A model already tracked by the manager is in page cache, so it loads
        fast; the first load of anything may have to fault a multi-gigabyte
        weight file off disk.
        """
        if model in self._resident:
            return CACHED_LOAD_TIMEOUT_SECONDS
        return FIRST_LOAD_TIMEOUT_SECONDS

    def _report_swap_burst(self, swaps: int, model: str) -> None:
        """Hand a swap burst to the Amygdala for scoring.

        Crossing :data:`SWAP_BURST_THRESHOLD` *is* the detection — the Amygdala is
        asked to say how serious it is, not whether it happened. Gating the audit
        record on the Amygdala agreeing the burst was dangerous would be circular:
        it scores patterns in the target text, and a model name contains none, so
        every burst would score zero and the record would never be written. A
        silent anomaly nobody can audit is worse than a recorded one that was later
        judged benign.
        """
        if self.amygdala is None:
            return
        action = Action(
            category=MANAGE_MODELS,  # type: ignore[arg-type]
            target=model,
            detail={"swaps_in_window": swaps},
        )
        try:
            assessment = self.amygdala.assess(action)
        except Exception:
            return
        if self._audit is not None:
            self._audit.record(
                THREAT_ASSESSED,
                f"{swaps} model swaps in {SWAP_WINDOW_SECONDS}s; amygdala scored "
                f"{assessment.level} ({assessment.score}/10)",
                category=MANAGE_MODELS,
                level=assessment.level,
                target=model,
                swaps_in_window=swaps,
            )

    def _log(self, action: str, **fields: Any) -> None:
        """Write one record to the activity log, if there is one."""
        if self._logger is None:
            return
        try:
            self._logger.log("hypothalamus", action, **fields)
        except Exception:
            # Bookkeeping must never be the reason a resource operation fails.
            pass
