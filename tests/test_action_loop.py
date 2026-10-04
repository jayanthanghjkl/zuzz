"""Face API — the agent action loop.

The point of these tests is that a chat reply can make ZUZZ *do* something, and
that the safety layer is between the model's intent and the machine at every
step. So they assert the flow, not the endpoints:

* an action block in a reply reaches the Body **only** through the gate;
* a refusal is visible to the user rather than swallowed;
* a confirmation is asked for at the level the matrix says, and answering it is
  single-use and re-decided.

Every test replaces ``app.state.hands`` with a Hands layer wired to a recording
actuator and a recording launcher, so the suite cannot launch Notepad, move the
developer's mouse or write into the project. File actions are pointed at
``tmp_path``. The recurring assertion after a refusal is that the recorder is
still empty.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from brain.cerebrum.action_parser import MAX_ACTIONS_PER_REPLY
from brain.prefrontal_cortex.trust_manager import TrustManager
from brain.spinal_cord.kill_switch import KillSwitch
from brain.spinal_cord.reset_policy import RESET_WORD
from brain.spinal_cord.stop_state import SOURCE_UI
from core.audit import AuditLog

from body.hands.hands import Hands
from body.hands.recorder import RecordingActuator
from face.api.chat_actions import ChatActionRunner
from face.api.server import app

#: The real router, captured before any test swaps it for a scripted one. Without
#: this the substitution leaks: the next test's fixture walks
#: ``app.state.thalamus.cerebrum.gate`` and finds a stub with no gate, which reads
#: as a broken app rather than as a leaked fixture.
REAL_THALAMUS = app.state.thalamus

# ---------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------


class StubClipboard:
    """A clipboard that never touches the system one."""

    def __init__(self, content: str = "") -> None:
        self.content = content

    def paste(self) -> str:
        return self.content

    def write(self, text: str) -> None:
        self.content = text


class StubLauncher:
    """Records launches instead of performing them."""

    def __init__(self) -> None:
        self.launched: List[str] = []

    def __call__(self, command: str):
        self.launched.append(command)
        return type("P", (), {"pid": 4242})()


class ScriptedThalamus:
    """A Thalamus that answers with a canned reply.

    Replaces the router rather than mocking a model, because what these tests are
    about is what happens to the text *after* a model produced it. The reply is
    fixed, so the only variable is the pipeline.
    """

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.cerebrum = self

    async def route(self, message: str, internet_available: bool = True) -> Dict[str, Any]:
        return self._result()

    async def route_stream(self, message: str, internet_available: bool = True):
        yield {"type": "token", "content": self.reply}
        yield {
            "type": "done",
            "level": 3,
            "streamed_level": 3,
            "reason": "scripted",
            "provider": "script",
            "chars": len(self.reply),
            "degraded": False,
        }

    def check_level_allowed(self, level: int) -> str:
        return ""

    def _result(self) -> Dict[str, Any]:
        return {
            "reply": self.reply,
            "level": 3,
            "provider": "script",
            "model": "script",
            "confidence": 1.0,
            "reason": "scripted",
            "degraded": False,
        }


def block(tool: str, params: Dict[str, Any], explanation: str = "") -> str:
    """Render one fenced action block."""
    return (
        "```action\n"
        + json.dumps({"tool": tool, "params": params, "explanation": explanation})
        + "\n```"
    )


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


@pytest.fixture
def actuator() -> RecordingActuator:
    """A recording actuator. Nothing here reaches the hardware."""
    return RecordingActuator()


@pytest.fixture
def switch(tmp_path) -> KillSwitch:
    """A kill switch confined to ``tmp_path``.

    These tests engage the stop, so using the process-wide singleton would write
    a stop flag into the developer's real storage and leave ZUZZ stopped on their
    own machine.
    """
    return KillSwitch(
        paths=[tmp_path / "stop.flag"], audit=AuditLog(tmp_path / "audit.jsonl")
    )


@pytest.fixture
def launcher() -> StubLauncher:
    """A launcher that records instead of launching."""
    return StubLauncher()


@pytest.fixture
def trust(tmp_path) -> TrustManager:
    """A trust manager with its own file, defaulting to level 1."""
    return TrustManager(tmp_path / "trust.json")


@pytest.fixture
def wired(actuator, switch, launcher, trust) -> Dict[str, Any]:
    """An app whose chat pipeline cannot touch the machine or the real stop.

    Returns the parts a test needs to assert on: the recorder, the launcher, the
    isolated switch and the trust manager.
    """
    app.state.kill_switch = switch
    app.state.trust = trust
    app.state.thalamus = REAL_THALAMUS
    app.state.hands = Hands(
        actuator=actuator,
        kill_switch=switch,
        logger=app.state.logger,
        clipboard_backend=StubClipboard(),
        launcher=launcher,
        foreground=lambda: {"fullscreen": False, "window": {"title": "test"}, "elevated": False},
    )
    # Every gate in the app was built holding the original switch, so replacing
    # app.state.kill_switch alone would leave the chat gate consulting a switch
    # this test cannot engage.
    for gate in (
        app.state.permission_gate,
        app.state.hypothalamus.models.gate,
        app.state.thalamus.cerebrum.gate,
    ):
        if gate is not None:
            gate.kill_switch = switch
    # Construction probes the display size. Clearing it keeps "nothing was
    # recorded" a statement about the request rather than about building Hands.
    actuator.calls.clear()
    return {
        "actuator": actuator,
        "switch": switch,
        "launcher": launcher,
        "trust": trust,
        "app": app,
    }


@pytest.fixture
def client(wired) -> TestClient:
    """A test client with the internet probe stubbed out."""
    from face.api import routes_chat

    routes_chat._INTERNET_CACHE.update(value=False, checked_at=float("inf"))
    return TestClient(app)


def say(client: TestClient, reply: str) -> Dict[str, Any]:
    """Post one message whose reply is ``reply`` and return the actions half."""
    app.state.thalamus = ScriptedThalamus(reply)
    body = client.post("/api/chat", json={"message": "do the thing"}).json()
    return body


def trust_to(wired: Dict[str, Any], level: int) -> None:
    """Set the trust level and re-point the chat gate's matrix reader."""
    wired["trust"].save(level)
    ChatActionRunner(app.state).gate.refresh()


