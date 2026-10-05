# ZUZZ — Setup Guide

Everything needed to get ZUZZ running on Jayanthan's laptop. Follow the steps in
order; each one depends on the previous.

---

## 1. Python Environment

ZUZZ needs Python 3.10 or newer. From the project root:

```powershell
cd web\zuzz-assistant
python --version
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` includes `litellm` for cloud routing. Without it ZUZZ still
starts, but cloud levels return a clear "litellm is not installed" error.

---

## 2. API Keys

```powershell
copy .env.example .env
notepad .env
```

Fill in whichever keys you have. ZUZZ works with **any subset** — local-only is
fine, cloud just gets skipped.

| Variable | Used for | Required? |
|---|---|---|
| `GROQ_API_KEY` | Groq LLaMA 70B (default cloud brain) | Optional |
| `HUGGINGFACE_API_KEY` or `HF_API_KEY` | HuggingFace inference | Optional |
| `OPENROUTER_API_KEY` | OpenRouter models | Optional |
| `NVIDIA_API_KEY` | NVIDIA Build models | Optional |
| `COLAB_API_URL` or `COLAB_URL` or `NGROK_URL` | Colab LLaMA 3.1 8B | Optional |
| `TELEGRAM_BOT_TOKEN` | Telegram messaging (Phase 8) | Optional |
| `NTFY_TOPIC` | Push notifications (Phase 8) | Optional |

Each provider also accepts a model override: `GROQ_MODEL`, `OPENROUTER_MODEL`,
`NVIDIA_MODEL`, `HUGGINGFACE_MODEL`, `COLAB_MODEL`. Leave them unset to use the
defaults.

The provider fallback order lives in `config/models.json`
(`groq → colab → huggingface → openrouter → nvidia`). Only providers with a key
configured are considered; with no keys at all ZUZZ stays local and never makes
a doomed network call.

The ngrok URL changes every time the Colab notebook restarts — update
`COLAB_API_URL` when it does. `/api/providers` shows each provider's state
(healthy, degraded, offline, rate_limited, unconfigured) and the UI header shows
a coloured dot per provider.

Non-secret settings such as port, host and trust level live in
`config/settings.json`, not in `.env`. `.env` is git-ignored and must never be
committed.

---

## 3. Install Ollama

Download the Windows installer from <https://ollama.com/download/windows>, run
it, then verify:

```powershell
ollama --version
```

Leave Ollama running in the background — ZUZZ talks to it over HTTP at
`http://localhost:11434`.

---

## 4. Download the Local Models

```powershell
ollama pull qwen2.5-coder:1.5b    # ~1 GB, always-loaded fast brain (level 1)
ollama pull qwen2.5-coder:3b      # ~2 GB, smart brain (level 2)
ollama pull moondream             # ~1.5 GB, vision (Phase 3)
ollama list
```

Model names come from `.env` (`LOCAL_MODEL_FAST`, `LOCAL_MODEL_SMART`,
`LOCAL_VISION_MODEL`), so you can swap in different models without touching code.

---

## 5. Check the System

```powershell
python scripts\health_check.py
```

Expected output ends with `All critical checks passed`. Warnings about a missing
`.env` or absent Ollama models are informational — ZUZZ degrades to cloud, or to
a clear error message, rather than failing.

---

## 6. Start ZUZZ

```powershell
python start.py
```

Then open <http://localhost:8080> in a browser.

Other options:

```powershell
python start.py --port 9000        # different port
python start.py --reload           # auto-reload during development
```

Stop ZUZZ with `Ctrl+C` in the terminal.

---

## 7. Test End-to-End

Send these from the browser and check the level badge and metadata line under
each reply:

| Message | Expected route |
|---|---|
| "Hello" | Level 1, local Qwen 1.5B, under 1s |
| "What is 2+2?" | Level 1, local Qwen 1.5B |
| "Explain quantum computing" | Level 3, cloud (Groq if keyed) |
| "Write a Python web scraper" | Level 2, local Qwen 3B |
| "Debug why this function returns None" | Level 3, cloud |
| "Monitor prices daily and alert me" | Level 5, background agent |
| "Organize my downloads folder" | Level 2, local Qwen 3B |

Confirm: text streams in progressively, the level badge updates, and the Status
button shows Ollama up with your models listed.

If the UI shows `503` with "All backends failed", the local model is missing and
no cloud key is configured — check steps 2 and 4.

---

## 8. Run the Tests

```powershell
python -m pytest tests -q
```

Expected: `890 passed, 2 skipped`. The suite stubs out Ollama and every cloud provider with
`httpx.MockTransport`, and an autouse fixture strips credentials from `.env`
first — so it runs offline and can never touch a real account.

Run it with the virtual environment active (step 1). Running it against a bare
system interpreter can pass or fail for reasons that have nothing to do with the
code, because the dependencies differ from the pinned set.

## 9. Check Provider Status

```powershell
# In the browser, the header shows a coloured dot per provider.
Invoke-RestMethod http://localhost:8080/api/providers | ConvertTo-Json -Depth 4
```

Each provider reports `healthy`, `degraded`, `offline`, `rate_limited` or
`unconfigured`, plus the reason and its per-minute quota usage.

## 10. Phase 3 Extra Setup (vision + browser)

Phase 3 is not started yet. Everything below can be prepared now so that Step 1
does not stall on it.

The vision and browser packages are already in `requirements.txt`, so step 1
covers them. Two things are **not** covered by pip:

### 10a. Chromium for Playwright

```powershell
venv\Scripts\activate
python -m playwright install chromium
```

Downloads both the full Chromium build and the headless shell (~90 MB). The full
build is the one that matters: ZUZZ's browser runs **visible** by default so
that you can watch it and intervene, with headless available as an explicit
opt-in flag. A headless-shell-only install will fail to show a window.

### 10b. Tesseract — manual, needs administrator

```powershell
winget install UB-Mannheim.TesseractOCR
```

`pytesseract` is only a wrapper around the Tesseract binary; without the binary
installed the OCR fallback silently does nothing. This installer is an OS-level
change, so it is not automated here. After installing, confirm:

```powershell
tesseract --version
```

Restart the terminal afterwards so `PATH` picks it up.

### 10c. Vision model

Moondream is the primary reader, with Tesseract as the second opinion when
Moondream's confidence falls under the OCR threshold:

```powershell
ollama pull moondream
```

### 10d. Phase 3 configuration

Two files ship with the decisions already recorded in them:

| File | Holds |
|---|---|
| `config/url_allowlist.json` | 11 preset domains, download quarantine rules, tab caps |
| `config/vision_thresholds.json` | Per-task confidence thresholds and the OCR fallback ladder |

Both are re-read on every lookup, so editing either takes effect without a
restart. `vision_thresholds.json` is expected to be tuned on real hardware — the
values in it are starting guesses made before any screenshot had been taken.

---

## 11. What Is Not Built Yet

Phases 1 and 2 are built. Phase 2 is code-complete and still awaiting its live
hardware verification. These do not exist yet, even though the folders are there:

- **Vision** (screenshots, Moondream, OCR) — Phase 3
- **Browser automation** (Playwright, navigation, extraction) — Phase 3
- **Memory** (conversation history on disk, semantic search) — Phase 6
- **Encrypted key entry from the web UI** — the vault (`api_vault.py`) works and
  is tested, but connectors still read keys from `.env`

Chat currently keeps history in RAM only, per session.

See `docs/ARCHITECTURE.md` for the component diagram, request flow and a
built-vs-planned table.
