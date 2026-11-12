# ZUZZ repository review and desk-assistant integration

Reviewed: 2026-11-11. Local baseline: `363353f8bf732dd86b1a6b685d386a803d0b6d11`.
This is an inspection and integration proposal, not implemented hardware support.

## Bottom line

Keep ZUZZ, inference, speech recognition, memory and permissions on a computer.
Use the microcontroller as a network-connected face and input peripheral.
An ESP8266 can be a simple face/button terminal; an ESP32-S3 is the better fit
for a round animated display plus remote microphone and speaker.

The reference is **not ESP8266 firmware** and is **not an AI voice assistant**.
It implements local eyes, maze, steps, timer and scale apps with remote controls.

## Reference inspected

- Repository: https://github.com/gokulraj381/Neo---all-in-one-bot
- Inspected revision: `066b12e260c028aa9d3c08a69168d9451d5edfa5`.
- `README.md`, `platformio.ini`, `src/main.cpp` and display setup inspected via GitHub.
- Board: Waveshare ESP32-S3-LCD-1.28, non-touch, GC9A01 240×240 SPI display,
  QMI8658 IMU, BOOT button, 16 MB flash and 2 MB PSRAM.
- Firmware uses ESP32 BLE, `Preferences`, `WiFi.h`, `WebServer.h` and TFT_eSPI.
- `webInit()` creates a Wi-Fi access point; it does not join your home router.
- `/cmd` and `/status` drive/report local apps, not ZUZZ or an LLM.
- No microphone capture, speech recognition, speech playback or AI connection
  was found in the inspected firmware. The described board has no audio chain.
- No LICENSE file was listed and GitHub returned no license metadata. Clarify
  permission before copying/distributing its source; use the UI idea as reference.

## Repository problems, ordered by integration risk

| Priority | Finding and evidence | Fix before integration |
| --- | --- | --- |
| Critical before LAN exposure | `face/api/server.py` registers control APIs without app-level authentication; chat, confirmation and voice routes also have no caller authentication. Permission checks are not network authentication. | Authenticate HTTP and WebSocket callers, authorize by device capability, check browser origins, limit message size/rate and restrict network access. Do not expose port 8080 publicly. |
| High, reproduced on Linux | `core/paths.py` converts separators to backslashes, then uses host-platform `Path(...).name`. On Linux it misses credential basenames. Existing tests fail for project `.env`, `.ENV` and a Windows SSH private-key path. | Separate filesystem resolution from cross-platform lexical classification, enforce path-component boundaries, and add Linux/Windows regression tests before enabling file actions. These failures do not alone prove an end-to-end file exploit. |
| High, reproduced | `/api/voice/listen/start` passes `language=` to `CaptureConfig`, which has no such field. The request returns HTTP 500 even with an initialized temporal-lobe dependency. | Pass language to the transcription engine/session through a supported contract, or add and propagate an explicit configuration field. Add endpoint tests. |
| High, confirmed stub | `/api/voice/stream` only receives bytes and returns `{status: received, size: ...}`. It does not transcribe audio, call chat or produce speech. | Implement a real remote-audio pipeline; do not mistake the acknowledgement for working STT. |
| High | Voice routes pass `decision={}`; `TemporalLobe` and `BrocaArea` accept but do not validate that decision. Those direct paths do not apply the normal permission/kill-switch gate. | Obtain real gate decisions, enforce them at the operation boundary, audit operations and terminate active audio work on stop. Preserve the immutable rules. |
| High | `websocket_chat()` calls `thalamus.cerebrum.stream(message)` directly. It ignores the received `level` and skips `Router.route()` classification/fallback/resource routing. `stream()` defaults to level 1. Its inference backstop still exists. | Add a router-owned streaming path and use it from both web and hardware clients. Test cloud-only and unavailable-local-model cases. |
| High for installation | Server import eagerly imports communication and voice modules. `whisper_engine.py` imports Whisper/Torch at module scope. Missing optional packages can prevent importing the server before the guarded component initialization runs. | Split core/voice/desktop/communication dependencies, lazy-load optional modules and provide feature-specific health checks. |
| High for speech output | `requirements.txt` does not include `piper-tts` or `edge-tts`, but `TTSEngine` requires one. `BrocaArea()` defaults to Piper. CLI detection in the route does not establish that a usable engine and voice model were initialized. | Declare tested optional TTS dependencies, configure engine and model explicitly, and report actual engine readiness. |
| High for Edge TTS | Async `BrocaArea.speak()` calls sync `TTSEngine.synthesize()`, whose Edge branch calls `asyncio.run()`. This conflicts with an already-running API event loop. Sync inference/synthesis can also block the loop. | Use an awaited async engine interface or bounded worker execution for blocking operations. |
| High for remote audio | `/api/voice/speak` returns a server-local `audio_file` path, not a device-accessible stream or download URL. Capture uses the computer's `sounddevice` microphone. | Separate local and remote audio sources/sinks. Provide authenticated, expiring audio delivery with explicit codec/sample rate. |
| Medium, reproduced | Listen-start wraps an intended 503 initialization error in HTTP 500 through a broad `except Exception`. Several other voice routes use the same pattern. | Re-raise `HTTPException`; return consistent validation, offline and internal-error responses. |
| Medium | `body/ears/audio_stream.py` uses an unbounded asyncio queue and writes to it from the sounddevice callback thread. Asyncio queues are not thread-safe. | Schedule writes with `loop.call_soon_threadsafe`, bound buffers and define overflow/cancellation behavior. |
| Medium | Voice/agent route dependencies are module globals; chat history belongs to the shared Cerebrum. Neither is a per-device/session contract. | Use app-state dependencies and explicit session ownership, turn serialization and disconnect cleanup. |
| Documentation | `project.md` says all phases are complete, but the remote-audio stub and reproduced route failures remain. | Distinguish implemented modules, unit-tested behavior, live-tested features and hardware validation. |

