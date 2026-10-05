# Kilo Code Integration Plan — Phase 4

**Status:** Research complete. **Nothing implemented.** Awaiting decisions on the five
questions in section 10.

**Researcher:** Phase 4 Session 4
**Date:** 2026-10-05
**Subject:** Should Kilo Code become ZUZZ's code generation and multi-agent coding engine?

---

## 1. Summary of findings

Kilo Code is **not a Python library and cannot become one.** It is a Bun-compiled
TypeScript binary distributed on npm as `@kilocode/cli`. There is no PyPI package, no
Python SDK, no Python binding, and no announced plan for one — this was verified against
the complete PyPI index (905,515 packages; zero matches for `kilocode`).

The research produced one finding that changes the shape of the task, and it is a safety
finding, not a technical one:

> **If ZUZZ lets Kilo write files, every safety layer ZUZZ has built is bypassed.**

Kilo's `edit` and `bash` tools write directly to disk from inside its own process. ZUZZ's
PermissionGate, Amygdala, Body/Fingers (trash, backup, symlink resolution, secret
protection), TrashManager and immutable activity log never see the write. That conflicts
with immutable rules **R3** (never disable safety checks), **R6** (log ALL actions) and
**R9** (the AI cannot change its own trust level), with `PROJECT.md` §17 Architecture
Rule 4 ("Prefrontal Cortex checks EVERY action before execution"), and with the
instruction that all Kilo file writes must go through Body/Fingers.

The intended mitigation — Kilo's sandbox — **does not exist on Windows**, and Jayanthan's
machine is Windows 11. Per Kilo's own documentation, enabling `sandbox.enabled: true`
where the policy cannot be enforced *blocks restricted tool execution entirely* rather
than degrading to unrestricted execution. So on the target platform there is no runtime
write boundary at all.

What survives this is still worth having. Kilo's agent definition format, its permission
glob rule engine, and its plan → diff → approve → apply workflow are MIT-licensed designs
that map cleanly onto ZUZZ's existing architecture. Section 8 sets out three options and
recommends one.

---

## 2. Kilo architecture overview

### 2.1 What Kilo is

Kilo Code (repo `Kilo-Org/kilocode`, acquired by Anaconda) is an agentic coding
platform. All of its products — VS Code extension, JetBrains plugin, TUI, Cloud Agents —
are thin clients over one core:

```
                        @kilocode/cli  (packages/opencode/)
                     ┌────────────────────────────────┐
                     │  AI agents, tools, sessions,    │
                     │  providers, config, MCP, LSP    │
                     │  HTTP API server + SSE          │
                     └──┬──────────┬──────────────────┘
                        │          │
                ┌───────┴──┐ ┌────┴────┐
                │ TUI      │ │ VS Code │
                └──────────┘ └─────────┘
```

