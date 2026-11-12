"""Voice API contracts with real gates and mocked audio/model transports."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.permission_gate import PermissionGate
from brain.spinal_cord.kill_switch import get_kill_switch
from face.api import routes_voice, routes_voice_media


@pytest.fixture
def voice_app():
    """Build app-scoped voice collaborators without microphone or model downloads."""
    app = FastAPI()
    app.state.kill_switch = get_kill_switch()
    app.state.trust = SimpleNamespace(current=lambda: 3)
    app.state.temporal_lobe = SimpleNamespace(
        start_listening=AsyncMock(return_value="actual-session"),
        stop_listening=AsyncMock(return_value="spoken words"),
        whisper=SimpleNamespace(transcribe_async=AsyncMock(return_value={"text": "open my notes"})),
    )
    app.state.broca_area = None
    app.state.reticular_formation = None
    app.include_router(routes_voice.router)
    app.include_router(routes_voice_media.router)
    return app


def enable_stt(monkeypatch):
    """Mock external decode/model availability, not API or permission behavior."""
    original = routes_voice_media.importlib.util.find_spec
    monkeypatch.setattr(routes_voice_media.importlib.util, "find_spec", lambda name: object() if name == "whisper" else original(name))
    decode = AsyncMock(return_value=np.full(1600, 0.1, dtype=np.float32))
    monkeypatch.setattr(routes_voice_media, "decode_audio", decode)
    return decode


def test_listen_start_uses_supported_config(voice_app):
    """The previously crashing language parameter reaches a real config object."""
    with TestClient(voice_app) as client:
        result = client.post('/api/voice/listen/start', json={"language": "ta"})
    assert result.status_code == 200
    decision, config = voice_app.state.temporal_lobe.start_listening.call_args.args
    assert config.language == "ta"
    assert decision.allowed
    assert result.json()["session_id"] == "actual-session"


def test_unavailable_service_remains_503(voice_app):
    """Service absence is not disguised as an internal 500 or success."""
    voice_app.state.temporal_lobe = None
    with TestClient(voice_app) as client:
        result = client.post('/api/voice/listen/start', json={})
    assert result.status_code == 503


@pytest.mark.parametrize("level", [1, 2])
def test_voice_consent_bound_single_use_and_rechecked(voice_app, monkeypatch, level):
    """An uploaded recording follows trust policy and cannot replay an approval."""
    enable_stt(monkeypatch)
    voice_app.state.trust.current = lambda: level
    with TestClient(voice_app) as client:
        def send(data=b"recorded audio", identifier=""):
            return client.post('/api/voice/transcribe?confirmation_id=' + identifier, content=data, headers={"Content-Type": "audio/webm"})
        pending = send()
        assert pending.status_code == 202
        voice_app.state.temporal_lobe.whisper.transcribe_async.assert_not_called()
        identifier = pending.json()["confirmation_id"]
        result = send(identifier=identifier)
        assert result.status_code == 200, result.text
        assert result.json()["text"] == "open my notes"
        assert send(identifier=identifier).status_code == 400
        another = send().json()["confirmation_id"]
        assert send(b"different recording", another).status_code == 400


def test_stop_prevents_transcription(voice_app, monkeypatch):
    decode = enable_stt(monkeypatch)
    voice_app.state.kill_switch.trigger()
    with TestClient(voice_app) as client:
        result = client.post('/api/voice/transcribe', content=b"audio", headers={"Content-Type": "audio/webm"})
    assert result.status_code == 409
    decode.assert_not_called()


@pytest.mark.parametrize("data,media,status", [(b"", "audio/webm", 400), (b"x", "text/plain", 415), (b"x" * (8 * 1024 * 1024 + 1), "audio/wav", 413)])
def test_upload_bounds(voice_app, data, media, status):
    with TestClient(voice_app) as client:
        result = client.post('/api/voice/transcribe', content=data, headers={"Content-Type": media})
    assert result.status_code == status


def test_speech_returns_playable_bytes_and_removes_temp_file(voice_app, tmp_path):
    """A local filesystem path is not mistaken for browser audio delivery."""
    path = tmp_path / "speech.wav"
    path.write_bytes(b"RIFF-test-audio")
    voice_app.state.broca_area = SimpleNamespace(speak=AsyncMock(return_value=str(path)))
    with TestClient(voice_app) as client:
        result = client.post('/api/voice/speak', json={"text": "Hello"})
    assert result.status_code == 200
    assert result.headers['content-type'] == 'audio/wav'
    assert result.content == b"RIFF-test-audio"
    assert not path.exists()


async def test_real_tts_async_pipeline_works_inside_event_loop(monkeypatch):
    """Exercise actual Broca and Edge adapter; mock only Microsoft's transport."""
    from brain.broca_area import tts_engine
    from brain.broca_area.broca_area import BrocaArea
    async def save(path):
        Path(path).write_bytes(b"encoded-mp3")
    monkeypatch.setattr(tts_engine.edge_tts, "Communicate", lambda text, voice: SimpleNamespace(save=save))
    area = BrocaArea(default_engine="edge")
    decision = PermissionGate().decide(Action(category=ActionCategory.VOICE_OUTPUT, target="test speech"), 3)
    path = await area.speak(decision, "Hello")
    try:
        assert Path(path).read_bytes() == b"encoded-mp3"
        assert not area.is_speaking()
    finally:
        Path(path).unlink()


async def test_voice_components_reject_fabricated_decision():
    from brain.broca_area.broca_area import BrocaArea
    area = BrocaArea(default_engine="edge")
    with pytest.raises(ValueError, match="permission decision"):
        await area.speak({}, "must not synthesize")


def test_wake_word_not_claimed_when_detector_missing(voice_app):
    voice_app.state.reticular_formation = SimpleNamespace(wake_word=None)
    with TestClient(voice_app) as client:
        result = client.post('/api/voice/wake-word/enable', json={})
    assert result.status_code == 503


def test_silent_recording_does_not_hallucinate_a_command(voice_app, monkeypatch):
    decode = enable_stt(monkeypatch)
    decode.return_value = np.zeros(1600, dtype=np.float32)
    with TestClient(voice_app) as client:
        result = client.post('/api/voice/transcribe', content=b"silent recording", headers={"Content-Type": "audio/webm"})
    assert result.status_code == 200
    assert result.json() == {"text": "", "status": "no_speech"}
    voice_app.state.temporal_lobe.whisper.transcribe_async.assert_not_called()
