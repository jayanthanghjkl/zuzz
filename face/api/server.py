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
from brain.basal_ganglia.basal_ganglia import BasalGanglia
from brain.brainstem.activity_logger import get_logger
from brain.brainstem.heartbeat import Heartbeat
from brain.brainstem.process_manager import ProcessManager
from brain.brainstem.scheduler import Scheduler
from brain.cerebellum.cerebellum import Cerebellum
from brain.hippocampus.hippocampus import Hippocampus
from brain.hypothalamus.hypothalamus import Hypothalamus
from brain.occipital_lobe.occipital_lobe import OccipitalLobe
from brain.parietal_lobe.parietal_lobe import ParietalLobe
from brain.prefrontal_cortex.immutable_rules import IntegrityError, verify
from brain.prefrontal_cortex.permission_gate import PermissionGate
from brain.prefrontal_cortex.trust_manager import TrustManager
from brain.spinal_cord.kill_switch import get_kill_switch
from brain.spinal_cord.stop_scope import StopScope
from brain.spinal_cord.watchdog import Watchdog
from brain.thalamus.router import Router
from body.fingers import DirectoryManager, FileManager, TrashManager
from body.hands import Hands
from body.legs import BrowserActions
from body.mouth.kilo import (
    DiffStore,
    KiloConfig,
    KiloEngine,
    KiloGit,
    KiloWorkspace,
    Reviewer,
    Terminal,
)
from core.audit import get_audit_log
from core.config import ROOT, get_settings
from brain.communication_hub.hub import CommunicationHub
from brain.background_agents.agent_manager import AgentManager
from face.api import (
    routes_assess,
    routes_brain,
    routes_browser,
    routes_chat,
    routes_code,
    routes_communication,
    routes_dashboard,
    routes_files,
    routes_habits,
    routes_hands,
    routes_kill_switch,
    routes_memory,
    routes_parietal,
    routes_personality,
    routes_plugins,
    routes_resources,
    routes_safety,
    routes_settings,
    routes_vision,
    routes_background,
)
from face.api.chat_actions import ChatActionRunner
from face.api.file_confirmations import PendingConfirmations

FRONTEND_DIR = ROOT / "face" / "frontend"
STATIC_DIR = FRONTEND_DIR / "static"
PAGES_DIR = FRONTEND_DIR / "pages"

#: The trash purge runs daily; items older than 30 days are
#: handed to the operating system's recycle bin.
TRASH_PURGE_INTERVAL_SECONDS = 86400

#: How often the Hippocampus writes its short-term buffer to disk.
#:
#: Thirty seconds, and the number is a compromise between two ways of losing a
#: memory. The buffer is RAM-only and bounded, so a crash loses everything since the
#: last flush; and it evicts its weakest entry when full, so an unflushed buffer
#: loses the entry the user would have minded most. Thirty seconds bounds the first
#: to one turn of a conversation in practice.
#:
#: Deliberately *not* routed through the Consolidator's threshold. That threshold
#: answers "what is worth keeping?", which is a curation question, and a curation
#: question cannot be what decides whether the user's own words reach the disk. The
#: Memory page still shows what was flushed and still scores it.
MEMORY_FLUSH_INTERVAL_SECONDS = 30


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
    app.state.scheduler.add_job(
        app.state.hippocampus.flush,
        "memory_flush",
        interval_seconds=MEMORY_FLUSH_INTERVAL_SECONDS,
    )
    app.state.logger.log(
        "brainstem",
        "startup",
        version=app.state.version,
        streaming=bool(app.state.settings.get("streaming")),
        stopped=app.state.kill_switch.is_killed(),
    )
    yield
    # The last flush happens before the scheduler stops, so it is the scheduler that
    # is still there to run it. Shutting down first would mean the periodic flush had
    # already stopped and everything from the final half-minute is lost — which is
    # exactly the conversation the user ended on.
    try:
        app.state.hippocampus.flush()
    except Exception as exc:  # pragma: no cover - shutdown must not raise
        app.state.logger.log("hippocampus", "shutdown_flush_failed", error=str(exc))
    app.state.scheduler.shutdown()
    app.state.logger.log("brainstem", "shutdown")