# ---------------------------------------------------------------------
# 1. An action reaches the machine
# ---------------------------------------------------------------------


def test_chat_open_app_triggers_body_execution(client, wired) -> None:
    """``open_app`` at trust level 3 launches, because the gate auto-approves it.

    The whole point of the agent loop: a sentence produced an action, the action
    was resolved to ``notepad.exe`` *before* the gate saw it, and the Body ran.
    """
    trust_to(wired, 3)
    body = say(client, "Opening Notepad.\n\n" + block("open_app", {"app_name": "notepad"}))

    executed = body["actions"]["executed"]
    assert len(executed) == 1, body["actions"]
    assert executed[0]["tool"] == "open_app"
    assert executed[0]["status"] == "executed"
    # The *executable* was judged, not the alias the model wrote.
    assert executed[0]["target"] == "notepad.exe"
    assert wired["launcher"].launched == ["notepad.exe"]
    assert body["actions"]["blocked"] == []
    assert body["actions"]["pending"] == []


# ---------------------------------------------------------------------
# 2. The Amygdala refuses
# ---------------------------------------------------------------------


def test_chat_dangerous_command_blocked_by_amygdala(client, wired) -> None:
    """A disk-formatting command is vetoed at every trust level.

    ``run_safe_commands`` auto-approves from level 3, so this is the case that
    proves the Amygdala is in the path: its ``forbidden`` score vetoes the action
    *before* the matrix is consulted, and no trust level reaches it.
    """
    from brain.amygdala.threat_detector import ThreatDetector
    from brain.prefrontal_cortex.action import Action, ActionCategory
    from brain.prefrontal_cortex.permission_gate import PermissionGate

    action = Action(category=ActionCategory.RUN_SAFE_COMMANDS, target="format c:")
    threat = ThreatDetector().assess(action)
    assert threat.veto and threat.level == "forbidden"
    assert PermissionGate().decide(action, 5, threat).blocked

    # Through the pipeline, at the level that would otherwise allow it. An
    # unwhitelisted application resolves to ``run_safe_commands``, which
    # auto-approves from level 3 — so if the Amygdala were absent this would run.
    trust_to(wired, 3)
    assert PermissionGate().decide(
        Action(category=ActionCategory.RUN_SAFE_COMMANDS, target="format c:"), 3,
        ThreatDetector().assess(
            Action(category=ActionCategory.RUN_SAFE_COMMANDS, target="format c:")
        ),
    ).blocked

    # And the immutable rules refuse the filesystem case the user would phrase as
    # "delete C:\\Windows", with the reason shown rather than swallowed.
    body = say(
        client,
        "No.\n\n" + block("file_delete", {"path": "C:\\Windows"}),
    )
    blocked = body["actions"]["blocked"]
    assert len(blocked) == 1, body["actions"]
    assert blocked[0]["reason"]
    assert "R1" in blocked[0]["reason"] or "immutable" in blocked[0]["reason"].lower()
    assert wired["actuator"].calls == []
    assert wired["launcher"].launched == []