`packages/opencode/` is a fork of upstream [OpenCode](https://github.com/anomalyco/opencode).
It is written in TypeScript on the **Effect** library, built with **Bun**, and ships as a
platform-specific native binary. Current version **7.8.3** (2026-10-01).

### 2.2 Core modules (not the VS Code UI)

| Package | Role |
|---|---|
| `packages/opencode/` → `@kilocode/cli` | The entire runtime: agents, tools, sessions, providers, config, MCP, LSP, HTTP API server |
| `packages/sdk/js` → `@kilocode/sdk` | Auto-generated TypeScript client for the server API |
| `packages/kilo-gateway` | Auth (device flow), OpenRouter routing, Kilo API integration |
| `packages/kilo-ui` | SolidJS component library (VS Code webview) |
| `packages/kilo-telemetry` | PostHog + OpenTelemetry |

Server routes are defined with Effect's `HttpApi` under
`packages/opencode/src/server/routes/instance/httpapi/`, which is what makes the OpenAPI
artifact at `packages/sdk/openapi.json` (271 paths, OpenAPI 3.1.0) possible.

### 2.3 The HTTP surface

`kilo serve --port 0` starts an HTTP + SSE server. **Two API generations are mounted
simultaneously** under the same host:port, and this is not documented anywhere:

| Generation | Prefix | Routing param | Event variants |
|---|---|---|---|
| v1 (unprefixed) | `/session`, `/event`, `/file`, `/config`, `/permission` | `?directory=` | 213 |
| v2 | `/api/session`, `/api/event`, `/api/fs/list` | `?location=` | 93 |

v1 is richer (it includes Kilo-only events for sandbox, memory, indexing, background
processes and the Agent Manager) and is what the editor clients and `kilo run` use.
There is **no version negotiation** on the wire — both are always live.

Endpoints ZUZZ would use (v1):

| Purpose | Method + path | operationId |
|---|---|---|
| Create session | `POST /session` | `session.create` |
| Send prompt (async) | `POST /session/{id}/prompt_async` | `session.prompt_async` |
| Send prompt (blocking) | `POST /session/{id}/message` | `session.prompt` |
| SSE event stream | `GET /event?directory=` | `event.subscribe` |
| Process-wide SSE | `GET /global/event` | `global.event` |
| List files | `GET /file?directory=&path=` | `file.list` |
| Read file | `GET /file/content?directory=&path=` | `file.read` |
| **Session diff** | `GET /session/{id}/diff` | `session.diff` |
| File/git status | `GET /file/status` | `file.status` |
| Transcript | `GET /session/{id}/message` | `session.messages` |
| Pending permissions | `GET /permission` | `permission.list` |
| Reply to permission | `POST /permission/{requestID}/reply` | `permission.reply` |
| Health | `GET /global/health` | `global.health` |
| Live OpenAPI | `GET /doc` | *(unofficial but real)* |

`session.diff` returns `SnapshotFileDiff[]` with `{file, patch, before, after, additions,
deletions, status: added|deleted|modified}` — which is exactly the shape ZUZZ's diff
preview page needs.

Auth is HTTP Basic, and becomes **required** when `KILO_SERVER_PASSWORD` is set. Three
paths always require auth regardless: `/permission/allow-everything`,
`/kilocode/snapshot/remove`, `/kilocode/worktree/teardown`.

### 2.4 Multi-file editing

Kilo does **not** have a bespoke multi-file editor. It has an `edit` tool (and `write`),
driven by an agent loop, plus a **snapshot** system: every session tracks repository state
so changes can be reverted. `config.snapshot` is a boolean — setting it `false` disables
snapshots entirely, and with them undo/revert. Diffs come from the snapshot layer, not
from a patch editor.

Consequence for ZUZZ: there is no "here is a multi-file patch, approve it" primitive to
call. Approval is per-tool-call, via `permission.asked` / `POST /permission/{id}/reply`,
and the diff must be *assembled afterwards* from `session.diff`.

### 2.5 Agent orchestration

Agents are **markdown files with YAML frontmatter** in `.kilo/agents/*.md` (project) or
`~/.config/kilo/agents/` (global). The filename without `.md` is the agent name. The
markdown body becomes the system prompt.

```markdown
---
description: Performs security audits and identifies vulnerabilities
mode: subagent
model: anthropic/claude-sonnet-4-20250514
temperature: 0.1
steps: 20
permission:
  "*": deny
  read: allow
  grep: allow
  edit: deny
  bash:
    "*": deny
    "git log*": allow
    "git status *": allow
---

You are a security expert. Focus on identifying potentially serious security issues.
```

Frontmatter supports `description`, `mode` (`subagent|primary|all`), `model`, `variant`,
`temperature`, `top_p`, `steps`, `permission`, `options`, `color`, `hidden`, `disable`.

**Agents can restrict their tools**, via a glob rule engine with last-match-wins:

```jsonc
"permission": {
  "*": "ask",
  "bash": { "*": "ask", "git *": "allow", "npm *": "allow", "rm *": "deny" },
  "edit":  { "*": "deny", "docs/*.mdx": "allow" }
}
```

This format is a genuinely good fit for ZUZZ, and it is the most portable thing Kilo has.

### 2.6 Sandbox — and why it does not help here

Kilo's sandbox is a **write** boundary, not a privacy boundary (an agent can still read
any file your account can read). Defaults: `enabled: false`, `network: "deny"`.