### Validation actually performed

A temporary ignored `.venv` used Python 3.11.2 and a limited dependency set, not
an installation of every pin in `requirements.txt`.

Command:

```sh
.venv/bin/python -m pytest -q tests/test_thalamus.py tests/test_cerebrum.py \
  tests/test_amygdala.py tests/test_amygdala_gate.py tests/test_kill_switch.py \
  tests/test_kill_switch_gate.py tests/test_paths.py
```

Result: **126 passed, 3 failed**; all three failures were in `tests/test_paths.py`.
An isolated FastAPI harness mounted the real voice router, supplied a mock
initialized temporal lobe and loaded the actual `CaptureConfig` class definition
without importing Whisper: listen-start returned the unsupported-language 500;
a 320-byte WebSocket frame returned only a size acknowledgement. With no temporal
lobe, listen-start returned 500 instead of its intended 503.

Full collection in this limited environment found 1,391 tests and eight import
errors involving missing optional dependencies (including ntfy, telegram,
Whisper and sounddevice). This is **not** a full-suite result or proof that those
packages cannot be installed. No live LLM, audio device, firmware build, display,
Wi-Fi latency, memory usage or physical board was tested. Application code was
not changed by this review.

## Recommended architecture

```text
ESP face + button (+ optional IMU)
             | authenticated Wi-Fi WebSocket, small JSON events
             v
Dedicated device gateway / restricted ZUZZ device API
             | session + permission checks
             v
Thalamus -> Cerebrum -> local Ollama / configured cloud provider
             | gated actions, memory, final prose
             v
PC microphone -> STT       TTS -> PC speaker        [first version]
ESP32-S3 mic -> STT        TTS -> ESP32-S3 speaker  [later version]
```

**Start safely:** a separate, narrowly scoped gateway can bind to the LAN while
ZUZZ stays on `127.0.0.1:8080`. The gateway may access loopback internally; the
board must use the gateway computer's LAN IP or resolvable hostname, never its
own `localhost`. A gateway is not a security boundary if it forwards arbitrary
ZUZZ routes. Restrict routes/capabilities, authenticate devices and bind sensitive
confirmations to the trusted browser. Account for chat's ability to trigger actions.

Once the main API is secured, direct LAN binding is possible with
`python start.py --host 0.0.0.0 --port 8080`, but this flag alone exposes all
existing routes. Do **not** use it as the first integration step on an untrusted
network. Use TLS or a protected tunnel outside an isolated trusted test network;
a token over plaintext Wi-Fi is not sufficient protection.

## ESP8266 versus ESP32-S3

| Feature | ESP8266 | ESP32-S3 / Neo hardware |
| --- | --- | --- |
| AI inference / Whisper | Run on the computer | Run on the computer |
| Face and status | Practical with small buffers or OLED | Suited to Neo-style round graphics |
| Neo BLE code | No built-in BLE; remove it | Supported by reference firmware |
| Neo firmware as-is | Not compatible: APIs, pins and memory differ | Matches reference board, but still no AI link |
| Remote mic and speaker | Constrained; not recommended for this project | Better choice with suitable mic, amplifier and firmware |

