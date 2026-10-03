"""Priority 1 — the two gaps carried out of Session 8.

Both were named rather than fixed, and both are the same class of defect: a
component that works correctly in isolation while the thing that is supposed to
connect it to the rest of the system does not.

* **Gap A** — ``ProcessManager._loaded_at`` was never written, so the reaper had
  nothing to consider and the dashboard reported an empty machine while two
  models sat in VRAM.
* **Gap B** — ``Cerebrum.think_local`` posted to Ollama without consulting the
  kill switch or the gate. Every other route into ZUZZ passed the gate on the way
  to the Body layer; inference was the one thing that answered regardless of what
  the switch said.

The tests below assert on observable effects — a status payload, an HTTP status,
whether a backend was reached at all — rather than on whether a method was
called, because "the gate was consulted" is only interesting if it changed the
answer.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from brain.cerebrum.cerebrum import Cerebrum, CerebrumError, InferenceRefused
from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.permission_gate import PermissionGate
from brain.spinal_cord.kill_switch import KillSwitch
from brain.spinal_cord.stop_state import SOURCE_UI

SMALL_MODELS = {
    "tiny-model": {"vram_mb": 600, "ram_mb": 200, "priority": "on_demand", "role": "vision"},
    "core-model": {"vram_mb": 700, "ram_mb": 300, "priority": "always_loaded", "role": "fast_responses"},
}


class RecordingTransport:
    """Stands in for Ollama and records every call that reached it."""

    def __init__(self) -> None:
        """Start with nothing recorded."""
        self.loaded: List[str] = []
        self.unloaded: List[str] = []

    async def load(self, model: str, timeout: float) -> None:
        """Record a load."""
        self.loaded.append(model)

    async def unload(self, model: str, timeout: float) -> None:
        """Record an unload."""
        self.unloaded.append(model)


# -- Gap A: the Brainstem's view of what is loaded ----------------------------


def _wired_manager_app(app):
    """Return ``(app, transport)`` with a model manager that cannot reach hardware."""
    from brain.hypothalamus.model_manager import ModelManager
    from brain.hypothalamus.vram_budget import VRAMBudget

    transport = RecordingTransport()
    app.state.hypothalamus.models = ModelManager(
        budget=app.state.hypothalamus.budget,
        models=SMALL_MODELS,
        transport=transport,
        gate=app.state.permission_gate,
        trust_level=3,
        audit=app.state.audit,
        logger=app.state.logger,
    )
    app.state.process_manager.hypothalamus = app.state.hypothalamus
    return app, transport


def _allow_load(target: str = "tiny-model"):
    """Return a decision permitting a model load at the given trust level.

    Uses the same plain string the model manager uses for this category rather
    than the enum, because ``manage_models`` is a trust-matrix key that predates
    the enum and the gate resolves both.

    Decided at trust level 3, where ``manage_models`` is ``auto``. At level 1 it
    is ``ask``, and an unanswered confirmation is correctly refused — which is the
    matrix working, not something to route around.
    """
    return PermissionGate().decide(Action(category="manage_models", target=target), 3)


def test_brain_status_reports_a_model_that_is_actually_loaded(kill_switch_app) -> None:
    """A load must be visible in the status the dashboard reads.

    This is the whole of Gap A. Before the fix the manager reported an empty
    ``loaded`` set forever, so an operator debugging a VRAM problem was told the
    machine was idle while models were resident in it.
    """
    app, _ = _wired_manager_app(kill_switch_app)
    manager = app.state.process_manager

    assert manager.status()["loaded"] == {}, "a fresh machine should report nothing loaded"

    asyncio.run(app.state.hypothalamus.models.load("tiny-model", _allow_load()))
    status = manager.status()

    assert "tiny-model" in status["loaded"], "a loaded model is invisible to the Brainstem"
    assert status["loaded"]["tiny-model"]["vram_mb"] == 600
    assert status["loaded"]["tiny-model"]["priority"] == "on_demand"
    assert status["source"] == "model_manager"


def test_brain_status_drops_a_model_after_it_is_unloaded(kill_switch_app) -> None:
    """And the entry disappears again, so the view cannot go stale in the other
    direction — a model listed as loaded that is not would send an operator
    looking for the wrong problem."""
    app, _ = _wired_manager_app(kill_switch_app)
    manager = app.state.process_manager
    models = app.state.hypothalamus.models

    asyncio.run(models.load("tiny-model", _allow_load()))
    assert "tiny-model" in manager.status()["loaded"]

    asyncio.run(models.unload("tiny-model", protective=True))
    assert "tiny-model" not in manager.status()["loaded"]


def test_brain_status_survives_the_full_load_unload_cycle(kill_switch_app) -> None:
    """Two models in sequence leave no residue from the first.

    A dict that is only ever added to, or only ever cleared wholesale, would pass
    the single-model tests above and still be wrong in the case that matters.
    """
    app, _ = _wired_manager_app(kill_switch_app)
    manager = app.state.process_manager
    models = app.state.hypothalamus.models

    asyncio.run(models.load("tiny-model", _allow_load()))
    asyncio.run(models.unload("tiny-model", protective=True))
    asyncio.run(models.load("core-model", _allow_load("core-model")))

    loaded = manager.status()["loaded"]
    assert list(loaded) == ["core-model"]


def test_the_brain_status_endpoint_serves_the_truth(kill_switch_app) -> None:
    """The route reads the same status, so the API and the object cannot differ."""
    app, _ = _wired_manager_app(kill_switch_app)
    client = TestClient(app)

    asyncio.run(app.state.hypothalamus.models.load("tiny-model", _allow_load()))
    body = client.get("/api/brain/status").json()
    loaded = body.get("process_manager", body).get("loaded", {})
    assert "tiny-model" in loaded, "/api/brain/status disagrees with the manager"


def test_mark_loaded_starts_the_idle_clock_without_a_hypothalamus() -> None:
    """With no resource layer attached the clock still runs.

    The reaper's original code path is what runs in a bare Brainstem, and it reads
    ``_loaded_at``. If nothing writes it, that sweep is inert — which is exactly
    the bug Gap A was, one layer down.
    """
    from brain.brainstem.process_manager import ProcessManager

    manager = ProcessManager()
    assert manager.status()["source"] == "local"
    assert manager.status()["loaded"] == {}

    asyncio.run(manager.mark_loaded("tiny-model"))
    assert "tiny-model" in manager.status()["loaded"]

    # Re-loading resets the clock rather than keeping the first arrival time.
    asyncio.run(manager.mark_loaded("tiny-model"))
    assert "tiny-model" in manager.status()["loaded"]


# -- Gap B: the Cerebrum decides whether it may run ---------------------------


class SpyBackend:
    """Records whether a backend was actually reached."""

    def __init__(self) -> None:
        """Start unreached."""
        self.calls = 0

    async def call(self, *args: Any, **kwargs: Any) -> str:
        """Count the call and answer."""
        self.calls += 1
        return "an answer"


def _stopped_cerebrum(switch: KillSwitch) -> Cerebrum:
    """Return a Cerebrum whose gate consults the given switch."""
    gate = PermissionGate(kill_switch=switch)
    return Cerebrum(gate=gate, trust_level=5)


def test_direct_local_call_is_refused_while_the_switch_is_engaged(tmp_path: Path) -> None:
    """The core of Gap B: the Cerebrum reached without the Router says no.

    ``think_local`` called directly, with a Cerebrum the caller built itself and
    no Thalamus anywhere in sight, must still be refused. This is the path that
    would otherwise let anything holding a reference keep the machine talking
    after a stop.
    """
    switch = KillSwitch(paths=[tmp_path / "stop.flag"])
    switch.trigger(source=SOURCE_UI, reason="test stop")
    cerebrum = _stopped_cerebrum(switch)

    with pytest.raises(InferenceRefused) as caught:
        asyncio.run(cerebrum.think_local("are you there?", level=1))

    assert "kill switch" in str(caught.value).lower()


def test_direct_cloud_call_is_refused_too(tmp_path: Path) -> None:
    """The cloud path is the same hole and is closed the same way.

    A stop that only silences the local model still sends the user's questions to
    a provider, which is a disclosure rather than a silence.
    """
    switch = KillSwitch(paths=[tmp_path / "stop.flag"])
    switch.trigger(source=SOURCE_UI, reason="test stop")
    cerebrum = _stopped_cerebrum(switch)

    with pytest.raises(InferenceRefused):
        asyncio.run(cerebrum.think_cloud("are you there?", level=3))


def test_the_streaming_path_is_refused(tmp_path: Path) -> None:
    """Streaming is the chat UI's route, so it must be refused identically.

    Session 8 found the router's streaming path unguarded. Closing only
    ``think_local`` would have left the browser's own chat box answering during a
    stop — the same bypass, one layer down.
    """

    async def drain() -> List[str]:
        """Collect whatever the stream yields, if anything."""
        return [fragment async for fragment in cerebrum.stream("hello?", level=1)]

    switch = KillSwitch(paths=[tmp_path / "stop.flag"])
    switch.trigger(source=SOURCE_UI, reason="test stop")
    cerebrum = _stopped_cerebrum(switch)

    with pytest.raises(InferenceRefused):
        asyncio.run(drain())


def test_streaming_actually_yields_fragments(tmp_path: Path) -> None:
    """The stream emits fragments at all.

    Found while closing Gap B: ``stream`` collected the whole reply into a list
    and returned ``None``, with no ``yield`` anywhere, so it was an ordinary
    coroutine and ``async for`` over it raised ``TypeError``. Every mocked test
    passed because the mock was a real async generator — the breakage was only
    visible against the real class. Asserted here with the backend stubbed at the
    lowest possible level, because "the chat box receives tokens" is a
    precondition for every other streaming claim.
    """
    import brain.cerebrum.streaming as streaming_module

    async def fake_stream_local(model: Any, messages: Any, timeout: Any):
        """Yield three fragments as a live backend would."""
        for fragment in ("Hello", ", ", "world"):
            yield fragment

    original = streaming_module.stream_local
    streaming_module.stream_local = fake_stream_local
    try:
        cerebrum = _stopped_cerebrum(KillSwitch(paths=[tmp_path / "stop.flag"]))

        async def drain() -> List[str]:
            """Collect the stream."""
            return [fragment async for fragment in cerebrum.stream("hi", level=1)]

        fragments = asyncio.run(drain())
    finally:
        streaming_module.stream_local = original

    assert fragments == ["Hello", ", ", "world"]
    assert cerebrum.history[-1]["content"] == "Hello, world", "history lost the reply"


def test_the_refusal_happens_before_the_backend_is_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    """No request is made to Ollama at all.

    Asserting only that an exception was raised would pass even if the check ran
    *after* the model had already been asked — which would mean the machine did
    the work and then declined to show it. The backend is spied on so that
    ordering is visible.
    """
    import brain.cerebrum.cerebrum as cerebrum_module

    switch = KillSwitch(paths=[Path("unused.flag")])
    switch.paths = [Path("unused.flag")]
    switch.trigger(source=SOURCE_UI, reason="test stop")
    cerebrum = _stopped_cerebrum(switch)

    spy = SpyBackend()

    async def fake_call_local(*args: Any, **kwargs: Any) -> str:
        """Stand in for the Ollama call."""
        return await spy.call()

    monkeypatch.setattr(cerebrum_module, "call_local", fake_call_local)

    with pytest.raises(InferenceRefused):
        asyncio.run(cerebrum.think_local("are you there?", level=1))
    assert spy.calls == 0, "the backend was called despite the stop"


def test_inference_still_works_when_the_switch_is_clear(tmp_path: Path) -> None:
    """The gate must not have become a wall.

    A backstop that refuses everything is indistinguishable from a broken machine.
    This one has to pass, or the fix was not a fix but a different defect.
    """
    import brain.cerebrum.cerebrum as cerebrum_module

    switch = KillSwitch(paths=[tmp_path / "stop.flag"])
    cerebrum = _stopped_cerebrum(switch)

    async def fake_call_local(*args: Any, **kwargs: Any) -> str:
        """Answer without touching hardware."""
        return "hello from the model"

    cerebrum_module_original = cerebrum_module
    assert cerebrum_module_original is not None
    import unittest.mock

    with unittest.mock.patch.object(cerebrum_module_original, "call_local", fake_call_local):
        answer = asyncio.run(cerebrum.think_local("hi", level=1))

    assert answer == "hello from the model"
    assert cerebrum.last_provider == "ollama"


def test_the_refusal_is_a_conflict_not_a_server_error(kill_switch_app) -> None:
    """A stop is a 409, not a 503.

    The distinction matters to the caller: 503 invites a retry, and retrying a
    stopped machine is precisely the wrong advice to give it.
    """
    app = kill_switch_app
    client = TestClient(app)
    app.state.kill_switch.trigger(source=SOURCE_UI, reason="test stop")

    response = client.post("/api/chat", json={"message": "are you there?"})
    assert response.status_code == 409, response.text
    assert "kill switch" in response.json()["detail"].lower()


def test_a_forced_level_request_is_also_refused(kill_switch_app) -> None:
    """The forced-level route bypasses the Router, so it needed its own cover.

    ``POST /api/chat`` with an explicit level calls ``cerebrum.think`` directly
    rather than going through ``thalamus.route``. That was a second way around the
    routing path and therefore a second way around anything the Router consults.
    """
    app = kill_switch_app
    client = TestClient(app)
    app.state.kill_switch.trigger(source=SOURCE_UI, reason="test stop")

    response = client.post("/api/chat", json={"message": "hi", "level": 1})
    assert response.status_code == 409, response.text


def test_inference_refused_is_still_a_cerebrum_error() -> None:
    """Existing handlers must not break.

    A subclass rather than a sibling, so every ``except CerebrumError`` written
    before this exception existed still catches it. Anything that treats a refusal
    as a generic failure keeps working.
    """
    assert issubclass(InferenceRefused, CerebrumError)
    with pytest.raises(CerebrumError):
        raise InferenceRefused("stopped")


def test_the_router_path_is_refused_too(kill_switch_app) -> None:
    """The ordinary route, for completeness.

    The Router does not consult the kill switch itself; it relies on the gate and
    on the Cerebrum underneath it. This proves the chain holds end to end rather
    than assuming it because each link has its own test.
    """
    app = kill_switch_app
    client = TestClient(app)
    app.state.kill_switch.trigger(source=SOURCE_UI, reason="test stop")

    response = client.post("/api/chat", json={"message": "hello"})
    assert response.status_code == 409
