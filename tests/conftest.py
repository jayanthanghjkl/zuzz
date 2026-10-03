"""Shared pytest configuration.

Puts the project root on ``sys.path`` so tests import ``brain``, ``core`` and
``face`` exactly as the running server does, and isolates every test from the
developer's real credentials in `.env`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Every environment variable a provider connector can read, including the
# aliases in `core.config.ENV_ALIASES`. Tests must never reach a real account.
ISOLATED_ENV_VARS = (
    "GROQ_API_KEY",
    "GROQ_KEY",
    "GROQ_MODEL",
    "HUGGINGFACE_API_KEY",
    "HF_API_KEY",
    "HF_MODEL",
    "HUGGINGFACE_MODEL",
    "HUGGINGFACE_TOKEN",
    "OPENROUTER_API_KEY",
    "OPENROUTER_KEY",
    "OPENROUTER_MODEL",
    "NVIDIA_API_KEY",
    "NVIDIA_KEY",
    "NVAPI_KEY",
    "NVIDIA_MODEL",
    "COLAB_API_URL",
    "COLAB_URL",
    "NGROK_URL",
    "COLAB_MODEL",
    "CLOUD_MODEL",
    "OLLAMA_HOST",
    "OLLAMA_URL",
    "TELEGRAM_BOT_TOKEN",
    "NTFY_TOPIC",
)


@pytest.fixture(autouse=True)
def isolate_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip provider credentials so tests cannot touch real accounts.

    Runs before every test. A test that needs a credential sets it afterwards
    with its own ``monkeypatch.setenv``, which then takes precedence.
    """
    for name in ISOLATED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def kill_switch_app(tmp_path: Path):
    """Build an app whose kill switch cannot touch the developer's storage.

    The switch writes to ``ROOT/storage`` and the system temp directory by
    default. A test that engaged the real one would leave ZUZZ stopped on the
    developer's own machine, so every test that reaches the stop endpoints gets
    an app wired to a switch inside ``tmp_path``.
    """
    from brain.spinal_cord.kill_switch import KillSwitch
    from brain.spinal_cord.stop_scope import StopScope
    from brain.spinal_cord.watchdog import Watchdog
    from core.audit import AuditLog
    from face.api.server import create_app

    app = create_app()
    switch = KillSwitch(paths=[tmp_path / "stop.flag"], audit=AuditLog(tmp_path / "audit.jsonl"))
    app.state.kill_switch = switch
    app.state.stop_scope = StopScope(
        switch,
        pausables=[app.state.scheduler],
        cloud_agents=["groq", "colab", "huggingface", "openrouter", "nvidia"],
    )
    app.state.watchdog = Watchdog(switch, scope=app.state.stop_scope)
    app.state.thalamus.cerebrum.bridge.kill_switch = switch
    # Every gate in the app was built in create_app holding the *original*
    # switch, so replacing app.state.kill_switch alone would leave the gates
    # consulting a switch this test cannot engage — and a test that asserts a
    # refusal while the gate still points at a live switch proves nothing. All of
    # them are re-pointed at the isolated one, which is what makes "the stop
    # works" a claim about the code rather than about the fixture.
    for gate in (
        app.state.permission_gate,
        app.state.hypothalamus.models.gate,
        app.state.thalamus.cerebrum.gate,
    ):
        if gate is not None:
            gate.kill_switch = switch
    return app
