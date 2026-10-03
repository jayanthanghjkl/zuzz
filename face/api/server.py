"""Face API — FastAPI application.

Builds the ASGI app: mounts the static web UI, registers the route modules and
wires the brain components onto ``app.state``. This module is the composition
root — it is the only place that knows about every component.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from brain.amygdala.threat_detector import ThreatDetector
from brain.brainstem.activity_logger import get_logger
from brain.brainstem.heartbeat import Heartbeat
from brain.brainstem.process_manager import ProcessManager
from brain.brainstem.scheduler import Scheduler
from brain.hypothalamus.hypothalamus import Hypothalamus
from brain.prefrontal_cortex.immutable_rules import IntegrityError, verify
from brain.prefrontal_cortex.permission_gate import PermissionGate
from brain.prefrontal_cortex.trust_manager import TrustManager
from brain.spinal_cord.kill_switch import get_kill_switch
from brain.spinal_cord.stop_scope import StopScope
from brain.spinal_cord.watchdog import Watchdog
from brain.thalamus.router import Router
from body.fingers import DirectoryManager, FileManager, TrashManager
from body.hands import Hands
from core.audit import get_audit_log
from core.config import ROOT, get_settings
from face.api import (
    routes_assess,
    routes_brain,
    routes_chat,
    routes_files,
    routes_hands,
    routes_kill_switch,
    routes_plugins,
    routes_resources,
    routes_safety,
    routes_settings,
)
from face.api.file_confirmations import PendingConfirmations

FRONTEND_DIR = ROOT / "face" / "frontend"
STATIC_DIR = FRONTEND_DIR / "static"
PAGES_DIR = FRONTEND_DIR / "pages"

#: The trash purge runs daily; items older than 30 days are
#: handed to the operating system's recycle bin.
TRASH_PURGE_INTERVAL_SECONDS = 86400


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start and stop the brain's background services with the app.

    Args:
        app: The application being started.

    Yields:
        Control back to the server while it serves requests.
    """
    # A stop that survived the previous run still holds: the flag is on disk, so
    # background jobs must not quietly start again while the user has not reset.
    if app.state.kill_switch.is_killed():
        app.state.scheduler.pause()
    app.state.scheduler.add_job(app.state.heartbeat.beat, "heartbeat", interval_seconds=30)
    app.state.scheduler.add_job(
        app.state.process_manager.unload_idle, "model_reaper", interval_seconds=15
    )
    # The Hypothalamus runs its own cycle rather than being called from the reaper,
    # because the two answer different questions: the reaper asks "is this model
    # still wanted", and the resource cycle asks "is the machine about to run out
    # of VRAM or get too hot to keep going". One cycle per second is the chart's
    # refresh rate, and the escalation it performs is prompt enough to help.
    app.state.scheduler.add_job(
        app.state.hypothalamus.tick, "resource_cycle", interval_seconds=1
    )
    app.state.scheduler.add_job(
        app.state.trash_manager.auto_purge_old,
        "trash_purge",
        interval_seconds=TRASH_PURGE_INTERVAL_SECONDS,
    )
    app.state.logger.log(
        "brainstem",
        "startup",
        version=app.state.version,
        streaming=bool(app.state.settings.get("streaming")),
        stopped=app.state.kill_switch.is_killed(),
    )
    yield
    app.state.scheduler.shutdown()
    app.state.logger.log("brainstem", "shutdown")


