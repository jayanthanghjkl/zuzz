# Core chat, actions and voice repairs — 2026-11-11

This is an implementation/verification record, **not a declaration that every ZUZZ feature works**. The earlier `DESK_ASSISTANT_REVIEW.md` records the pre-fix review and hardware proposal.

## What changed

- Streaming chat uses the same routing and permission checks as normal chat. Explicit levels are respected, fallback is allowed before output starts, and a failed partial stream is never replayed as another action-bearing reply.
- Actions execute from the original model protocol, before cosmetic response enhancement. Async tools are awaited, error results are not marked successful, and the browser remains busy until action results and the final completion arrive. Tool outcomes are recorded for the next turn as bounded, untrusted data.
- Empty/invalid WebSocket input is rejected; connection loss and backend errors remain visible. No automatic resend of a potentially destructive task.
- Native POSIX file operations no longer turn paths into malformed Windows filenames. Windows path protection remains lexically checked on non-Windows hosts; POSIX system roots and credentials are protected too. Runtime prompts state the real OS and home instead of a developer's account.
- Voice input/output/wake actions now have explicit permission categories. Input confirmations bind to the recording and language, expire, and are single-use; current trust/stop policy is checked again at execution time.
- Browser push-to-talk sends recognized text through real chat. Where browser recognition is unavailable, recording uploads use bounded decoding and optional Whisper. Silent recordings do not become hallucinated commands.
- Speech output awaits real synthesis and returns playable MP3/WAV bytes, with temporary-file cleanup. Browser-native voices are also available where supported. Browser/server stop paths interrupt active audio; a missing backend is an error, not an acknowledgement pretending to be sound.
- Heavy transcription/audio imports are optional/lazy. Headless startup can serve chat/files while explicitly reporting desktop control as unavailable. Wake word is not enabled without its required detector configuration.
- Communication/background tools no longer start nested event loops. Unimplemented inbox, email-scheduling and quiet-hours operations explicitly refuse instead of returning fabricated success.
- Added `/api/health/readiness`, frontend readiness status, and CI for Python and JavaScript contract tests. The service worker does not cache API state.

## Install and start

Use Python **3.11+**; the synthesis adapter uses Python 3.11 async timeouts.

```sh
python -m venv .venv
# Linux/macOS:
. .venv/bin/activate
# Windows PowerShell instead:
# .venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip check
```

Copy `.env.example` to `.env` locally and configure at least **one working AI backend**. Do not put keys into chat or commit `.env`.

For local chat, install/start Ollama and pull the names configured by `LOCAL_MODEL_FAST` and `LOCAL_MODEL_SMART` (defaults at verification time):

```sh
ollama pull qwen2.5-coder:1.5b
ollama pull qwen2.5-coder:3b
python -m uvicorn face.api.server:app --host 0.0.0.0 --port 8000
```

Or configure a supported cloud provider in `.env`. A configured API key is **not proof** that the provider/model works. Visit `/api/health/readiness`, then ask a real question. `/api/health` only proves the web server is alive.

Desktop automation requires a logged-in graphical desktop on the computer running the server. A headless remote server cannot operate your laptop's desktop. Do not expose the agent publicly without reviewing access control and deployment security.

## Voice setup and practical checks

1. Open `/pages/voice.html` in a supported browser over HTTPS or localhost. Allow microphone permission. A browser client connecting to a server must use its actual origin, not the server's private `localhost`.
2. Select a browser-provided voice if available. Recognition and synthesis support varies by browser; browser recognition may use an external online service.
3. For recording-upload transcription: install `requirements-voice.txt`, install the **ffmpeg executable** on PATH, and allow the first-use Whisper model download. `WHISPER_MODEL=tiny` and `WHISPER_DEVICE=cpu` are defaults, not a latency guarantee. These dependencies/downloads have not been exercised live in the sandbox.
4. Server Edge TTS requires internet access to Microsoft's service. Optional offline Piper requires its ONNX model plus matching model configuration file; set `TTS_ENGINE=piper`, leave `TTS_VOICE` blank and set `PIPER_MODEL` to that model. A model is not bundled.
5. Say “hello”, confirm the displayed transcript, then verify the actual reply and audible output. At low trust, approve the microphone operation when prompted.
6. Ask to create a harmless file in a temporary, permitted folder. Approve if requested. Verify the **file contents on disk**, not just the assistant's prose or an “executed” badge. In this test environment this scenario is covered using a deterministic model transport, not live inference.
7. Test stop during listening/speaking and confirm audio stops. Resume deliberately before submitting another task.

Tone preferences are engine-dependent; arbitrary emotional TTS control is not certified. Always-on wake word and ESP hardware integration are not delivered by these repairs.

## Verification and limits

Final local verification on Python 3.11:

| Check | Result |
|---|---|
| Full Python suite | **1,635 passed, 9 skipped** (45.89 seconds) |
| JavaScript application-wiring contracts | **10 passed** |
| `pip check` | No broken requirements |
| Python compile check | Passed |
| `git diff --check` | Passed |

The nine skips are opt-in physical-audio tests, not audio successes. The new CI workflow has not yet run on GitHub.

Automated tests use fake model transports and isolated temporary files. They exercise the actual API/router/action/voice-adapter code, but cannot establish real model accuracy, microphone recognition quality, remote service availability or physical speaker output. Hardware tests are opt-in rather than silently represented as passes.

Reproduce the checks:

```sh
python -m pytest -q
node --test tests/frontend/*.test.cjs
python -m compileall -q body brain core face
# Only on a configured machine with actual audio devices/models:
# ZUZZ_TEST_LIVE_AUDIO=1 python -m pytest -q -m live_audio
```

JavaScript tests execute application wiring with mocked DOM/audio/browser services. They are **not** a Playwright/browser microphone test.

Actual headless startup smoke check: health/readiness, voice status/catalogue and the voice HTML page returned HTTP 200. Readiness found **no local model, no configured cloud provider and no desktop display**. A real chat request correctly returned **503, all backends unavailable**. This environment therefore cannot honestly demonstrate a live AI answer or desktop action.

Remaining work: validate on the user's configured computer with a real model and microphone/speakers; audit the many unrelated prototype tools before claiming all application features complete; then integrate and physically test selected hardware. Older project completion percentages are historical claims, not renewed certification.