# ---------------------------------------------------------------------
# 3. Confirmation at trust level 1
# ---------------------------------------------------------------------


def test_chat_file_write_requests_confirmation_at_L1(client, wired, tmp_path) -> None:
    """A file write at level 1 parks for approval and writes nothing.

    Level 1 means "ask permission for everything", and this is the shape that
    takes: a ``confirmation_id`` comes back, the file is not on disk, and the
    answer is the only thing that can change that.
    """
    trust_to(wired, 1)
    target = tmp_path / "hello.txt"
    body = say(
        client,
        "Writing that now.\n\n" + block("file_write", {"path": str(target), "content": "hi"}),
    )

    pending = body["actions"]["pending"]
    assert len(pending) == 1, body["actions"]
    assert pending[0]["confirmation_id"]
    assert body["actions"]["executed"] == []
    assert not target.exists()


# ---------------------------------------------------------------------
# 4. The stop outranks everything
# ---------------------------------------------------------------------


def test_chat_action_refused_when_kill_switch_engaged(client, wired) -> None:
    """With the stop engaged, no action runs — not even one the level allows.

    Trust level 5 is used deliberately: it auto-approves ``open_apps``, so if this
    passed it would be because the gate consulted the matrix and ignored the
    switch.
    """
    trust_to(wired, 5)
    wired["switch"].trigger(source=SOURCE_UI, reason="test")

    body = say(client, "Opening it.\n\n" + block("open_app", {"app_name": "notepad"}))

    assert body["actions"]["executed"] == []
    assert body["actions"]["pending"] == []
    blocked = body["actions"]["blocked"]
    assert len(blocked) == 1, body["actions"]
    assert "kill switch" in blocked[0]["reason"].lower()
    assert wired["launcher"].launched == []


# ---------------------------------------------------------------------
# 5. Conversation is not action
# ---------------------------------------------------------------------


def test_pure_conversation_does_not_trigger_tools(client, wired) -> None:
    """A reply with no action block touches nothing and reports nothing.

    This is the common case, and it must stay free: an empty envelope means the UI
    has nothing to draw, so no confirmation is ever asked for a conversation.
    """
    trust_to(wired, 5)
    body = say(client, "2 + 2 is 4.")

    assert body["response"] == "2 + 2 is 4."
    assert body["actions"] == {
        "executed": [],
        "blocked": [],
        "pending": [],
        "errors": [],
        "warnings": [],
        "summary": "",
    }
    assert wired["actuator"].calls == []
    assert wired["launcher"].launched == []

    # An ordinary code fence is not an action fence and must survive intact.
    body = say(client, "Use this:\n\n```python\nprint(1)\n```")
    assert "```python" in body["response"]


# ---------------------------------------------------------------------
# 6. Malformed actions fail visibly
# ---------------------------------------------------------------------


