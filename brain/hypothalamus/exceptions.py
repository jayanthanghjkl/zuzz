"""Hypothalamus — Resource exceptions.

The errors the resource layer can raise, so callers can distinguish a budget
that genuinely has no room from a GPU that simply has no driver, and so the API
can map them onto the right HTTP status instead of collapsing both to 500.

The hierarchy matters twice over. A :class:`BudgetExceededError` is a 429 —
the system is healthy but full, and the caller's options are to wait, to drop a
model, or to route to the cloud. A :class:`ResourceUnavailableError` is a 503 —
something the system needs is not there, and retrying later may succeed.
"""

from __future__ import annotations

from typing import Any


class ResourceError(Exception):
    """Base class for every Hypothalamus error."""


class BudgetExceededError(ResourceError):
    """Loading a model would breach the VRAM budget.

    Raised with a reason so the caller can show the user *why* the load was
    refused and what would need to be freed.
    """


class InsufficientVRAMForBudget(ResourceError):
    """The total budget cannot accommodate even a model fit to headroom."""


class ModelNotFoundError(ResourceError):
    """The model name is not in the configuration or not downloaded."""


class ModelLoadError(ResourceError):
    """Ollama rejected the load request or timed out."""


class ModelUnloadError(ResourceError):
    """Ollama rejected the unload request."""


class ThermalThrottleError(ResourceError):
    """The GPU or CPU temperature crossed the throttle threshold.

    Carries the temperature and the action taken (model downgrade or full
    unload) so the caller can report what the thermal guardian did.
    """


class ThermalCriticalError(ResourceError):
    """Temperature reached the critical threshold.

    Cloud-only mode is engaged: all local models are unloaded and no new
    local load is accepted until the hardware cools.
    """


class ResourceUnavailableError(ResourceError):
    """A required sensor or library is not available.

    Raised when pynvml and GPUtil are both missing on a system that claims to
    have an NVIDIA GPU, or when the GPU cannot be initialised. The resource
    layer falls back to conservative estimates rather than crashing.
    """


class DecisionRequiredError(ResourceError):
    """A model-management operation was attempted without a decision.

    Mirrors the Hands-layer pattern: loading or unloading a model requires a
    :class:`~brain.prefrontal_cortex.permission_decision.PermissionDecision`
    so the call cannot proceed without one.
    """


class PermissionErrorForModel(DecisionRequiredError):
    """The gate refused a model-management operation.

    Named for what it means rather than for the mechanism. Callers catch this to
    offer the user a confirmation or to route the task to the cloud, so it needs
    to read as a permission problem rather than as a fault in the resource layer.

    Attributes:
        decision: The verdict that produced this refusal, when one was reached.
            Carried rather than discarded because ``ask`` and ``blocked`` need
            different answers from the caller: one opens a confirmation dialog and
            the other does not. A single boolean would force the API to guess.
    """

    def __init__(self, message: str, decision: Any = None) -> None:
        """Record the message and the verdict behind it.

        Args:
            message: Human-readable explanation.
            decision: The ``PermissionDecision`` that refused, or None when the
                refusal happened before any decision existed.
        """
        super().__init__(message)
        self.decision = decision

    @property
    def needs_confirmation(self) -> bool:
        """Return True when a human could approve this by confirming.

        False for a blocked verdict, which no confirmation can unlock.
        """
        return bool(getattr(self.decision, "requires_confirmation", False))


class HotSwapRollbackError(ResourceError):
    """The new model failed to load and the old model could not be restored.

    Hot-swap is atomic: on failure the old model is kept loaded. If even the
    rollback fails, this error is raised with the original cause.
    """
