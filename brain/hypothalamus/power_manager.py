"""Hypothalamus — Power Manager.

Manages the three power modes — idle, active, performance — and switches between
them based on user activity and battery state.

* **Idle**: no user input for 5 minutes. Background jobs are reduced, optional
  models are unloaded, and only the fast model stays loaded.
* **Active**: the user is interacting. Normal operation, models loaded on demand.
* **Performance**: a heavy task is running. Idle models are evicted to free VRAM
  and CPU, cloud routing is preferred for complex work.

Battery awareness protects the user: below 20% on a laptop, the manager forces
idle mode and refuses performance mode, because a sudden shutdown mid-task is
how data is lost. On a desktop machine (where ``sensors_battery`` returns None)
the machine is treated as always on AC power.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from core.config import env_int


class PowerManager:
    """Selects and transitions between power modes.

    Attributes:
        modes: The three supported modes in order of intensity.
    """

    modes = ("idle", "active", "performance")

    def __init__(
        self,
        monitor: Any = None,
        idle_threshold_seconds: int = 300,
        low_battery_percent: int = 20,
        battery_check_interval: int = 60,
        logger: Any = None,
    ) -> None:
        """Configure the power manager.

        Args:
            monitor: A :class:`~brain.hypothalamus.resource_monitor.ResourceMonitor`
                for battery detection, or None.
            idle_threshold_seconds: Inactivity before switching to idle.
            low_battery_percent: Battery percentage that forces idle mode.
            battery_check_interval: Seconds between battery checks.
            logger: Activity logger. Every mode change is written to it, so a
                question about why ZUZZ went quiet has an answer with a timestamp.
        """
        self._monitor = monitor
        self.idle_threshold_seconds = idle_threshold_seconds
        self.low_battery_percent = low_battery_percent
        self.battery_check_interval = battery_check_interval
        self._logger = logger
        self._mode: str = "active"
        self._last_user_input: float = 0.0
        self._battery: Optional[Dict[str, Any]] = None
        self._last_battery_check: float = 0.0
        self._mode_changed_at: float = time.time()

    def current_mode(self) -> str:
        """Return the current power mode.

        Returns:
            One of ``idle``, ``active`` or ``performance``.
        """
        return self._mode

    def set_mode(self, mode: str) -> bool:
        """Set the power mode explicitly.

        Battery awareness overrides a ``performance`` request when the machine
        is on low battery — the hardware is protected regardless of what the
        caller asked for.

        Args:
            mode: One of ``idle``, ``active`` or ``performance``.

        Returns:
            True when the mode was set, False when overridden by battery state.

        Raises:
            ValueError: The mode is not one of the three supported names.
        """
        if mode not in self.modes:
            raise ValueError(f"unknown power mode: {mode!r}; must be one of {self.modes}")
        if mode == "performance" and self._on_low_battery():
            return False
        previous = self._mode
        self._set_mode(mode)
        if mode != previous:
            self._log_change(previous, mode, "explicit")
        return True

    def record_user_input(self) -> None:
        """Note that the user is active, resetting the idle timer."""
        self._last_user_input = time.time()
        if self._mode == "idle":
            previous = self._mode
            self._set_mode("active")
            self._log_change(previous, "active", "user_input")

    def auto_mode_switcher(self) -> Dict[str, Any]:
        """Run one auto-switch cycle.

        Checks battery state and user inactivity, transitioning the mode if
        the conditions have changed since the last call.

        Returns:
            ``mode``, ``battery_percent``, ``on_ac`` and ``changed`` (True when
            the mode actually changed this cycle).
        """
        battery = self._read_battery()
        idle_for = time.time() - self._last_user_input
        previous = self._mode
        self._maybe_force_idle(battery)
        if self._mode == "idle" and idle_for < self.idle_threshold_seconds and battery:
            self._mode = "active"
        if self._mode == "active" and idle_for >= self.idle_threshold_seconds:
            self._mode = "idle"
        if self._mode == "active" and battery and not battery["on_ac"]:
            self._mode = "idle"
        changed = self._mode != previous
        if changed:
            self._mode_changed_at = time.time()
            self._log_change(previous, self._mode, "auto")
        return {
            "mode": self._mode,
            "battery_percent": battery.get("percent", "unknown") if battery else "unknown",
            "on_ac": battery["on_ac"] if battery else False,
            "changed": changed,
        }

    def _set_mode(self, mode: str) -> None:
        """Record a mode and restart its uptime clock."""
        self._mode = mode
        self._mode_changed_at = time.time()

    def _log_change(self, previous: str, current: str, cause: str) -> None:
        """Write one mode change to the activity log, if there is one."""
        if self._logger is None:
            return
        try:
            self._logger.log(
                "hypothalamus",
                "power_mode_changed",
                previous=previous,
                current=current,
                cause=cause,
            )
        except Exception:
            # Logging must never be the reason a mode change fails.
            pass

    def _read_battery(self) -> Optional[Dict[str, Any]]:
        """Return battery state, cached for the check interval.

        Goes through the monitor when one was injected, and falls back to psutil
        directly. The indirection is not decoration: it is what makes battery
        awareness testable without a laptop, and it keeps one reader of the
        hardware rather than two that could disagree.

        Returns None on desktop machines where there is no battery, which callers
        treat as "always on AC".
        """
        now = time.time()
        if self._battery is not None and (now - self._last_battery_check) < self.battery_check_interval:
            return self._battery
        reading: Optional[Dict[str, Any]] = None
        if self._monitor is not None:
            getter = getattr(self._monitor, "get_battery", None)
            if getter is not None:
                try:
                    reading = getter()
                except Exception:
                    reading = None
        if reading is None:
            try:
                import psutil

                info = psutil.sensors_battery()
                if info is not None:
                    reading = {
                        "on_ac": bool(info.power_plugged),
                        "percent": int(info.percent) if info.percent else 0,
                    }
            except Exception:
                reading = None
        if reading is None:
            # No battery means mains power. Performance mode stays available.
            reading = {"on_ac": True, "percent": 100, "desktop": True}
        self._battery = reading
        self._last_battery_check = now
        return self._battery

    def _on_low_battery(self) -> bool:
        """Return True when on battery below the low threshold."""
        battery = self._read_battery()
        if battery is None:
            return False
        return not battery["on_ac"] and battery.get("percent", 100) < self.low_battery_percent

    def _maybe_force_idle(self, battery: Optional[Dict[str, Any]]) -> None:
        """Drop to idle when battery is critically low, regardless of mode."""
        if battery and not battery["on_ac"] and battery.get("percent", 100) <= self.low_battery_percent:
            self._set_mode("idle")

    def mode_uptime(self) -> float:
        """Return seconds the current mode has been active."""
        return round(time.time() - self._mode_changed_at, 1)