| Platform | Backend |
|---|---|
| macOS | `sandbox-exec` (Seatbelt) |
| Linux | Bubblewrap (`bwrap`), netns + seccomp |
| **Windows** | **None. Unsupported.** |

From Kilo's sandboxing docs: *"Sandboxing is not available on Windows. If an enabled macOS
or Linux sandbox policy cannot be enforced, Kilo reports the reason and refuses to run the
affected tool. It does not silently fall back to unrestricted execution."*

That is the crux: on Windows the sandbox does not degrade to "no sandbox" — it stops
restricted tools from running at all. Either way there is no runtime containment.

The sandbox is also not in the published config schema (`https://app.kilo.ai/config.json`
has 44 keys and `sandbox` is not one of them), so `$schema` + `"sandbox"` fails strict
editor validation.

### 2.7 License

**MIT, everywhere, with no exceptions.** Verified against the npm registry and the repo:

- Repo root `LICENSE` — MIT, 1,094 bytes, `Copyright (c) 2026 Kilo Code / Copyright (c) 2025 opencode`
- `@kilocode/cli` — `"license": "MIT"`, ships a byte-identical LICENSE
- `packages/kilo-vscode` (`kilo-code`) — `"license": "MIT"`, no separate terms
- `@kilocode/sdk` — MIT

The Anaconda acquisition is a corporate change, not a licence change.

⚠️ Name collision: the unscoped npm package **`kilocode`** (v1.2.0, ISC) is **not**
Kilo-Org's. Use the scoped `@kilocode/cli`.

### 2.8 Python ecosystem

**Nothing official.** For reference, ~80 community `opencode-*` packages exist on PyPI,
including `opencode-client-python`, `opencode-py` and `opencode-sdk`. Because Kilo is a
fork of OpenCode they *might* interoperate with the v1 surface, but none are affiliated
with Kilo-Org and none are tested against it. This is speculation, not a supported claim.

---

## 3. Integration approach — three options

### Option A — ZUZZ-native orchestration, Kilo-derived design *(recommended)*

Implement `body/mouth/kilo/` in Python. Inference goes through ZUZZ's **existing**
Cerebrum + Corpus Callosum (Groq LLaMA 70B, Ollama Qwen Coder 3B — the exact models the
task specifies). Adopt Kilo's *architecture* rather than its binary:

- agent definitions as `.md` + frontmatter with a `permission` glob block
- plan → diff → approve → apply workflow
- per-agent model / temperature / tool restrictions

Every file write goes through Body/Fingers **by construction**, because ZUZZ owns the
write path.

- **Dependencies added:** none. `httpx` and `pytest` are already pinned.
- **Safety:** every existing guarantee holds unchanged. Nothing new can bypass the gate,
  because nothing outside ZUZZ touches the disk.
- **Cost:** we do not reuse Kilo's implementation — only its design.
- **Risk:** low. This is the same pattern the Occipital Lobe and Body/Legs already follow.

### Option B — Kilo as a read-only reasoning backend

Run `kilo.exe` as a subprocess with `permission: {"edit": "deny", "bash": "deny"}`. Kilo
reads the workspace and returns text plus a diff proposal; ZUZZ applies it via
Body/Fingers.

- **Gains:** Kilo's production prompts, 500+ model routing, session management.
- **Costs:** a 65.6 MB binary plus a Bun runtime; the undocumented `/api` v2 surface;
  no wire version negotiation; telemetry **on by default** (an R2/R6 exposure unless
  `experimental.openTelemetry: false`); Node required unless the prebuilt `.exe` is used
  directly.