One 240×240 RGB565 framebuffer needs **115,200 bytes**, before Wi-Fi, JSON,
stacks and other sprites. Neo also creates app background sprites. Do not port
that allocation strategy to an ESP8266. Use partial/line redraws, reduced graphics,
or a 128×64 monochrome OLED (1,024 framebuffer bytes). Render eyes locally;
send state changes, not screenshots or video frames.

For a NodeMCU ESP8266 + I2C OLED prototype, a common mapping is D1/GPIO5 SCL,
D2/GPIO4 SDA, 3.3 V and GND. Verify the exact board/display labels and electrical
requirements first. Avoid boot-strap pins for additional buttons without checking
boot levels. Neo's GPIO40 backlight and other S3 pin definitions do not transfer.

## Firmware and protocol plan (new work, not existing endpoints)

1. Keep Neo's app state machine, eyes and local button/tilt handling on ESP32-S3.
   Add an assistant screen without removing its offline apps.
2. Replace AP-only networking with station mode joining the same router as the
   computer. Optional provisioning AP must use a unique password and be disabled
   after setup; do not retain the reference's shared default `12345678`.
3. Add a WebSocket client. On ESP8266 use its Wi-Fi headers/storage APIs and a
   lightweight renderer instead of copying S3 BLE/Preferences/sprite code.
4. Introduce a versioned device endpoint, for example `/ws/device`. It does not
   exist today. Authenticate the handshake, assign a server-owned device/session,
   reject unknown message types and separate read-only display from action rights.
5. Proposed messages: board sends `button`, `ping` and optional rate-limited
   `telemetry`; server sends `state`, bounded `caption`, `pong` and `error`.
   Include protocol version, request/turn ID and monotonic sequence numbers.
6. Define states: `idle`, `listening`, `thinking`, `speaking`,
   `awaiting_confirmation`, `offline`, `error`, `stopped`. Map them to local eyes.
   Derive authoritative state from operation lifecycle, not LLM-authored commands.
7. Keep graphics responsive: no blocking chat HTTP request in the animation loop.
   Queue events, use bounded messages, heartbeat, exponential reconnect backoff,
   and an offline timeout. Never replay a destructive action after reconnection.
8. Confirm risky actions in the trusted web UI initially. Never treat a generic
   short button press as blanket approval or let firmware change trust levels.

For an initial backend-only text smoke test, existing `POST /api/chat` accepts
`{"message":"Hello ZUZZ"}` and returns `response` plus action/routing metadata.
Use it locally; its whole response is not an ESP8266-friendly device protocol.
The existing `/ws` is text-chat streaming, not an audio or face-state feed, and
has the routing issue noted above. Browser interactions do not currently broadcast
their state to an attached device; that event fan-out must be added explicitly.

## Voice inside the desk bot (second milestone)

Use an ESP32-S3 with a compatible I2S microphone and I2S amplifier/speaker, or a
board with an integrated audio codec. Verify available pins against LCD, IMU,
USB and boot pins before selecting/wiring components. The Waveshare non-touch
LCD board alone is not an audio development board.

Start with push-to-talk, half duplex. A proposed input format is mono signed
16-bit PCM at 16 kHz: 32,000 bytes/second; 20 ms is 640 bytes. Add explicit
start/end controls, bounded chunks, timeouts and maximum recording duration.
Keep capture streaming rather than buffering entire utterances on the MCU.
Server processing: audio ingress -> VAD/STT -> Thalamus -> gated actions -> final
prose -> TTS -> specified output codec/rate -> board playback. Add playback-ended
acknowledgement so `speaking` reflects the speaker, not merely TTS completion.
Use turn IDs and cancellation to discard stale audio. Add wake word and echo
handling only after push-to-talk works. Do not attempt LiveKit/WebRTC on ESP8266.

## Delivery order and acceptance checks

1. Fix credential-path classification, voice config/errors, safety enforcement,
   streaming routing and optional dependency startup. Add regression tests.
2. Choose the exact board/display and local-versus-device audio arrangement.
3. Implement authenticated restricted gateway + device protocol, then face/status.
4. Verify phone/PC chat changes the face, the device survives lost Wi-Fi/server
   restart, the UI never freezes, unauthorized devices are rejected and hardware
   cannot approve sensitive actions or bypass the stop.
5. Add push-to-talk and remote audio only on verified audio-capable hardware.
6. Measure free heap, stack use, latency and sustained operation on the real board.

**Needed before writing flashable firmware:** exact ESP8266/ESP32-S3 board model,
display controller/resolution, available buttons/IMU, microphone/speaker parts,
and whether audio should live on the computer or inside the bot.
