"""Honest tool outcomes and async audio adapters without external services."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from body.tools.communication_tools import COMMUNICATION_TOOLS
from body.tools.background_agent_tools import BACKGROUND_AGENT_TOOLS
from body.tools.tool_spec import ToolContext, ToolRefused


def context(**services):
    """Build isolated collaborators, not desktop or network clients."""
    return ToolContext(hands=None, files=None, directories=None, **services)


@pytest.mark.asyncio
async def test_communication_tool_awaits_service_in_running_loop():
    """No run_until_complete is allowed inside the chat event loop."""
    hub = SimpleNamespace(send_telegram=AsyncMock(return_value={"message_id": 123}))
    tool = next(t for t in COMMUNICATION_TOOLS if t.name == 'telegram_send_message')
    result = await tool.execute(None, {"message": "hello"}, context(communication_hub=hub))
    assert result == {"message_id": 123}
    hub.send_telegram.assert_awaited_once_with('hello')


@pytest.mark.asyncio
async def test_background_agent_awaits_service_in_running_loop():
    """Deployment only succeeds after its manager returns a real identifier."""
    manager = SimpleNamespace(deploy_agent=AsyncMock(return_value='agent-123'))
    tool = next(t for t in BACKGROUND_AGENT_TOOLS if t.name == 'agent_deploy')
    assert await tool.execute(None, {"agent_type": "research"}, context(agent_manager=manager)) == {"agent_id": "agent-123"}
    manager.deploy_agent.assert_awaited_once()


@pytest.mark.parametrize('name', ['email_schedule_briefing', 'email_check_inbox', 'push_set_quiet_hours'])
def test_unimplemented_tools_never_return_fabricated_success(name):
    """Unsupported operations must refuse, not return scheduled/set/empty data."""
    tool = next(t for t in COMMUNICATION_TOOLS if t.name == name)
    with pytest.raises(ToolRefused, match='not implemented'):
        tool.execute(None, {}, context())


@pytest.mark.asyncio
async def test_optional_enhancement_failure_cannot_prevent_completion():
    """Cosmetic processing failures must not turn completed actions into retries."""
    from face.api.chat_presentation import enhancement_events
    engine = SimpleNamespace(enhance_response=Mock(side_effect=ValueError('cosmetic')))
    assert enhancement_events(engine, 'task', 'done') == []


@pytest.mark.asyncio
async def test_complete_speech_awaits_backend_delivers_bytes_and_removes_temp(tmp_path):
    """The non-stream path cannot nest a loop, mix PCM formats or leak files."""
    from brain.broca_area.streaming_speech import StreamingSpeech
    audio = tmp_path / 'speech.mp3'
    audio.write_bytes(b'encoded speech')
    engine = SimpleNamespace(synthesize_async=AsyncMock(return_value=str(audio)))
    speech = StreamingSpeech(tts_engine=engine)
    callback = Mock()
    speech.set_audio_callback(callback)
    result = await speech.speak_text('Hello')
    engine.synthesize_async.assert_awaited_once_with('Hello')
    callback.assert_called_once_with(b'encoded speech')
    assert result.is_final and not audio.exists()


def test_speaker_stop_stops_blocking_playback(monkeypatch):
    """Stopping must reach sounddevice, not only clear a queue nobody reads."""
    import body.voice.speaker as module
    device = SimpleNamespace(stop=Mock(), play=Mock(side_effect=OSError('no device')))
    monkeypatch.setattr(module, 'sd', device)
    speaker = module.Speaker()
    speaker.stop()
    device.stop.assert_called_once()
    with pytest.raises(module.SpeakerError, match='Playback failed'):
        speaker.play([], blocking=True)