- **Loses:** the agent tool loop, which is most of what Kilo is.
- **Safety:** acceptable *only* with `edit` and `bash` denied. This is a configuration
  choice, not an enforced boundary — Kilo can be reconfigured, and ZUZZ would have to
  verify the config before every call to be sure.

### Option C — Kilo as the executor *(rejected)*

Let `kilo serve` run with write access and orchestrate file changes itself.

- **Rejected because** it bypasses the PermissionGate, the Amygdala, Body/Fingers
  entirely, the activity log, and the kill switch's practical reach — and because the
  only containment Kilo offers does not exist on Windows.
- It would also make ZUZZ's trust-level matrix, which the user can tune, meaningless for
  the most powerful capability in the system.

---

## 4. Recommendation

**Option A**, with Option B held as a possible later addition for *reasoning only*.

The deciding factor is that ZUZZ's safety architecture is not a layer ZUZZ can opt out of
— it is enforced by the `PermissionDecision` being the *signature* of every public Body
method, and by `PROJECT.md` §17 Rule 4. Option C would nullify both. Option B is viable
but buys a binary dependency and an undocumented HTTP surface for capabilities ZUZZ
mostly already has, since the task's target models (Groq 70B, Qwen Coder 3B) are already
reachable through Corpus Callosum.

Option A also resolves the tool-catalogue requirement cleanly: the ten `kilo_*` tools
become thin wrappers over `body/mouth/kilo/`, exactly as the 35 existing tools are
wrappers over `body/hands` and `body/fingers`.

---

## 5. Dependency list

### Option A (recommended)

**No new dependencies.** All already pinned in `requirements.txt`:

| Package | Version | Use |
|---|---|---|
| `httpx` | 0.27.2 | Cloud calls (Groq, HF, OpenRouter, NVIDIA) via Corpus Callosum |
| `pytest` | 8.3.4 | Tests |
| `pytest-asyncio` | 0.25.0 | Async tests |
| `pydantic` | 2.10.4 | Request/response models for `routes_code.py` |
| `fastapi` | 0.115.6 | API routes |

Stdlib only for the rest: `difflib` (diffs), `subprocess` (terminal, git), `ast`
(static review), `json`, `hashlib` (digests), `pathlib`.

### Option B (adds these)

| Item | Version | Notes |
|---|---|---|
| `@kilocode/cli` | 7.8.3 | MIT. Install `kilo-windows-x64.zip` (65.6 MB) and invoke `kilo.exe` **directly** — the npm `kilo` shim is a Node script and requires Node at runtime |
| Node.js | ≥16 (inferred, not documented) | Only if the npm shim is used. Avoidable |
| `sseclient` or `aiohttp` | — | SSE parsing; 213 event variants in v1, discriminated by a `type` string |

Windows note: Jayanthan's CPU is an AMD Ryzen 5 7235HS, which has AVX2, so the standard
x64 binary is correct. The `-baseline` variant is only for pre-AVX CPUs, which crash
with "Illegal instruction".

---

## 6. Proposed `body/mouth/kilo/` structure

Under ZUZZ's 200-line-per-file rule, with the package docstring carrying the invariant
list in the style of `body/legs/__init__.py`.

```
body/mouth/kilo/
├── __init__.py          # Package docstring: invariant list + exports
├── exceptions.py        # KiloError hierarchy
├── kilo_config.py       # Model selection, agent defaults, limits
├── kilo_agents.py       # The 5 agent definitions
├── kilo_engine.py       # Orchestrator: plan -> generate -> review -> diff
├── kilo_workspace.py    # Project workspace scoping + path guard
├── kilo_diff.py         # Diff preview, unified + side-by-side, apply
├── kilo_git.py          # Git ops behind the gate (force-push blocked)
├── kilo_terminal.py     # Terminal exec with output parsing
└── kilo_review.py       # Static checks + review findings
```

Plus, outside the package: `body/mouth/code_actions.py` (the category mapping and
proposal builders, mirroring `body/legs/browser_actions.py`).

