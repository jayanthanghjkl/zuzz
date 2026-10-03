"""Hypothalamus — System resource monitor.

Reads RAM, VRAM, CPU, GPU, disk and network state in a single snapshot so the
model manager can decide whether a model fits before it is loaded.

The monitor degrades gracefully: on a machine with no NVIDIA driver, ``pynvml``
is absent and VRAM is reported as ``unknown`` rather than crashing the agent.
Model loading then falls back to conservative estimates from the config. This is
not a performance limitation — it is a safety one. A resource monitor that
refuses to boot when the GPU is unavailable is one that has just taken the agent
offline.

Hardware dependencies are injected through constructor arguments typed as
``Any`` so tests can substitute stubs: a monitor can be exercised with a fake
``psutil.Process`` or a fake ``pynvml`` handle without touching the real machine.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.config import env_int


def _gpu_unavailable(reason: str) -> Dict[str, Any]:
    """Return the GPU reading for a machine that cannot be read.

    ``temperature_c`` is None rather than 0. A caller that cannot tell a missing
    sensor from a cold GPU will treat the cold reading as fact and skip the
    thermal checks that a real reading would have triggered.
    """
    return {
        "percent": 0.0,
        "memory_percent": 0.0,
        "temperature_c": None,
        "power_draw_w": 0.0,
        "name": "unknown",
        "available": False,
        "reason": reason,
    }


class ResourceMonitor:
    """Reads system resource state.

    Attributes:
        _psutil: Lazily imported ``psutil`` module, or None.
        _nvml: Lazily imported ``pynvml`` module, or None.
        _process: A ``psutil.Process`` for the current process.
    """

    def __init__(
        self,
        process: Any = None,
        nvml_handle: Any = None,
    ) -> None:
        """Bind the monitor to its dependencies.

        Args:
            process: A ``psutil.Process`` for the current process. When None,
                ``psutil.Process()`` is used at call time.
            nvml_handle: A ``pynvml`` handle object, for tests that need to
                inject a fake GPU.
        """
        self._process = process
        self._nvml = nvml_handle
        self._net_last: Dict[str, Any] = {}
        self._net_last_time: float = 0.0

    # -- RAM ------------------------------------------------------------------

    def get_ram(self) -> Dict[str, Any]:
        """Return system RAM statistics.

        Returns:
            ``total_mb``, ``used_mb``, ``available_mb`` and ``percent``.
        """
        try:
            import psutil
        except ImportError:
            return {"total_mb": 0, "used_mb": 0, "available_mb": 0, "percent": 0.0}
        mem = psutil.virtual_memory()
        return {
            "total_mb": round(mem.total / (1024 * 1024), 1),
            "used_mb": round(mem.used / (1024 * 1024), 1),
            "available_mb": round(mem.available / (1024 * 1024), 1),
            "percent": round(mem.percent, 1),
        }

    def get_agent_ram(self) -> Dict[str, Any]:
        """Return this process's RSS against the agent RAM budget.

        Returns:
            ``ram_mb``, ``budget_mb`` and ``percent_of_budget``.
        """
        budget = env_int("MAX_AGENT_RAM_MB", 3072)
        try:
            proc = self._process or self._psutil_process()
            rss = proc.memory_info().rss / (1024 * 1024)
        except Exception:
            rss = 0.0
        return {
            "ram_mb": round(rss, 1),
            "budget_mb": budget,
            "percent_of_budget": round(rss / budget * 100, 1) if budget else 0.0,
        }

    # -- VRAM -----------------------------------------------------------------

    def get_vram(self) -> Dict[str, Any]:
        """Return GPU VRAM statistics.

        Uses ``pynvml`` for accurate per-process allocation when available.
        Falls back to ``GPUtil`` when pynvml is absent. When neither is
        available, VRAM is reported as ``unknown``.

        Returns:
            ``total_mb``, ``used_mb``, ``available_mb``, ``per_model`` and
            ``source`` (``"pynvml"``, ``"gputil"`` or ``"unknown"``).
        """
        result = self._vram_via_pynvml()
        if result is not None:
            return result
        result = self._vram_via_gputil()
        if result is not None:
            return result
        return {
            "total_mb": None,
            "used_mb": None,
            "available_mb": None,
            "per_model": {},
            "source": "unknown",
        }

    def _vram_via_pynvml(self) -> Optional[Dict[str, Any]]:
        """Query VRAM through pynvml, returning None when unavailable."""
        try:
            import pynvml
            handle = self._nvml_handle(pynvml)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            return {
                "total_mb": round(mem.total / (1024 * 1024), 1),
                "used_mb": round(mem.used / (1024 * 1024), 1),
                "available_mb": round(mem.free / (1024 * 1024), 1),
                "per_model": {},
                "source": "pynvml",
            }
        except Exception:
            return None

    def _vram_via_gputil(self) -> Optional[Dict[str, Any]]:
        """Query VRAM through GPUtil, returning None when unavailable."""
        try:
            import GPUtil
            gpus = GPUtil.getGPUs()
            if not gpus:
                return None
            gpu = gpus[0]
            return {
                "total_mb": round(gpu.memoryTotal, 1),
                "used_mb": round(gpu.memoryUsed, 1),
                "available_mb": round(gpu.memoryFree, 1),
                "per_model": {},
                "source": "gputil",
            }
        except Exception:
            return None

    # -- CPU ------------------------------------------------------------------

    def get_cpu(self) -> Dict[str, Any]:
        """Return CPU statistics.

        Sampled without an interval, so it reports usage since the previous poll
        instead of blocking for one. An interval would make every read sleep, and
        this is called once a second: a sensor that pauses the event loop to
        measure itself is a sensor that costs more than it reports.

        Returns:
            ``percent``, ``cores`` and ``per_core`` usage percentages.
        """
        try:
            import psutil

            per_core = psutil.cpu_percent(interval=None, percpu=True)
            return {
                "percent": round(psutil.cpu_percent(interval=None), 1),
                "cores": psutil.cpu_count(logical=True) or 1,
                "per_core": [round(p, 1) for p in per_core],
            }
        except Exception:
            return {"percent": 0.0, "cores": 1, "per_core": [0.0]}

    def get_gpu(self) -> Dict[str, Any]:
        """Return GPU utilization, temperature and power.

        Each field is read independently. That is a safety requirement, not a
        style preference: the temperature is what the thermal guardian watches,
        and a blanket ``except`` around the whole read turns one unavailable
        field — GPU utilization is not exposed by every driver — into a reading
        of 0°C. A thermal guard fed 0°C reports ``normal`` forever while the
        hardware heats, which is worse than having no guard at all because the
        operator believes it is working.

        Reads the modern ``nvmlDeviceGetUtilizationRates`` and falls back to the
        older ``nvmlDeviceGetUtilization``, because the two do not coexist
        across pynvml releases.

        Returns:
            ``percent``, ``memory_percent``, ``temperature_c``, ``power_draw_w``,
            ``name`` and ``available``. ``temperature_c`` is ``None`` — never
            0 — when the sensor cannot be read, so a caller can tell a missing
            sensor from a genuinely cold GPU.
        """
        try:
            import pynvml
        except ImportError:
            return _gpu_unavailable("no pynvml")
        try:
            handle = self._nvml_handle(pynvml)
        except Exception as exc:
            return _gpu_unavailable(f"nvml unavailable: {exc}")

        utilization = self._gpu_utilization(pynvml, handle)
        temperature = self._gpu_temperature(pynvml, handle)
        return {
            "percent": utilization.get("percent", 0.0),
            "memory_percent": utilization.get("memory_percent", 0.0),
            "temperature_c": temperature,
            "power_draw_w": self._gpu_power(pynvml, handle),
            "name": self._gpu_name(pynvml, handle),
            "available": temperature is not None,
        }

    def _gpu_utilization(self, pynvml: Any, handle: Any) -> Dict[str, float]:
        """Return GPU utilization, or zeros when the driver does not expose it."""
        for name in ("nvmlDeviceGetUtilizationRates", "nvmlDeviceGetUtilization"):
            call = getattr(pynvml, name, None)
            if call is None:
                continue
            try:
                util = call(handle)
                return {
                    "percent": float(getattr(util, "gpu", 0.0) or 0.0),
                    "memory_percent": float(getattr(util, "memory", 0.0) or 0.0),
                }
            except Exception:
                continue
        return {"percent": 0.0, "memory_percent": 0.0}

    def _gpu_temperature(self, pynvml: Any, handle: Any) -> Optional[int]:
        """Return the GPU temperature, or None when the sensor is unreadable.

        None rather than 0 on purpose: 0°C is a plausible reading and an
        impossible one, and the thermal guardian must be able to tell them apart.
        """
        try:
            return int(
                pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            )
        except Exception:
            return None

    def _gpu_power(self, pynvml: Any, handle: Any) -> float:
        """Return power draw in watts, or 0.0 when unavailable."""
        try:
            return round(pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0, 1)
        except Exception:
            return 0.0

    def _gpu_name(self, pynvml: Any, handle: Any) -> str:
        """Return the GPU name, decoded when the driver returns bytes."""
        try:
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
            return str(name)
        except Exception:
            return "unknown"

    def _nvml_handle(self, pynvml: Any) -> Any:
        """Return an NVML device handle, initialising NVML when needed.

        Args:
            pynvml: The imported module.

        Returns:
            A handle to the first GPU.

        Raises:
            Exception: Whatever NVML raised, so the caller can report why the
                GPU is unavailable instead of silently substituting zeros.
        """
        if self._nvml is not None:
            return self._nvml
        pynvml.nvmlInit()
        return pynvml.nvmlDeviceGetHandleByIndex(0)

    # -- Disk and network ----------------------------------------------------

    def get_disk(self) -> Dict[str, Any]:
        """Return disk usage for the project root.

        Returns:
            ``total_mb``, ``used_mb``, ``available_mb`` and ``percent``.
        """
        try:
            import psutil
            from core.config import ROOT
            usage = psutil.disk_usage(str(ROOT))
            return {
                "total_mb": round(usage.total / (1024 * 1024), 1),
                "used_mb": round(usage.used / (1024 * 1024), 1),
                "available_mb": round(usage.free / (1024 * 1024), 1),
                "percent": round(usage.percent, 1),
            }
        except Exception:
            return {"total_mb": 0, "used_mb": 0, "available_mb": 0, "percent": 0.0}

    def get_network(self) -> Dict[str, Any]:
        """Return network I/O rates since the last call.

        Returns:
            ``bytes_sent``, ``bytes_recv`` and per-second ``rate``.
        """
        import time
        try:
            import psutil
            io = psutil.net_io_counters()
            now = time.time()
            rate = 0.0
            if self._net_last and self._net_last_time:
                dt = now - self._net_last_time
                if dt > 0:
                    rate = round(
                        (io.bytes_sent + io.bytes_recv - self._net_last.get("total", 0)) / dt, 1
                    )
            self._net_last = {"total": io.bytes_sent + io.bytes_recv}
            self._net_last_time = now
            return {
                "bytes_sent": io.bytes_sent,
                "bytes_recv": io.bytes_recv,
                "rate": rate,
            }
        except Exception:
            return {"bytes_sent": 0, "bytes_recv": 0, "rate": 0.0}

    def get_battery(self) -> Optional[Dict[str, Any]]:
        """Return battery state, or None on a machine with no battery.

        Reads ``psutil.sensors_battery``. A desktop returns None, which callers
        must read as "always on AC" rather than as "battery at 0%": treating an
        absent battery as an empty one would pin every desktop into idle mode.

        Returns:
            ``{"on_ac": bool, "percent": int}``, or None when there is no
            battery to report.
        """
        try:
            import psutil

            info = psutil.sensors_battery()
        except Exception:
            return None
        if info is None:
            return None
        return {
            "on_ac": bool(info.power_plugged),
            "percent": int(info.percent) if info.percent else 0,
        }

    # -- Snapshot ------------------------------------------------------------

    def get_snapshot(self) -> Dict[str, Any]:
        """Return all resource readings in one call.

        Returns:
            A dict with ``ram``, ``vram``, ``cpu``, ``gpu``, ``disk``,
            ``network`` and ``agent_ram`` keys.
        """
        return {
            "ram": self.get_ram(),
            "vram": self.get_vram(),
            "cpu": self.get_cpu(),
            "gpu": self.get_gpu(),
            "disk": self.get_disk(),
            "network": self.get_network(),
            "agent_ram": self.get_agent_ram(),
        }

    def _psutil_process(self) -> Any:
        """Return a psutil.Process for the current process."""
        import psutil
        return psutil.Process()