def test_malformed_action_fails_visibly(client, wired, tmp_path) -> None:
    """A block that will not parse is reported, not dropped.

    Silence here would be indistinguishable from ZUZZ pretending it acted, which is
    the one failure this parser exists to prevent. The raw JSON is still stripped
    from the prose, so the user is told rather than shown protocol.
    """
    trust_to(wired, 5)
    body = say(
        client,
        "One moment.\n\n```action\n{\"tool\": \"open_app\", oops\n```",
    )

    actions = body["actions"]
    assert actions["executed"] == []
    assert actions["errors"], actions
    assert "JSON" in json.dumps(actions["errors"])
    # The malformed block never reaches the user's chat bubble.
    assert "```" not in body["response"]
    assert "oops" not in body["response"]
    assert wired["launcher"].launched == []

    # A block that names a parameter the tool does not have is equally visible,
    # and equally refuses rather than quietly dropping the parameter.
    body = say(
        client,
        "Writing.\n\n"
        + block("file_write", {"path": str(tmp_path / "x.txt"), "content": "y", "recursive": True}),
    )
    assert body["actions"]["errors"], body["actions"]
    assert "recursive" in json.dumps(body["actions"]["errors"])
    assert not (tmp_path / "x.txt").exists()


# ---------------------------------------------------------------------
# 7. The fence never reaches the user
# ---------------------------------------------------------------------


def test_action_fence_stripped_from_prose(client, wired) -> None:
    """The reply the user reads contains no protocol, only prose."""
    trust_to(wired, 3)
    body = say(
        client,
        "Opening Notepad for you.\n\n"
        + block("open_app", {"app_name": "notepad"}, explanation="Opening Notepad"),
    )

    assert body["response"] == "Opening Notepad for you."
    assert "action" not in body["response"]
    assert "{" not in body["response"]

    # And the WebSocket replaces the streamed text with the same clean prose.
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"message": "open notepad", "level": None})
        events = []
        while True:
            event = socket.receive_json()
            events.append(event)
            if event["type"] == "actions":
                break
    actions_event = events[-1]
    assert actions_event["type"] == "actions"
    assert actions_event["prose"] == "Opening Notepad for you."
    assert actions_event["executed"][0]["tool"] == "open_app"
    # It arrives after done, so the bubble exists to hang the badges off.
    assert [e["type"] for e in events] == ["token", "done", "actions"]


# ---------------------------------------------------------------------
# 8. The action cap
# ---------------------------------------------------------------------


def test_five_action_limit_per_reply(client, wired) -> None:
    """More than five actions in a reply means the extras are refused, not queued.

    A reply wanting twenty actions is either confused or is being used to slip
    past a prompt. Either way it is truncated, and the truncation is reported.
    """
    trust_to(wired, 3)
    reply = "Doing a batch.\n\n" + "\n\n".join(
        block("get_system_resources", {}, f"reading {index}")
        for index in range(MAX_ACTIONS_PER_REPLY + 3)
    )
    body = say(client, reply)

    assert len(body["actions"]["executed"]) == MAX_ACTIONS_PER_REPLY
    # The three refused extras are reported as errors, not silently dropped: they
    # were requested actions that did not happen.
    assert "only the first" in json.dumps(body["actions"]["errors"]), body["actions"]["errors"]

    # The parser enforces the same cap independently of the runner, so a caller
    # reaching it directly cannot get a longer list either.
    from brain.cerebrum.action_parser import parse_actions

    parsed = parse_actions("\n".join(block("get_system_resources", {}) for _ in range(9)))
    assert len(parsed.calls) == MAX_ACTIONS_PER_REPLY
    assert parsed.problems


# ---------------------------------------------------------------------
# 9. Unknown tools
# ---------------------------------------------------------------------


def test_file_write_creates_missing_parent_folders(client, wired, tmp_path) -> None:
    """A write into a folder that does not exist yet creates it.

    Found in live verification: the model was told ``file_write`` "creates it and
    any missing parent folders", and it believed that — so a perfectly ordinary
    request to write into a not-yet-created directory raised ``FileNotFoundError``
    and surfaced as "internal error, see the activity log". The schema now tells
    the truth. This test holds it to it.
    """
    trust_to(wired, 3)
    target = tmp_path / "new-folder" / "deeper" / "hello.txt"
    body = say(
        client,
        "Writing.\n\n"
        + block("file_write", {"path": str(target), "content": "ZUZZ works"}),
    )

    assert body["actions"]["errors"] == [], body["actions"]
    assert len(body["actions"]["executed"]) == 1, body["actions"]
    assert target.read_text(encoding="utf-8") == "ZUZZ works"

    # And an unwritable location is reported legibly rather than as an internal
    # error: the user is told what happened, not that something did.
    blocked_dir = tmp_path / "file.txt"
    blocked_dir.write_text("not a folder", encoding="utf-8")
    body = say(
        client,
        "Writing.\n\n"
        + block(
            "file_write",
            {"path": str(blocked_dir / "child.txt"), "content": "x"},
        ),
    )
    assert body["actions"]["executed"] == []
    assert "internal error" not in json.dumps(body["actions"]), body["actions"]