### Module responsibilities

| Module | Responsibility |
|---|---|
| `exceptions.py` | Typed refusals, each mapping to an HTTP status |
| `kilo_config.py` | Which model per agent, step budgets, output caps, the ZUZZ-guard enable flag |
| `kilo_agents.py` | The 5 agents: prompt, model, temperature, tool permissions |
| `kilo_engine.py` | The pipeline. Owns no I/O of its own — it calls the others |
| `kilo_workspace.py` | Resolves a target to a workspace; **refuses ZUZZ core paths** |
| `kilo_diff.py` | Builds `difflib` output; applies through Body/Fingers only |
| `kilo_git.py` | status/diff/log via `subprocess`, no shell, force-push refused |
| `kilo_terminal.py` | Runs commands, parses pytest output into structured results |
| `kilo_review.py` | AST-based checks (unused imports, bare excepts, `eval`) + model review |

---

## 7. Agent definitions

Per the task. Model assignments match the models ZUZZ already has configured.

| Agent | Model | Responsibility | Tool policy |
|---|---|---|---|
| **Architect** | Groq LLaMA 70B | Folder layout, tech stack, dependencies, module interfaces | **No writes.** Emits a design document only |
| **Coder** | Groq coding model, falling back to Qwen Coder 3B | Implements against the Architect's design | Writes proposed through `kilo_diff`, never directly |
| **Reviewer** | Groq LLaMA 70B | Bugs, quality, security, unused code | **No writes.** Emits findings with `file:line` |
| **Debugger** | Groq LLaMA 70B | Root-cause analysis from an error + code | Proposes a patch; never applies it |
| **Tester** | Qwen Coder 3B | Writes pytest tests, runs them, reports | Proposes test files; execution through `kilo_terminal` |

Three of five agents cannot write at all, and the two that propose writes produce a diff
that a human approves. This is the structural reason the design is safe rather than
merely gated.

Coordination (`kilo_engine.py`): Architect → Coder → (Reviewer ⇄ Coder, max 2 loops) →
Tester, with Debugger invoked on test failure. Sequential, not parallel: the agents
depend on each other's output, and ZUZZ's 3 GB RAM budget does not allow concurrent local
model loads.

---

## 8. Safety integration points

### 8.1 New action categories

Five rows in `config/trust_levels.json` and `ActionCategory`. **A category with no matrix
row is refused by the gate** — the safe direction to fail — so the rows must exist before
the tools do.

| Category | Proposal | Rationale |
|---|---|---|
| `generate_code` | ask 1–2, auto from 3 | Produces text. Exposure is the model's reasoning leaving the machine |
| `execute_code` | ask 1–4, auto at 5 | Runs code. Mirrors `run_python`, which auto-approves only at 5 |
| `modify_project` | ask 1–2, auto from 3 | Writes into a workspace, never ZUZZ's own tree |
| `git_operations` | ask 1–4, auto at 5 | Mutating git can rewrite history |
| `install_packages` | **ask at every level** | Same shape as `download_files` and `install_software` |

