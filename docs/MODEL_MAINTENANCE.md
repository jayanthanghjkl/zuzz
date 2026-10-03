# Model Maintenance

Cloud providers retire models without warning. A retired model does not fail
loudly at startup — it returns **HTTP 404 on a real request**, so ZUZZ quietly
falls back to a slower provider and the only symptom is "responses got slower".

This happened for real: `llama-3.3-70b-versatile` was retired from Groq, and
ZUZZ was silently answering every cloud request from Colab instead.

---

## 1. The Check

```powershell
python scripts\check_models.py
```

Runs as part of the health check too:

```powershell
python scripts\health_check.py
```

Output looks like this:

```
[OK     ] groq          openai/gpt-oss-120b
         model is served
[UNKNOWN] colab         llama3.1:8b
         no catalogue published; flask running, ollama process alive
[DEAD   ] openrouter    meta-llama/llama-3.1-8b-instruct:free
         model not in catalogue of 464. closest available: ...
```

Machine-readable with `--json`.

### Exit codes

| Exit | Meaning |
|---|---|
| 0 | No dead models |
| 1 | At least one configured provider's model is gone — **fix this** |

### States

| State | Meaning | Action |
|---|---|---|
| `ok` | Model is in the provider's live catalogue | None |
| `dead` | Provider published a catalogue and the model is absent | Change the model |
| `unknown` | Provider publishes no catalogue, is unreachable, or the repo is gated | None, but re-check later |
| `skipped` | No credential configured | None |

`unknown` never fails the check. Only a positive "this model is gone" is a
failure, because an unreachable catalogue is not proof of a bad model.

---

## 2. Where Each Provider's Catalogue Lives

| Provider | Endpoint | Shape |
|---|---|---|
| Groq | `https://api.groq.com/openai/v1/models` | `data[].id` |
| OpenRouter | `https://openrouter.ai/api/v1/models` | `data[].id` |
| NVIDIA | `https://integrate.api.nvidia.com/v1/models` | `data[].id` |
| HuggingFace | `https://huggingface.co/api/models/{repo}` | 200 = repo exists, 404 = gone |
| Colab | none — the notebook owns its own Ollama models | checked via `/health` instead |

The HuggingFace check needs three things that are easy to get wrong: **no
URL-encoding** of the `repo/name` slash (HF rejects `%2F` with HTTP 400), an
**Authorization header** (unauthenticated, the endpoint answers 401 even for a
repo that does not exist), and **redirect following** (renamed repos 307 to the
new name).

`meta-llama/Meta-Llama-3.1-8B-Instruct` is currently such a rename — it 307s to
`meta-llama/Llama-3.1-8B-Instruct`. The check reports `ok` for the configured
name because the redirect resolves, but the canonical id is the newer one.

To browse by hand:

```powershell
# Groq
$h = @{ Authorization = "Bearer $env:GROQ_API_KEY" }
(Invoke-RestMethod https://api.groq.com/openai/v1/models -Headers $h).data.id

# OpenRouter — free tier only
((Invoke-RestMethod https://openrouter.ai/api/v1/models).data.id | Where-Object { $_ -like "*:free" })

# NVIDIA
$h = @{ Authorization = "Bearer $env:NVIDIA_API_KEY" }
(Invoke-RestMethod https://integrate.api.nvidia.com/v1/models -Headers $h).data.id
```

---

## 3. Changing a Model

Per-provider overrides in `.env` take priority over the connector defaults, so
no code change is needed:

```env
GROQ_MODEL=openai/gpt-oss-120b
OPENROUTER_MODEL=qwen/qwen3.8-27b:free
NVIDIA_MODEL=nvidia/llama-3.1-nemotron-70b-instruct
COLAB_MODEL=llama3.1:8b
HUGGINGFACE_MODEL=meta-llama/Meta-Llama-3.1-8B-Instruct
CLOUD_MODEL=groq/openai/gpt-oss-120b
```

`CLOUD_MODEL` is only the LiteLLM fallback path and must carry its provider
prefix. The others are bare model ids.

Then confirm:

```powershell
python scripts\check_models.py
```

To change the *default* rather than the override, edit `DEFAULT_MODEL` in the
connector and add a test asserting the value.

---

## 4. Current Defaults

| Connector | Default | Note |
|---|---|---|
| Groq | `openai/gpt-oss-120b` | LLaMA 3.3 70B was retired |
| Colab | `llama3.1:8b` | Set by your Colab notebook |
| HuggingFace | `meta-llama/Meta-Llama-3.1-8B-Instruct` | |
| OpenRouter | `qwen/qwen3.8-27b:free` | Free tier; LLaMA 3.1 8B free was removed |
| NVIDIA | `nvidia/llama-3.1-nemotron-70b-instruct` | `meta/llama-3.1-70b-instruct` removed |

---

## 5. Routine

Run `scripts\check_models.py`:

- **Before any release or demo** — a dead default model is embarrassing in front
  of users.
- **Whenever responses get noticeably slower** — that is the symptom of silently
  falling back.
- **Monthly**, or when a provider emails about deprecations.

When a provider's catalogue changes shape, update the connector's
`list_models()` — that method is the only place that parses it.

---

## 6. Why a 404 Is Not Loud Enough

A dead model returns 404, which `ConnectorTransport` treats as a generic
`ConnectorError`. The bridge benches the provider and tries the next one, which
is correct behaviour but hides the cause. The provider then reports
`degraded`/`offline` with `detail: "HTTP 404"`.

So the failure *is* recorded — in `/api/providers` and the dashboard dots. It is
just easy to miss. `check_models.py` exists to turn it into a startup-time error
instead of a runtime mystery.
