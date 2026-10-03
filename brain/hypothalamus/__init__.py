"""Hypothalamus — resource management for ZUZZ.

Owns the machine's limits: how much VRAM models may use, how hot the hardware has
become, which power mode applies, and whether a task will fit before it starts.
Every other brain region asks this one whether it can run.

The rule the whole region exists to enforce is that the VRAM budget is a hard
cap. Running out of VRAM mid-inference does not just fail a request — it can take
the GPU driver down with it, which takes the user's whole desktop with it.
"""

from brain.hypothalamus.exceptions import (
    BudgetExceededError,
    DecisionRequiredError,
    HotSwapRollbackError,
    InsufficientVRAMForBudget,
    ModelLoadError,
    ModelNotFoundError,
    ModelUnloadError,
    PermissionErrorForModel,
    ResourceError,
    ResourceUnavailableError,
    ThermalCriticalError,
    ThermalThrottleError,
)
from brain.hypothalamus.hypothalamus import Hypothalamus
from brain.hypothalamus.model_manager import MANAGE_MODELS, ModelManager
from brain.hypothalamus.model_transport import (
    ModelTransport,
    OllamaTransport,
    SwapWatch,
)
from brain.hypothalamus.power_manager import PowerManager
from brain.hypothalamus.resource_monitor import ResourceMonitor
from brain.hypothalamus.resource_predictor import ResourcePredictor
from brain.hypothalamus.thermal_guardian import ThermalGuardian
from brain.hypothalamus.vram_budget import VRAMBudget

__all__ = [
    "BudgetExceededError",
    "DecisionRequiredError",
    "HotSwapRollbackError",
    "Hypothalamus",
    "InsufficientVRAMForBudget",
    "MANAGE_MODELS",
    "ModelLoadError",
    "ModelManager",
    "ModelNotFoundError",
    "ModelTransport",
    "ModelUnloadError",
    "OllamaTransport",
    "PermissionErrorForModel",
    "PowerManager",
    "ResourceError",
    "ResourceMonitor",
    "ResourcePredictor",
    "ResourceUnavailableError",
    "SwapWatch",
    "ThermalCriticalError",
    "ThermalGuardian",
    "ThermalThrottleError",
    "VRAMBudget",
]