`install_packages` asking at all five levels matches immutable rule **R7** ("Never install
software without user approval") and the existing `install_software` row. Trust level 5
is autonomy over routine work, not permission to install unattended.

### 8.2 ZUZZ core is read-only to Kilo

`kilo_workspace.py` refuses any target under `brain/`, `body/`, `face/`, `core/`,
`config/`, and specifically `prefrontal_cortex/immutable_rules.py`,
`spinal_cord/kill_switch.py`, `brainstem/activity_logger.py`. This is R3 enforced
structurally — the path is never offered to the model, so it cannot be requested.

Note this must be a **refusal, not a clamp**, following the Occipital Lobe's precedent: a
silently relocated write is a write somewhere the user did not approve.

### 8.3 Write path

All writes: PermissionGate → Amygdala score → Body/Fingers (which snapshots `.bak.1`,
routes deletes to trash, resolves symlinks, blocks `.env`). `PermissionDecision` is the
first parameter of every public method, so the gate cannot be skipped by a caller that
forgot it.

### 8.4 Git

Force-push, `reset --hard`, `clean -fd`, `push --mirror`, and history rewriting are
refused at every trust level. Read-only git (`status`, `diff`, `log`) maps to
`read_files` semantics and auto-approves from level 2.

### 8.5 Execution

`kilo_terminal` runs with a timeout, captures stdout/stderr separately, never uses
`shell=True`, and passes an argument list. The amygdala scores the command string before
it runs, so `rm -rf` is caught as a pattern rather than as an argument vector.

### 8.6 Telemetry (Option B only)

`experimental.openTelemetry: false` is **on by default**. Must be set explicitly, since
Kilo ships telemetry enabled and R2/R6 apply to data leaving the machine.

---

## 9. Tool catalogue additions

Ten tools in `brain/cerebrum/tools_schema.py`, each bound to a real method in
`body/mouth/kilo/` — and per that module's docstring, a name with no implementation is a
test failure rather than a silent no-op.

| Tool | Args | Action category |
|---|---|---|
| `kilo_generate_code` | `description`, `workspace` | `generate_code` |
| `kilo_review_code` | `path` | `read_files` |
| `kilo_debug_error` | `error`, `code` | `generate_code` |
| `kilo_refactor_code` | `path`, `goal` | `generate_code` |
| `kilo_run_tests` | `path` | `execute_code` |
| `kilo_preview_diff` | `changes` | none — a gauge, reports ZUZZ's own pending state |
| `kilo_apply_changes` | `diff_id`, `approval` | `modify_project` |
| `kilo_rollback` | `diff_id` | `modify_project` |
| `kilo_git_status` | `path` | none — a gauge |
| `kilo_install_package` | `name` | `install_packages` |

`kilo_preview_diff` and `kilo_git_status` are deliberately **not** gated, following the
`body/legs/__init__.py` precedent: a gauge behind a confirmation is a gauge nobody looked
at. `kilo_apply_changes` is the gated one, and it is the only tool that writes.

---

## 10. API routes

`face/api/routes_code.py`, following the `routes_browser.py` pattern (thin assembler, or
a single cohesive router if it stays under 200 lines):

| Method | Path |
|---|---|
| POST | `/api/code/generate` |
| POST | `/api/code/review` |
| POST | `/api/code/debug` |
| POST | `/api/code/refactor` |
| POST | `/api/code/test` |
| POST | `/api/code/diff` |
| POST | `/api/code/apply` |
| POST | `/api/code/rollback` |
| GET | `/api/code/workspace` |
| POST | `/api/code/run` |
| GET | `/api/code/agents-status` |

## 10.1 UI

`face/frontend/pages/code.html`, `static/js/code.js`, `static/css/code.css`:
description input; agent activity log; per-file diff with Apply/Reject; terminal output
panel; git status panel.

**On the editor:** the task suggests CodeMirror or Monaco. Recommendation is **neither** —
both are large CDN dependencies for a page whose primary content is read-only diffs. The
existing ZUZZ pages are plain HTML/CSS/JS, and the only editing needed is "paste a
description". A syntax-highlighted, read-only diff rendered server-side matches the
existing stack and adds no dependency. Flagged as question 3.

---

## 11. Estimated size

| Item | Files | LOC |
|---|---|---|
| `body/mouth/kilo/` | 10 | ~1,450 |
| `body/mouth/code_actions.py` | 1 | ~180 |
| `face/api/routes_code.py` | 1 | ~190 |
| Frontend (`code.html` / `code.js` / `code.css`) | 3 | ~700 |
| `tests/test_kilo_integration.py` | 1 | ~450 |
| Trust matrix + `ActionCategory` rows | 2 | ~60 |
| Tool schemas | 1 | ~120 |
| **Total** | **19** | **~3,150** |

Option B adds ~700 LOC for the subprocess manager, HTTP client, SSE parser and config
writer, plus the binary dependency.

## 12. Timeline

| Step | Estimate |
|---|---|
| Guard (`kilo_workspace`) + exceptions + trust rows | 0.5 day |
| `kilo_config` + `kilo_agents` + prompt work | 1 day |
| `kilo_diff` + apply/rollback through Body/Fingers | 1 day |
| `kilo_review` + `kilo_terminal` + `kilo_git` | 1 day |
| `kilo_engine` orchestration | 1 day |
| `code_actions.py` + 10 tool schemas | 0.5 day |
| `routes_code.py` | 0.5 day |
| Frontend | 1 day |
| Tests (15 specified) | 1 day |
| **Total** | **~7.5 days** |

Plus the live demo. Note this assumes Groq is reachable and the model behaves; agent
loop quality is the main schedule risk, and it is why the plan puts the Architect and
Coder first.

---

## 13. Five open questions for Jayanthan

**1. Given that letting Kilo write files bypasses the gate — do you still want the Kilo binary at all?**
The research says Option A (ZUZZ-native, Kilo's design) gives the same five agents with
every safety layer intact and no new dependency. Option B adds a 65.6 MB binary and an
undocumented HTTP surface to reuse Kilo's prompts. Recommendation: **Option A**, revisit
Option B later if the prompts prove worth the dependency. This is the decision the rest of
the plan hangs on.

**2. Do you accept that `install_packages` asks for confirmation at all five trust levels?**
This follows immutable rule R7 and matches the existing `install_software` and
`download_files` rows. It costs one click per install, including at level 5. The
alternative — auto-approving at level 5 — would make R7 unenforceable at the level where
an unattended agent is most plausible. Recommendation: **ask at every level.**

**3. Plain read-only diffs, or a real in-browser editor (CodeMirror/Monaco)?**
A real editor is a large dependency for a page whose content is mostly diffs, and it
diverges from the existing frontend stack. Recommendation: **no editor dependency**;
server-rendered highlighted diffs.

**4. Should ZUZZ be able to modify its own non-core code?**
The task says Kilo must not touch `brain/`, `body/`, `face/` or `core/`. That is stricter
than `PROJECT.md` §13, which permits modifying own plugins and non-core utility code.
Tighter is safer and easier to audit, but it removes self-improvement — a stated project
goal — from the Kilo path. Recommendation: **keep the strict block**; revisit
self-modification as a separate, separately-gated capability.

**5. Groq 70B for all five agents, or the cheaper mix the task specifies?**
The task assigns Qwen Coder 3B to Coder and Tester. Qwen Coder 3B is ~2 GB VRAM against a
6 GB budget with a 4.5 GB ceiling, so local use competes with Moondream. Recommendation:
**Groq 70B for all five initially** (free, no VRAM cost), with Qwen Coder 3B as an
opt-in fallback if Groq rate-limits at 30 req/min during agent loops. Agent pipelines
make many calls per task and may well hit that ceiling.

---

## 14. Sources

Primary: `Kilo-Org/kilocode` repo and its `AGENTS.md` files; `packages/sdk/openapi.json`
(v7.8.3, 271 paths); `https://app.kilo.ai/config.json`; npm registry metadata for
`@kilocode/cli`, `@kilocode/sdk`, `kilo-code`; repo `LICENSE`.

Documentation: `kilo.ai/docs` — CLI, CLI Reference, CLI Runtime Architecture, Sandboxing,
Custom Models, Custom Subagents; `deepwiki.com/Kilo-Org/kilocode` (AI-generated over
source, treated as secondary).

Verified against the complete PyPI simple index (905,515 packages) for section 2.8.

Items that are **not documented** and were read from source are marked as such
throughout: the v1/v2 API split, `GET /doc`, the `location` vs `directory` parameter
difference, the `--format json` event vocabulary, and the Windows sandbox refusal.