def create_app() -> FastAPI:
    """Create and configure the ZUZZ FastAPI application.

    Returns:
        A FastAPI instance with the UI mounted and routes registered.
    """
    settings: Dict[str, Any] = get_settings()
    app = FastAPI(
        title="ZUZZ — Digital Brain",
        description="Local-first AI assistant API.",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.version = "0.1.0"
    app.state.thalamus = Router()
    app.state.heartbeat = Heartbeat()
    app.state.process_manager = ProcessManager()
    app.state.scheduler = Scheduler()
    app.state.logger = get_logger()
    app.state.trust = TrustManager()
    app.state.audit = get_audit_log()
    app.state.kill_switch = get_kill_switch()
    # The Body layer: every file operation is gated before it reaches
    # these, and these re-check the decision, the classification and
    # the switch a second time before touching the disk.
    app.state.trash_manager = TrashManager()
    app.state.file_manager = FileManager(
        trash_manager=app.state.trash_manager
    )
    app.state.directory_manager = DirectoryManager()
    # The Hands layer. Built with the same switch, the same gate decisions and
    # the same logger the rest of the Body uses, so the watchdog's desktop
    # signals count what ZUZZ did rather than what the user did. The kill switch
    # is passed explicitly rather than left to the Hands default so that the
    # app's switch and the Body's switch are provably the same object.
    app.state.hands = Hands(
        kill_switch=app.state.kill_switch,
        logger=app.state.logger,
    )
    # Confirmations are prompts, not contracts: they live in memory
    # only, so a restart cannot turn an unanswered popup into an
    # approval.
    app.state.confirmations = PendingConfirmations()
    # The switch stops the brain's own background work, so the scope is handed
    # the scheduler and the running logger rather than being given names to
    # look up later.
    app.state.stop_scope = StopScope(
        app.state.kill_switch,
        pausables=[app.state.scheduler],
        cloud_agents=["groq", "colab", "huggingface", "openrouter", "nvidia"],
    )
    app.state.watchdog = Watchdog(
        app.state.kill_switch,
        scope=app.state.stop_scope,
        logger=app.state.logger,
    )
    # The Hypothalamus watches what is loaded and how hot the machine is. It
    # shares the app's switch, audit log and activity logger so a model eviction
    # and a stop are recorded in the same place, and it is given the app's own
    # gate so a model-management decision is made by the same gate that decides
    # everything else — a second gate with its own reading of the matrix would
    # be two answers to one question.
    app.state.hypothalamus = Hypothalamus(
        gate=PermissionGate(kill_switch=app.state.kill_switch),
        audit=app.state.audit,
        logger=app.state.logger,
        amygdala=ThreatDetector(),
    )
    app.state.permission_gate = app.state.hypothalamus.models.gate
    # Handed to the router after it exists, because the two reference each other:
    # the resource layer answers whether a task fits, and the router decides where
    # it runs. Assigned here rather than through a constructor so the Router is
    # still the single place routing happens.
    app.state.thalamus.hypothalamus = app.state.hypothalamus
    # The idle reaper now asks the budget before it unloads, so a model is only
    # evicted when the space is actually needed.
    app.state.process_manager.hypothalamus = app.state.hypothalamus
    # The Cerebrum decides for itself whether it may run at all, so that a caller
    # holding one directly — the Router, a future caller, a plugin — cannot reach
    # inference without the switch and the gate being consulted. Given the app's
    # own gate, so the answer is the same answer the rest of the system gives.
    app.state.thalamus.cerebrum.gate = app.state.permission_gate
    # Decide once whether the VRAM figures are measured or estimated, and label
    # them before anything is loaded against them.
    app.state.hypothalamus.calibrate()
    # The bridge asks the same switch object the gate does, so the dashboard
    # cannot show providers running while the gate is denying everything.
    app.state.thalamus.cerebrum.bridge.kill_switch = app.state.kill_switch
    # Refuse to serve rather than run unguarded: an altered rule set means the
    # safety layer is not the one that was approved.
    try:
        verify()
    except IntegrityError as exc:
        raise RuntimeError(f"ZUZZ refused to start: {exc}") from exc

    app.include_router(routes_chat.router)
    app.include_router(routes_brain.router)
    app.include_router(routes_settings.router)
    app.include_router(routes_plugins.router)
    app.include_router(routes_safety.router)
    app.include_router(routes_assess.router)
    app.include_router(routes_kill_switch.router)
    app.include_router(routes_files.router)
    app.include_router(routes_hands.router)
    app.include_router(routes_resources.router)

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    if PAGES_DIR.is_dir():
        app.mount("/pages", StaticFiles(directory=str(PAGES_DIR)), name="pages")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        """Serve the single-page chat UI."""
        return FileResponse(FRONTEND_DIR / "index.html")

    return app


app = create_app()