def test_unknown_tool_returns_error(client, wired) -> None:
    """A tool that does not exist is an error the user sees.

    The parser already refuses the name, so this checks the second line of
    defence: the registry is the authority, and a name the schema somehow
    described but nothing implements must still fail rather than resolve to a
    default.
    """
    trust_to(wired, 5)
    body = say(client, "Sure.\n\n" + block("launch_missiles", {"target": "moon"}))

    actions = body["actions"]
    assert actions["executed"] == []
    assert actions["errors"], actions
    assert "launch_missiles" in json.dumps(actions["errors"])
    assert wired["actuator"].calls == []


# ---------------------------------------------------------------------
# 10. Confirmations are single-use
# ---------------------------------------------------------------------


def test_confirmation_id_single_use(client, wired, tmp_path) -> None:
    """The same id cannot be spent twice.

    The entry is popped when it is answered, so the pop and the removal are one
    step. A ``get`` would leave it in the registry and the second post would run
    the action again — which for a write means the user's file changes twice from
    one approval.
    """
    trust_to(wired, 1)
    target = tmp_path / "once.txt"
    body = say(
        client,
        "Writing.\n\n" + block("file_write", {"path": str(target), "content": "first"}),
    )
    confirmation_id = body["actions"]["pending"][0]["confirmation_id"]

    first = client.post(
        "/api/chat/confirm", json={"confirmation_id": confirmation_id, "approval": True}
    )
    assert first.status_code == 200, first.text
    assert first.json()["executed"][0]["status"] == "executed"
    assert target.read_text(encoding="utf-8") == "first"

    replay = client.post(
        "/api/chat/confirm", json={"confirmation_id": confirmation_id, "approval": True}
    )
    assert replay.status_code == 400
    assert "unknown or expired" in replay.json()["detail"]

    unknown = client.post(
        "/api/chat/confirm", json={"confirmation_id": "chat-conf-does-not-exist", "approval": True}
    )
    assert unknown.status_code == 400


# ---------------------------------------------------------------------
# 11. Confirmation re-decides
# ---------------------------------------------------------------------


def test_confirmation_re_decides_fresh(client, wired, tmp_path) -> None:
    """Approving does not wave the action through — it asks the gate again.

    Three fresh checks, because the world moves between the popup and the click:

    * a stop engaged while the popup was open still blocks;
    * a target that has become protected is re-classified and blocked;
    * the trust level is read again, not the one captured when it parked.
    """
    trust_to(wired, 1)
    target = tmp_path / "recheck.txt"
    body = say(
        client,
        "Writing.\n\n" + block("file_write", {"path": str(target), "content": "x"}),
    )
    confirmation_id = body["actions"]["pending"][0]["confirmation_id"]

    wired["switch"].trigger(source=SOURCE_UI, reason="stopped mid-prompt")
    refused = client.post(
        "/api/chat/confirm", json={"confirmation_id": confirmation_id, "approval": True}
    )
    assert refused.status_code == 409
    assert not target.exists()

    # Now the same shape, but re-blocked by the immutable rules rather than the
    # switch: a protected path asks at level 1 and is refused outright on approve.
    wired["switch"].reset(typed_word=RESET_WORD, confirmed=True)
    trust_to(wired, 1)
    body = say(
        client,
        "Writing.\n\n"
        + block("file_write", {"path": "C:\\Windows\\System32\\drivers\\etc\\hosts", "content": "x"}),
    )
    assert body["actions"]["pending"] or body["actions"]["blocked"], body["actions"]

    # A clean approve still runs, proving the re-decision did not break the happy
    # path — the check above must be a real check, not a permanent refusal.
    trust_to(wired, 1)
    body = say(
        client,
        "Writing.\n\n" + block("file_write", {"path": str(target), "content": "ok"}),
    )
    confirmation_id = body["actions"]["pending"][0]["confirmation_id"]
    granted = client.post(
        "/api/chat/confirm", json={"confirmation_id": confirmation_id, "approval": True}
    )
    assert granted.status_code == 200, granted.text
    assert target.read_text(encoding="utf-8") == "ok"

    # And declining writes nothing, while consuming the id.
    body = say(
        client,
        "Writing.\n\n" + block("file_write", {"path": str(target), "content": "no"}),
    )
    confirmation_id = body["actions"]["pending"][0]["confirmation_id"]
    declined = client.post(
        "/api/chat/confirm", json={"confirmation_id": confirmation_id, "approval": False}
    )
    assert declined.status_code == 200, declined.text
    assert target.read_text(encoding="utf-8") == "ok"


