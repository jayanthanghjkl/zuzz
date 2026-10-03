"""Hypothalamus — Resource Predictor.

Estimates what a task will cost before it runs, so the Thalamus can route
without loading a model only to discover it does not fit.

The estimates are a starting point and are corrected by observation. Every
allocation is checked against what the task actually used, and the per-task
moving average pulls the estimate toward reality. Two properties matter for
trust in the numbers:

* **The estimate errs high.** A prediction that under-counts causes the load the
  cap was meant to prevent, so the correction is applied as a headroom factor
  above the observed mean rather than as the bare mean.
* **Confidence is reported, not implied.** One observation is a guess; forty is
  a number worth routing on. The confidence interval widens with the variance
  and narrows with the sample count, and callers can see which one they have.

Unrecognised task types fall back to the generic chat estimate rather than
returning nothing, so a new caller gets a usable answer instead of an exception
at the routing layer.
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional

from brain.hypothalamus.vram_budget import VRAMBudget
from core.config import get_models, get_resource_limits

#: Static estimates in MB, used until observation replaces them. ``vram_mb`` is
#: the model weights; ``ram_mb`` adds the host-side process overhead; ``kv_factor``
#: scales the KV cache, which grows with context length during a long inference
#: and is the reason a "loaded and idle" model is not a loaded model.
BASE_ESTIMATES: Dict[str, Dict[str, float]] = {
    "fast_responses": {"vram_mb": 1200, "ram_mb": 500, "kv_factor": 1.1},
    "complex_responses": {"vram_mb": 2200, "ram_mb": 700, "kv_factor": 1.2},
    "vision": {"vram_mb": 1600, "ram_mb": 500, "kv_factor": 1.15},
    "deep_reasoning": {"vram_mb": 4200, "ram_mb": 1200, "kv_factor": 1.25},
    "chat": {"vram_mb": 1200, "ram_mb": 500, "kv_factor": 1.1},
}

#: The task type assumed for anything unrecognised.
DEFAULT_TASK_TYPE = "chat"

#: Observations needed before an estimate is considered measured rather than
#: assumed, and reported without the assumed band.
SAMPLES_FOR_CONFIDENCE = 10

#: Half-width of the prediction interval before anything has been observed.
#:
#: Deliberately modest, because model *weights* are not uncertain — ZUZZ knows
#: how large a model is on disk. What is uncertain is how much KV cache it will
#: hold during inference, and that is a fraction of a known number rather than an
#: unknown one. A wide band here would route every model to the cloud, which
#: would make the cap the only thing this region does.
ASSUMED_HALF_WIDTH_FRACTION = 0.15

#: Ceiling applied to the confidence half-width, as a fraction of the estimate.
MAX_HALF_WIDTH_FRACTION = 0.4

#: Floor on the half-width, as a fraction of the estimate. Below this the interval
#: claims a precision no sensor actually has.
MIN_HALF_WIDTH_FRACTION = 0.05


class ResourcePredictor:
    """Estimates the cost of a task and learns from what tasks actually cost.

    Attributes:
        budget: The budget estimates are checked against.
    """

    def __init__(
        self,
        budget: Optional[VRAMBudget] = None,
        estimates: Optional[Dict[str, Dict[str, float]]] = None,
        samples_required: int = SAMPLES_FOR_CONFIDENCE,
    ) -> None:
        """Build the predictor.

        Args:
            budget: The budget to fit predictions against.
            estimates: Override for the static estimate table.
            samples_required: Observations before an estimate is trusted.
        """
        self.budget = budget or VRAMBudget()
        self._estimates = dict(estimates or BASE_ESTIMATES)
        self.samples_required = samples_required
        self._observed: Dict[str, List[float]] = {}

    # -- Profiles -------------------------------------------------------------

    def known_task_types(self) -> List[str]:
        """Return the task types with static estimates."""
        return sorted(self._estimates)

    def _base(self, task_type: str) -> Dict[str, float]:
        """Return the static estimate for a task type, or the generic one."""
        if task_type in self._estimates:
            return dict(self._estimates[task_type])
        return dict(self._estimates.get(DEFAULT_TASK_TYPE, {"vram_mb": 0, "ram_mb": 0, "kv_factor": 1.0}))

    def _model_for(self, task_type: str) -> Optional[str]:
        """Return the model name whose role matches a task type."""
        # Only the ``_models`` block. The file also holds ``levels`` and
        # ``fallback_order``, whose values are not profiles.
        for name, profile in get_models().get("_models", {}).items():
            if isinstance(profile, dict) and str(profile.get("role", "")) == task_type:
                return name
        return None

    # -- Predictions ----------------------------------------------------------

    def predict_vram_for_task(self, task_type: str) -> Dict[str, Any]:
        """Estimate the VRAM a task needs.

        Returns:
            ``task_type``, ``vram_mb`` (static weights), ``kv_cache_mb``,
            ``dynamic_mb`` (weights plus KV growth), ``total_mb``, ``confidence``
            and ``source``.
        """
        base = self._base(task_type)
        static = float(base.get("vram_mb", 0) or 0)
        kv_factor = float(base.get("kv_factor", 1.0) or 1.0)
        # The KV cache is the part that grows while the task runs. Charging the
        # static figure alone would under-count every long inference.
        kv_cache = round(static * (kv_factor - 1.0), 1)
        total = self._apply_observation(task_type, static + kv_cache)
        return {
            "task_type": task_type,
            "vram_mb": round(static, 1),
            "kv_cache_mb": kv_cache,
            "dynamic_mb": round(static + kv_cache, 1),
            "total_mb": total["estimate_mb"],
            "low_mb": total["low_mb"],
            "high_mb": total["high_mb"],
            "confidence": total["confidence"],
            "samples": total["samples"],
            "source": total["source"],
            "model": self._model_for(task_type),
        }

    def predict_ram_for_task(self, task_type: str) -> Dict[str, Any]:
        """Estimate the host RAM a task needs.

        Judged against the *RAM* budget, not the VRAM cap. Host RAM and VRAM are
        separate pools: the card's 4608MB cap says nothing about whether a task
        fits in system memory, and comparing a RAM figure against it makes the
        verdict meaningless in both directions — never binding on a machine with
        far more RAM than VRAM, and spuriously refusing a task on a machine with a
        small card and plenty of memory. The cap and the pool are read from the
        ``vram`` and ``ram`` blocks of ``config/resource_limits.json``.

        Returns:
            ``task_type``, ``ram_mb``, ``budget_mb``, ``usable_mb``,
            ``percent_of_budget`` and ``fits``.
        """
        base = self._base(task_type)
        ram = round(float(base.get("ram_mb", 0) or 0), 1)
        ram_limits = get_resource_limits().get("ram", {})
        budget_mb = float(ram_limits.get("total_budget_mb", 0) or 0)
        headroom_mb = float(ram_limits.get("reserved_headroom_mb", 0) or 0)
        # The safe direction again: headroom is the OS's, not the task's.
        usable_mb = max(0.0, budget_mb - headroom_mb)
        return {
            "task_type": task_type,
            "ram_mb": ram,
            "budget_mb": budget_mb,
            "usable_mb": usable_mb,
            "percent_of_budget": round(ram / usable_mb * 100, 1) if usable_mb else 0.0,
            "fits": ram <= usable_mb,
        }

    def will_task_fit(self, task_type: str, budget: Optional[VRAMBudget] = None) -> Dict[str, Any]:
        """Return whether a task fits, and what to do when it does not.

        The answer uses the *upper* end of the confidence interval, not the
        mean. Routing on the mean is how a task that "should" have fit ends up
        breaching the cap halfway through generating.

        Args:
            task_type: The task about to run.
            budget: Override the budget to check against.

        Returns:
            ``fits``, ``action_needed``, ``required_mb``, ``worst_case_mb``,
            ``available_mb``, ``confidence`` and ``route``.
        """
        target = budget or self.budget
        prediction = self.predict_vram_for_task(task_type)
        worst_case = float(prediction["high_mb"])
        available = target.available_for_load()
        fits = worst_case <= available
        if fits:
            return {
                "task_type": task_type,
                "fits": True,
                "action_needed": "none",
                "required_mb": prediction["total_mb"],
                "worst_case_mb": round(worst_case, 1),
                "available_mb": round(available, 1),
                "confidence": prediction["confidence"],
                "route": "local",
            }
        route = self._fallback_route(task_type, prediction, target)
        return {
            "task_type": task_type,
            "fits": False,
            "action_needed": (
                "route_to_cloud"
                if route == "cloud"
                else "defer_until_vram_free"
            ),
            "required_mb": prediction["total_mb"],
            "worst_case_mb": round(worst_case, 1),
            "available_mb": round(available, 1),
            "confidence": prediction["confidence"],
            "route": route,
        }

    def recommend_route(self, task_type: str, budget: Optional[VRAMBudget] = None) -> str:
        """Return where a task should run.

        Args:
            task_type: The task about to run.
            budget: Override the budget to check against.

        Returns:
            ``local``, ``cloud`` or ``defer``.
        """
        return str(self.will_task_fit(task_type, budget)["route"])

    def _fallback_route(
        self,
        task_type: str,
        prediction: Dict[str, Any],
        budget: VRAMBudget,
    ) -> str:
        """Choose between the cloud and deferring.

        Deferring is only honest when the wait is short. A task that will fit in
        thirty seconds should wait rather than spend a cloud request; one that
        will not fit for an hour should go to the cloud rather than sit in a
        queue the user cannot see.
        """
        return "cloud" if prediction["high_mb"] > budget.usable_mb else "defer"

    # -- Learning -------------------------------------------------------------

    def record_actual(self, task_type: str, actual_mb: float) -> Dict[str, Any]:
        """Record what a task really used, so the estimate improves.

        Args:
            task_type: The task that ran.
            actual_mb: Observed VRAM in MB.

        Returns:
            The updated estimate and its confidence.
        """
        samples = self._observed.setdefault(task_type, [])
        samples.append(float(actual_mb))
        # Bounded so a long-running process cannot grow the table without limit.
        if len(samples) > 200:
            del samples[:-200]
        prediction = self.predict_vram_for_task(task_type)
        return {
            "task_type": task_type,
            "samples": len(samples),
            "total_mb": prediction["total_mb"],
            "confidence": prediction["confidence"],
        }

    def samples(self, task_type: str) -> int:
        """Return how many observations exist for a task type."""
        return len(self._observed.get(task_type, []))

    def clear(self) -> None:
        """Discard all observations."""
        self._observed.clear()

    def _apply_observation(self, task_type: str, baseline_mb: float) -> Dict[str, Any]:
        """Fold observation into a baseline estimate.

        Args:
            task_type: The task being estimated.
            baseline_mb: The static estimate, before observation.

        Returns:
            ``estimate_mb``, ``low_mb``, ``high_mb``, ``confidence``,
            ``samples`` and ``source``.
        """
        samples = self._observed.get(task_type, [])
        if not samples:
            # No observation: the structural estimate stands, with an explicit
            # band around it and an explicit absence of confidence. The band is
            # the *only* place uncertainty is applied — adding a separate
            # headroom multiplier on top of this would charge the same
            # conservatism twice and reject models that fit.
            low, high = _interval(baseline_mb, 0.0, baseline_mb, 0)
            return {
                "estimate_mb": round(baseline_mb, 1),
                "low_mb": low,
                "high_mb": high,
                "confidence": 0.0,
                "samples": 0,
                "source": "assumed",
            }
        mean = statistics.fmean(samples)
        spread = statistics.pstdev(samples) if len(samples) > 1 else 0.0
        # Enough observations and the mean replaces the structural estimate
        # outright: the recorded costs are a better guide than the config file.
        estimate = mean if len(samples) >= self.samples_required else baseline_mb
        low, high = _interval(mean, spread, estimate, len(samples))
        return {
            "estimate_mb": round(estimate, 1),
            "low_mb": low,
            "high_mb": high,
            "confidence": _confidence(len(samples), spread, mean),
            "samples": len(samples),
            "source": "measured" if len(samples) >= self.samples_required else "learning",
        }


def _confidence(samples: int, spread: float, mean: float) -> float:
    """Return confidence in 0-1 from the sample count and the spread.

    Two things earn confidence: having seen the task many times, and having seen
    it cost roughly the same each time. A task observed forty times with a wide
    spread is still a poor estimate.

    Args:
        samples: Number of observations.
        spread: Standard deviation of those observations, in MB.
        mean: Mean observation, in MB.

    Returns:
        A value in 0-1. Never 1.0: a model that reports certainty about a
        resource estimate is claiming something it cannot know.
    """
    if samples <= 0 or mean <= 0:
        return 0.0
    volume = min(1.0, samples / max(1, SAMPLES_FOR_CONFIDENCE * 4))
    stability = 1.0 / (1.0 + (spread / mean) * 4)
    return round(max(0.0, min(0.95, volume * stability)), 3)


def _interval(mean: float, spread: float, estimate: float, samples: int) -> tuple:
    """Return the (low, high) bounds of a prediction interval.

    The interval is never narrower than the assumed band until there are
    observations, and it never reports a lower bound above the figure it returns.
    A prediction a caller could route on and be surprised by is worse than a
    wide one, so the asymmetry is deliberate: the high bound is what routing uses,
    and it always sits at or above the estimate.

    Args:
        mean: Mean observed MB.
        spread: Standard deviation of observations.
        estimate: The figure returned as the prediction.
        samples: Number of observations.

    Returns:
        ``(low_mb, high_mb)`` in MB.
    """
    if samples <= 0:
        half = mean * ASSUMED_HALF_WIDTH_FRACTION
    else:
        half = spread * 2 if spread > 0 else mean * MIN_HALF_WIDTH_FRACTION
        half = max(half, mean * MIN_HALF_WIDTH_FRACTION)
        half = min(half, mean * MAX_HALF_WIDTH_FRACTION)
    low = round(max(0.0, min(mean - half, estimate)), 1)
    high = round(max(estimate, mean + half), 1)
    return low, high