def create_app(memory_dir: Path = None) -> FastAPI:
    """Create and configure the ZUZZ FastAPI application.

    Args:
        memory_dir: Where the Hippocampus keeps its JSONL stores. Defaults to
            ``storage/memory``. Injectable so a test — or a second instance on a
            different port — cannot read or write the developer's own memory; the
            default is unchanged so an ordinary call behaves exactly as before.

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
    # The Occipital Lobe. Given the app's own switch, audit log, logger and
    # Hypothalamus, so a screenshot is refused by the same stop that stops every
    # other action and its model is loaded by the same resource layer that already
    # knows whether Moondream fits. It is deliberately *not* given the gate: the
    # region verifies decisions, it does not make them, and a second gate inside it
    # would be a second reading of the matrix. Built after ``calibrate()`` so the
    # VRAM figures it checks against are the measured ones rather than estimates.
    app.state.occipital_lobe = OccipitalLobe(
        kill_switch=app.state.kill_switch,
        audit=app.state.audit,
        logger=app.state.logger,
        hypothalamus=app.state.hypothalamus,
    )
    # The Parietal Lobe: where the user is and what is running. Built here for the same
    # reason as the other two regions — it needs the kill switch, the audit log and
    # the logger, none of which exist before this point. It is also given the
    # Occipital Lobe's *capture backend*, not the Lobe, so that the monitor layout it
    # reports is the same one that decides which pixels a screenshot grabs. Handing it
    # the Lobe would have worked and been wrong twice over: it reads ``engine`` off it
    # by hand, and it would create a second reader of the display layout that could
    # disagree with the first.
    app.state.parietal_lobe = ParietalLobe(
        kill_switch=app.state.kill_switch,
        audit=app.state.audit,
        logger=app.state.logger,
        capture_backend=getattr(getattr(app.state.occipital_lobe, "engine", None), "backend", None),
    )
    # The Legs layer: the browser. Built here rather than inside the Body because it
    # needs collaborators that do not all exist until this point in the app — the
    # app's own trust manager, so a click is judged by the same trust level as a file
    # write, the Hands layer, so a download lands through the same classification and
    # quarantine path as any other file, and the Occipital Lobe, which supplies the
    # screenshot store. Built after all three so every reference is to a live object
    # rather than to a None placeholder assigned now and filled in later. The
    # permission gate is deliberately *not* handed to it: the routes decide and the
    # routes audit, and a second gate inside the Body would be a second reading of the
    # matrix.
    app.state.legs = BrowserActions(
        kill_switch=app.state.kill_switch,
        logger=app.state.logger,
        file_manager=app.state.file_manager,
        trust_manager=app.state.trust,
        # ``engine``, not ``capture``: on the Occipital Lobe ``capture`` is the
        # public method that takes a decision, and the screenshot store hangs off the
        # capture engine. Reading the shadowed attribute looked right and passed None.
        store=getattr(getattr(app.state.occipital_lobe, "engine", None), "store", None),
    )
    # The Mouth layer: the coding agents. Built here for the same reasons as the Legs
    # above — the engine needs the app's own FileManager, DirectoryManager, kill switch
    # and logger, and a proposal whose "before" text came from a second reader would be
    # a proposal whose rollback restores a fiction.
    #
    # Every collaborator is constructed rather than reached for through its
    # ``get_*()`` accessor, and that is load-bearing rather than tidiness. Those
    # accessors are process-wide singletons that capture whichever collaborators
    # happened to be first, so the engine a second app builds would reuse the first
    # app's FileManager and the first app's kill switch — a real stop on one instance
    # would not stop the other, and a test would write through another app's Body.
    app.state.kilo = KiloEngine(
        workspace=KiloWorkspace(),
        config=KiloConfig(logger=app.state.logger),
        diffs=DiffStore(
            files=app.state.file_manager,
            directories=app.state.directory_manager,
            logger=app.state.logger,
            kill_switch=app.state.kill_switch,
        ),
        git=KiloGit(logger=app.state.logger, kill_switch=app.state.kill_switch),
        terminal=Terminal(logger=app.state.logger, kill_switch=app.state.kill_switch),
        reviewer=Reviewer(logger=app.state.logger),
        files=app.state.file_manager,
        directories=app.state.directory_manager,
        logger=app.state.logger,
        kill_switch=app.state.kill_switch,
    )
    # The Hippocampus, the Cerebellum and the Basal Ganglia. Three regions that
    # change nothing on the machine, so none of them is given the gate — but the
    # Basal Ganglia is given the app's *trust manager*, because a routine's steps
    # are decided at the trust level in force when it runs, and a region holding a
    # second reading of the level would plan against a number nothing else agrees
    # with. It is built after ``app.state.permission_gate`` exists for the same
    # reason: the gate it plans against is the app's, not its own.
    app.state.hippocampus = Hippocampus(memory_dir)
    app.state.cerebellum = Cerebellum()
    app.state.basal_ganglia = BasalGanglia(
        gate=app.state.permission_gate,
        trust=app.state.trust,
        tool_context=lambda: ChatActionRunner(app.state).tool_context(),
    )
    app.state.communication_hub = CommunicationHub()
    app.state.agent_manager = AgentManager()
    # Shipped routines are added on first run, and never over a routine the user
    # has since edited or deleted. Their file wins either way.
    app.state.basal_ganglia.seed()
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
    app.include_router(routes_vision.router)
    app.include_router(routes_browser.router)
    app.include_router(routes_memory.router)
    app.include_router(routes_habits.router)
    app.include_router(routes_personality.router)
    app.include_router(routes_parietal.router)
    app.include_router(routes_code.router)
    app.include_router(routes_communication.router)
    app.include_router(routes_background.router)
    app.include_router(routes_dashboard.router)

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