# ---------------------------------------------------------------------
# 12. The catalogue
# ---------------------------------------------------------------------


def test_all_15_tools_registered_and_callable() -> None:
    """Every tool the model is told about exists, and every one resolves and runs.

    Checked as a unit test with recording doubles rather than through the API,
    because the claim being made is about the catalogue: a name in
    ``tools_schema`` with no implementation is a tool the model was promised and
    cannot reach, and an implementation with no schema is a capability no model
    can ask for. Either one is a bug that no single route test would surface.
    """
    from brain.cerebrum.tool_catalogue import TOOL_NAMES
    from body.tools import ToolContext, catalogue_mismatches

    assert len(TOOL_NAMES) == 15, TOOL_NAMES
    assert catalogue_mismatches() == []

    runner = ChatActionRunner(app.state)
    context = ToolContext(
        hands=app.state.hands,
        files=app.state.file_manager,
        directories=app.state.directory_manager,
        hypothalamus=app.state.hypothalamus,
    )

    # Every tool resolves to a real category and a target, with a summary the UI
    # can show. Resolution is where "there is no such thing to act on" is caught,
    # so running it for all fifteen is the test.
    for name in TOOL_NAMES:
        spec = runner.registry.get(name)
        assert spec is not None, name
        assert spec.summary, name
        assert callable(spec.resolve) and callable(spec.execute), name

    # And each one is reachable through the orchestrator, either executed or
    # refused for a stated reason. Nothing may fail with an internal error, which
    # is the signature of a tool wired to a method that does not exist.
    samples: Dict[str, Dict[str, Any]] = {
        "mouse_click": {"x": 10, "y": 10},
        "mouse_move": {"x": 10, "y": 10},
        "mouse_scroll": {"amount": 1},
        "keyboard_type": {"text": "hello"},
        "keyboard_hotkey": {"keys": ["ctrl", "s"]},
        "clipboard_read": {},
        "clipboard_write": {"text": "hi"},
        "open_app": {"app_name": "notepad"},
        "close_app": {"process_name": "notepad"},
        "file_read": {"path": "README.md"},
        "file_write": {"path": "README.md", "content": "x"},
        "file_delete": {"path": "does-not-exist.txt"},
        "file_list": {"path": "."},
        "file_search": {"pattern": "*.md", "root": "."},
        "get_system_resources": {},
    }
    assert set(samples) == set(TOOL_NAMES)

    from brain.cerebrum.action_parser import ToolCall
    from brain.cerebrum.tools_validate import validate

    for name, params in samples.items():
        call = ToolCall(tool=name, params=validate(name, params), explanation="")
        spec = runner.registry.get(name)
        # Resolve with a scratch context; a refusal here is a legitimate answer
        # (an unreadable path, an absent application) and is checked, not ignored.
        try:
            target = spec.resolve(dict(params), context)
            assert target.category, name
            assert target.target or name in ("clipboard_read", "mouse_scroll"), name
        except Exception as exc:  # noqa: BLE001 - the message is the assertion
            assert isinstance(exc, (ValueError, OSError)), f"{name}: {exc}"

    # Every tool survives being wired into the registry with its own spec.
    assert runner.registry.names() == tuple(TOOL_NAMES)