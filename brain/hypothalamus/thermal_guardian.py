"""Hypothalamus — Thermal Guardian.

Watches GPU and CPU temperature and responds before heat damages the hardware.
The chain is warn -> throttle -> critical:

* **Warn** (80°C GPU / 85°C CPU): a model downgrade is logged. A 3B model becomes
  a 1.5B model if one is available. This is a soft response — the user keeps
  working.
* **Throttle** (85°C GPU / 90°C CPU): optional models are evicted and the system
  runs cloud-only until temperature drops. This is also logged to the audit log,
  because sustained throttling is a hardware signal, not a routine event.
* **Critical** (90°C GPU / 95°C CPU): everything local is unloaded and no new
  local load is accepted. The hardware is protected from itself.

The guardian is purely reactive — it never commands a load. It returns a status
that the orchestrator acts on, so the thermal decision is visible in one place.

Thresholds come from ``config/thermal_limits.json`` and are constructor-overridable
so tests can exercise each tier without a hot GPU.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from core.audit import THERMAL_ESCALATION, THERMAL_SENSORS_LOST
from core.config import get_thermal_limits


def _fmt_temp(value: Optional[int]) -> str:
    """Format a temperature for a log line, marking a missing sensor.

    ``None`` renders as ``unreadable`` rather than as ``0``, so a log line can
    never imply a safe reading that was never taken.
    """
    return "unreadable" if value is None else str(value)


class ThermalGuardian:
    """Monitors temperature and escalates responses as heat rises.

    Attributes:
        gpu_warn: GPU temperature that triggers a model downgrade.
        gpu_throttle: GPU temperature that forces optional-model eviction.
        gpu_critical: GPU temperature that forces cloud-only mode.
        cpu_warn: CPU temperature that triggers a model downgrade.
        cpu_throttle: CPU temperature that forces eviction.
        cpu_critical: CPU temperature that forces cloud-only mode.
    """

    def __init__(
        self,
        gpu_warn: int = 80,
        gpu_throttle: int = 85,
        gpu_critical: int = 90,
        cpu_warn: int = 85,
        cpu_throttle: int = 90,
        cpu_critical: int = 95,
        monitor: Any = None,
        audit: Any = None,
        logger: Any = None,
    ) -> None:
        """Configure thresholds and dependencies.

        Args:
            gpu_warn, gpu_throttle, gpu_critical: GPU temperature tiers.
            cpu_warn, cpu_throttle, cpu_critical: CPU temperature tiers.
            monitor: A :class:`~brain.hypothalamus.resource_monitor.ResourceMonitor`
                for live readings, or None to use defaults.
            audit: Where to record throttle and critical events.
            logger: Activity logger for downgrades.
        """
        config = get_thermal_limits()
        gpu_cfg = config.get("gpu", {})
        cpu_cfg = config.get("cpu", {})
        self.gpu_warn = int(gpu_cfg.get("warn_celsius", gpu_warn))
        self.gpu_throttle = int(gpu_cfg.get("throttle_celsius", gpu_throttle))
        self.gpu_critical = int(gpu_cfg.get("critical_celsius", gpu_critical))
        self.cpu_warn = int(cpu_cfg.get("warn_celsius", cpu_warn))
        self.cpu_throttle = int(cpu_cfg.get("throttle_celsius", cpu_throttle))
        self.cpu_critical = int(cpu_cfg.get("critical_celsius", cpu_critical))
        self._monitor = monitor
        self._audit = audit
        self._logger = logger
        self._history: List[Dict[str, Any]] = []
        self._current_level: str = "normal"
        self.last_check: float = 0.0

    @property
    def current_level(self) -> str:
        """Return the current thermal state: normal, warning, throttle, critical."""
        return self._current_level

    def check(self) -> Dict[str, Any]:
        """Read temperatures and return the thermal status.

        Returns:
            ``gpu_temp``, ``cpu_temp``, ``level``, ``action``, ``sensors`` and
            ``timestamp``. ``level`` is one of ``normal``, ``warning``,
            ``throttle``, ``critical`` or ``unmonitored``. ``unmonitored`` is
            reported when a sensor that exists on this class of machine could not
            be read, so the caller can warn the user that the chain is not
            watching rather than showing a green light.
        """
        gpu_temp = self._read_gpu_temp()
        cpu_temp = self._read_cpu_temp()
        now = time.time()
        self.last_check = now
        level, action = self._classify_and_act(gpu_temp, cpu_temp, now)
        record = {
            "timestamp": now,
            "gpu_temp": gpu_temp,
            "cpu_temp": cpu_temp,
            "level": level,
            "action": action,
            "sensors": {
                "gpu": gpu_temp is not None,
                "cpu": cpu_temp is not None,
            },
        }
        self._record_history(record)
        return record

    def _read_gpu_temp(self) -> Optional[int]:
        """Return the GPU temperature in Celsius, or None when unavailable.

        None is not the same as 0. A GPU whose sensor cannot be read is
        *unmonitored*, and reporting it as 0°C would make the guardian assert
        ``normal`` for as long as the fault lasts — telling the operator the
        hardware is protected when nothing is watching it. The caller records the
        absence instead of inferring safety from it.
        """
        if self._monitor is not None:
            try:
                value = self._monitor.get_gpu().get("temperature_c")
            except Exception:
                return None
            return None if value is None else int(value)
        return None

    def _read_cpu_temp(self) -> Optional[int]:
        """Return the CPU temperature in Celsius, or None when unavailable."""
        try:
            import psutil
            temps = psutil.sensors_temperatures()
            if temps:
                for entries in temps.values():
                    for entry in entries:
                        if "cpu" in entry.label.lower() or "core" in entry.label.lower():
                            return int(entry.current)
                first = list(temps.values())[0]
                if first:
                    return int(first[0].current)
        except Exception:
            pass
        return None

    def _classify_and_act(
        self, gpu_temp: Optional[int], cpu_temp: Optional[int], now: float
    ) -> tuple[str, str]:
        """Map temperatures to a level and return the action to take.

        A missing sensor is excluded from the comparison rather than treated as
        zero, and when *every* sensor is missing the level becomes
        ``unmonitored`` — a state distinct from ``normal``, because the guardian
        reporting ``normal`` while reading nothing is the failure mode this whole
        method exists to avoid. Thermal protection still does not stop the agent:
        an unreadable sensor means less protection, not no agent.

        Args:
            gpu_temp: GPU temperature in Celsius, or None when unreadable.
            cpu_temp: CPU temperature in Celsius, or None when unreadable.
            now: Current time for logging.

        Returns:
            A (level, action) pair describing what was decided.
        """
        prev = self._current_level
        readings = [t for t in (gpu_temp, cpu_temp) if t is not None]
        if not readings:
            self._current_level = "unmonitored"
            if prev != "unmonitored":
                self._log_sensor_loss(prev)
            return "unmonitored", "report_thermal_sensors_unavailable"

        critical = (
            gpu_temp is not None and gpu_temp >= self.gpu_critical
        ) or (cpu_temp is not None and cpu_temp >= self.cpu_critical)
        throttle = (
            gpu_temp is not None and gpu_temp >= self.gpu_throttle
        ) or (cpu_temp is not None and cpu_temp >= self.cpu_throttle)
        warning = (
            gpu_temp is not None and gpu_temp >= self.gpu_warn
        ) or (cpu_temp is not None and cpu_temp >= self.cpu_warn)

        if critical:
            level = "critical"
            action = "unload_all_optional_models_cloud_only"
        elif throttle:
            level = "throttle"
            action = "unload_optional_models_cloud_only"
        elif warning:
            level = "warning"
            action = "downgrade_model"
        else:
            level = "normal"
            action = "restore"

        self._current_level = level
        if level != prev and level in ("throttle", "critical"):
            self._log_thermal_event(gpu_temp, cpu_temp, level, action, critical)
        return level, action

    def _log_sensor_loss(self, previous: str) -> None:
        """Record that thermal monitoring stopped working.

        Written to the audit log because it is the loss of a safety mechanism, not
        a routine observation: the machine is still running, and the chain that
        protects it is not watching.
        """
        summary = (
            f"thermal sensors unavailable: guardian was {previous} and can no "
            "longer read temperature; thermal protection is not active"
        )
        if self._audit is not None:
            self._audit.record(
                THERMAL_SENSORS_LOST,
                summary,
                level="dangerous",
                category="thermal",
            )
        if self._logger is not None:
            self._logger.log(
                "hypothalamus",
                "thermal_sensors_lost",
                previous_level=previous,
            )

    def _log_thermal_event(
        self,
        gpu_temp: Optional[int],
        cpu_temp: Optional[int],
        level: str,
        action: str,
        critical: bool,
    ) -> None:
        """Write a thermal event to the audit log and the activity log."""
        summary = (
            f"thermal {level}: gpu={_fmt_temp(gpu_temp)}C "
            f"cpu={_fmt_temp(cpu_temp)}C -> {action}"
        )
        if self._audit is not None:
            self._audit.record(
                THERMAL_ESCALATION,
                summary,
                level="dangerous" if not critical else "forbidden",
                category="thermal",
            )
        if self._logger is not None:
            # ``thermal_action``, not ``action``: the logger's second positional
            # parameter is named ``action``, so passing that name as a field is a
            # duplicate-argument TypeError against the real ActivityLogger — and
            # it would fire in the middle of a safety escalation.
            self._logger.log(
                "hypothalamus",
                "thermal_threshold",
                gpu_temp=gpu_temp,
                cpu_temp=cpu_temp,
                level=level,
                thermal_action=action,
            )
    def _record_history(self, record: Dict[str, Any]) -> None:
        """Keep the last 3600 readings (one hour at 1 Hz)."""
        self._history.append(record)
        if len(self._history) > 3600:
            self._history = self._history[-3600:]

    def history(self, count: int = 60) -> List[Dict[str, Any]]:
        """Return the most recent temperature readings.

        Args:
            count: Maximum number of readings to return.
        """
        return self._history[-count:]

    def recovery(self) -> bool:
        """Return True when temperature has returned to safe levels."""
        return self._current_level in ("normal",)
