"""Face API — dashboard routes.

Aggregates brain status, resources, activity and control endpoints into a
single surface the dashboard page can poll.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Request

from brain.brainstem.activity_logger import get_logger

router = APIRouter()


@router.get("/api/dashboard")
async def dashboard(request: Request) -> Dict[str, Any]:
    """Return the full dashboard payload in one round trip.

    Args:
        request: Used to reach the shared agent components.

    Returns:
        Brain status, resources, recent activity and system health.
    """
    beat = await request.app.state.heartbeat.beat()
    thalamus = request.app.state.thalamus.status()
    kill = request.app.state.kill_switch.status()
    logger = get_logger()
    regions = _build_regions(request.app)
    return {
        "brain": {
            "heartbeat": beat,
            "thalamus": thalamus,
            "scheduler": request.app.state.scheduler.status(),
            "kill_switch": kill,
            "regions": regions,
        },
        "resources": beat,
        "activity": logger.tail(10),
        "health_score": _health_score(beat, thalamus, kill),
    }


def _build_regions(app) -> list[Dict[str, Any]]:
    """Construct a regions list from the initialized brain components."""
    known = [
        ("Cerebrum", getattr(app.state.thalamus, "cerebrum", None)),
        ("Cerebellum", getattr(app.state, "cerebellum", None)),
        ("Brainstem", getattr(app.state, "scheduler", None)),
        ("Thalamus", getattr(app.state, "thalamus", None)),
        ("Hippocampus", getattr(app.state, "hippocampus", None)),
        ("Amygdala", getattr(app.state, "amygdala", None)),
        ("Prefrontal Cortex", getattr(app.state, "permission_gate", None)),
        ("Hypothalamus", getattr(app.state, "hypothalamus", None)),
        ("Occipital Lobe", getattr(app.state, "occipital_lobe", None)),
        ("Parietal Lobe", getattr(app.state, "parietal_lobe", None)),
        ("Basal Ganglia", getattr(app.state, "basal_ganglia", None)),
        ("Spinal Cord", getattr(app.state, "kill_switch", None)),
        ("Reticular Formation", getattr(app.state, "watchdog", None)),
        ("Corpus Callosum", getattr(app.state.thalamus, "cerebrum", None)),
    ]
    out: list[Dict[str, Any]] = []
    for name, obj in known:
        if obj is None:
            continue
        status = "idle"
        try:
            if hasattr(obj, "status") and callable(obj.status):
                st = obj.status()
                if isinstance(st, dict):
                    status = "active" if st else "idle"
        except Exception:
            pass
        out.append({"name": name, "status": status})
    return out


def _health_score(heartbeat: Dict[str, Any], thalamus: Dict[str, Any], kill: Dict[str, Any]) -> int:
    """Derive a 0-100 health score from the live system state."""
    if kill.get("engaged"):
        return 0
    score = 100
    ram = heartbeat.get("ram_mb", 0) or 0
    if ram > 12000:
        score -= 20
    providers = thalamus.get("providers", {})
    if isinstance(providers, dict):
        unhealthy = sum(1 for p in providers.values() if isinstance(p, dict) and p.get("benched"))
        if unhealthy:
            score -= min(30, unhealthy * 10)
    return max(0, min(100, score))


@router.get("/manifest.json")
async def manifest() -> Dict[str, Any]:
    """Return the PWA manifest."""
    return {
        "name": "ZUZZ",
        "short_name": "ZUZZ",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0a0a0a",
        "theme_color": "#00ff00",
    }


@router.get("/sw.js")
async def service_worker() -> Dict[str, Any]:
    """Return the service worker script."""
    return {"cache": "zuzz-v1"}
