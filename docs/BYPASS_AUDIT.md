# Bypass Audit

Every path that can make ZUZZ infer, act, or change its own safety settings, and
what stands in front of it.

Written after the Session 8 lesson: the safety chain was verified component by
component and still had three entry points that went around it. Each of those
three had the same shape — *the guard existed on the function someone happened to
be looking at*. So the question this document answers is not "is each component
correct" but "what is the complete set of doors, and is every one of them
guarded".

## The chain

```
HTTP / WebSocket / scheduler / script
        │
        ▼
  Thalamus ──── Amygdala (score only, can never allow)
        │
        ├─ kill switch ──── first, before anything else
        ├─ PermissionGate ── immutable rules → trust matrix → target
        ├─ Hypothalamus ──── VRAM, thermal, power (local levels only)
        │
        ▼
  Cerebrum ──── re-checks the switch and the gate at its own entry
        │
        ▼
  Corpus Callosum / Ollama
        │
        ▼
  Body (Hands, Fingers) ──── re-verifies the decision at the boundary
```

Two properties make the chain more than a diagram:

- **The Amygdala can only subtract.** A `forbidden` assessment blocks, a
  `dangerous` one forces confirmation. Nothing it says can turn a blocked action
  into an allowed one.
- **The Cerebrum re-checks.** The Router is not the only way to reach inference,
  so `think_local`, `think_cloud` and `stream` each call `_check_may_infer`
  themselves. A caller holding a Cerebrum cannot skip the stop.

## Group 1 — Inference entry points

### 1.1 The lowest layer — raw HTTP to a model

| Entry point | Gate | Switch | Hypothalamus | Status |
|---|---|---|---|---|
| `reasoning.py:51` `stream_local` | — | — | — | internal only |
| `backends.py:40` `call_local` | — | — | — | internal only |
| `backends.py:73` `call_litellm` | — | — | — | internal only |
| `connector_transport.py:52` `chat` | — | — | — | internal only |
| `connector_transport.py:149` `stream_chat` | — | — | — | internal only |
| `reasoning.py:25` `list_local_models` | — | — | — | read-only |
| `routes_chat.py:59` `internet_available` | — | — | — | GET to groq.com |

These are unguarded by design and safe for one reason: every one of them is
called only from a guarded function above it. `call_local` and `call_litellm` are
called from `think_local`/`think_cloud`; `stream_local` from `stream`; the
connector methods from `_stream_cloud`. No route, scheduler job or script reaches
any of them directly.

That argument is only as good as its weakest link, which is why Group 3 pins it:
a test asserts every inference entry point refuses while the switch is engaged.

### 1.2 The guarded layer — the Cerebrum

| Entry point | Switch | Gate | Hypothalamus | Test |
|---|---|---|---|---|
| `cerebrum.py:160` `think_local` | yes | yes | **no** | `test_gap_closure.py` |
| `cerebrum.py:193` `think_cloud` | yes | yes | **no** | `test_gap_closure.py` |
| `cerebrum.py:215` `think` | yes | yes | **no** | delegates to the two above |
| `streaming.py:53` `stream` | yes | yes | **no** | `test_gap_closure.py` |

**No Hypothalamus at this layer, deliberately.** The Cerebrum answers "is the
machine allowed to speak?", which is the kill switch and the immutable rules.
Whether the machine can *afford* the answer is the Router's question, and the
Cerebrum has no budget to consult. Putting a VRAM check here would mean a
resource refusal raised as an inference failure, which would report a healthy
machine as broken.

The cost of that choice is the one thing that had to be closed by hand: a caller
holding a Cerebrum and a level can reach local inference having consulted only
the switch. That was **Gap B**, named out of Session 8 and closed here — and the
Cerebrum is reachable from routes, the Router and future code, so "the Router
always goes first" was not a safe assumption.

### 1.3 The callers

| Entry point | Switch | Gate | Hypothalamus | Status |
|---|---|---|---|---|
| `router.py:81` `route` | yes | yes | yes | every rung checked |
| `router.py:177` `route_stream` | yes | yes | yes | checked before opening the stream |
| `routes_chat.py:83` `chat` (auto) | yes | yes | yes | via `route` |
| `routes_chat.py:83` `chat` (**forced level**) | yes | yes | yes | **closed this session** |
| `routes_chat.py:141` `/ws` websocket | yes | yes | yes | via `route_stream` |

The forced-level branch is the fourth instance of the Session 8 pattern. It calls
`cerebrum.think` directly rather than walking the ladder, so it skipped the
ladder's per-rung resource check — and `level: 1` in a request body was a
complete bypass of the VRAM budget and the thermal guardian. It now asks
`Router.check_level_allowed` and **refuses** (409) rather than rerouting, because
the caller named a level and silently answering from a cloud provider would
answer a different request and would send the text off the machine without saying
so.

## Group 2 — Side-effecting entry points (the host)

### 2.1 Fingers and Hands

Every method takes a `PermissionDecision` as its first argument and re-verifies
it at the boundary — `fingers/guards.py:54` and `hands/hands_guards.py:45` both
re-check type, dry-run, kill switch, allow, and category/target replay. The
decision is therefore not a token that can be forged by holding it longer; it is
re-checked at the last moment before the disk or the mouse.

Two methods reach the physical boundary directly, by design:

| Entry point | Why it is safe |
|---|---|
| `actuator.py:71` `PyAutoGUIActuator` | The pyautogui call itself. Reachable only from verified code above it. |
| `app_manager.py:207` `launch` | `subprocess.Popen(shell=False)`. Reached only via `open_app`, which verifies at `:112`. |
| `keyboard.py:215` `release_all` | Releasing keys is the *recovery* direction. Refusing it would leave a key stuck down. |
| `clipboard.py:169` `_read_text` | Reads before deciding, because the content is what determines the category. Nothing leaves the process. |
| `app_manager.py:111` `resolve_target` | Resolves a friendly app name to an executable before deciding. Reads only. |

### 2.2 Routes

| Route | Switch | Gate | Status |
|---|---|---|---|
| `routes_files.py` — all 14 file operations | yes (`:298`) | yes (`:312`) | via `_run` |
| `routes_hands.py` — all 18 input operations | yes (`:289`) | yes (`:293`) | via `_run` |
| `routes_hands.py` — 4 confirmation answers | yes (`:381`) | yes (`:386`) | re-decided |
| `routes_resources.py` — load/unload/swap | yes | yes (`:74`) | via `_mutate` |
| `routes_assess.py` — `/api/safety/assess` | yes | yes | reporting only |
| `routes_safety.py:131` — `/api/safety/check` | yes (`:131`) | yes | dry run |
| **`routes_kill_switch.py:148` — `/api/kill-switch/watchdog`** | — | — | **closed this session** |
| `routes_safety.py:76` — `/api/safety/trust` | — | — | documented below |
| `routes_settings.py:52` — `POST /api/settings` | — | — | documented below |
| `routes_resources.py:259` — `POST /api/power/set-mode` | — | — | documented below |

Read-only routes carry no gate by design and are listed as such: the trash
listing, the backup listing, the layout and policy descriptions, every gauge
endpoint, the brain status block, and the plugin listings.

### 2.3 The watchdog endpoint — closed this session

`POST /api/kill-switch/watchdog` could set `cpu_percent`, `cpu_streak_limit` and
`deletion_limit` with **no authentication, no gate and no kill-switch check**.

Every one of those is an upper bound the watchdog trips at —
`deletions >= self.deletion_limit` — so raising it means the signal fires later
or never. The audit's phrasing is the honest one: this made the mass-deletion
scenario, the reason the watchdog exists, defeatable with one request. The
endpoint is also unauthenticated, and ZUZZ has no auth anywhere, so "attacker"
means "anything that can reach localhost" — including a browser page.

The fix is asymmetric on purpose:

- **Tightening needs no permission.** A lower limit fires sooner and cannot
  reduce safety, so an operator reacting to something that looks like runaway
  work is not made to raise a prompt.
- **Loosening is `disable_safety`**, which the trust matrix blocks at all five
  levels. There is no trust level on which the watchdog can be turned off from
  here.
- **Zero is refused** (400). `deletions >= 0` is true on every poll, so zero looks
  like the strictest setting and is a self-inflicted denial of service.

Only three of the seven limits are tunable; the click, keystroke, app-open and
blocked-hotkey limits are fixed in code and unreachable from any route.

### 2.4 Documented, and why each remains

| Entry point | Why it stays ungated |
|---|---|
| `model_manager.py` protective unloads (`:457`, `:527`, `:666`, `:732`) | Relieving memory pressure or heat must not wait on a dialog — the dialog would appear at the moment the machine can least afford one. All four are audited, and none can touch a core model. This is the widest ungated side-effect surface in the codebase and it is intentional. |
| `trash_manager.py:238` `auto_purge_old` | Only purges items already in the trash older than 7 days, plus a disk-pressure path. Never touches anything outside `.zuzz_trash/`. |
| `routes_kill_switch.py:76` `POST /stop` | Triggering needs no confirmation. The whole point of a stop is that it works while something is going wrong. |
| `routes_kill_switch.py:110` `POST /reset` | Protected by `check_confirmation` (typed word) and `check_cooldown`, both in `reset_policy.py`. Single-factor by design and noted in the module docstring. |
| `routes_safety.py:76` `POST /api/safety/trust` | Protected by `TrustManager.request_change`, which requires escalating confirmation to raise and a cooldown to lower. Not gated by `PermissionGate` because raising your own permissions *is* the action — a gate consulted at the current level would approve it. |
| `routes_settings.py:52` `POST /api/settings` | Rejects any key outside `EDITABLE_KEYS`; `trust_level` is not among them. In-memory only, no disk write. `host`/`port` are editable but the server binds once at startup, so a change does not take effect until restart. |
| `routes_resources.py:259` `POST /api/power/set-mode` | Purely in-process state (`self._mode = mode`). Never touches an OS power plan. |
| `routes_hands.py:488` `release-keys` | Recovery, like `release_all`. |
| `api_vault.py` | Live code, writes `storage/vault.json` and `storage/.vault_key` with no gate. **Not wired to any route.** Flagged for Phase 7: any route exposing it must be gated, and the key file must stay gitignored. |

## Group 3 — Non-HTTP surfaces

| Surface | Gate | Notes |
|---|---|---|
| `/ws` chat websocket | yes | via `route_stream` |
| `/api/resources/live` websocket | no | Read-only sensor stream. Nothing it touches changes machine state. |
| `server.py:72` heartbeat, 30 s | no | GET to Ollama's model list. |
| `server.py:73` model reaper, 15 s | no | Protective unloads, audited. |
| `server.py:81` `hypothalamus.tick`, 1 s | no | **Is** the resource layer. Its whole job is to act without asking. |
| `server.py:84` trash purge, 24 h | no | As above. |
| `hypothalamus.py:398` `poll_loop` | — | Dead code. The scheduler job is the live path. |
| `scheduler.py:109` `add_job` | — | No route can register a job; only `server.py` calls it. |
| `stop_scope.py:80` `register_process` | — | **Never called.** PIDs for termination are never registered, so `terminate_all()` currently has nothing to terminate. Flagged, not fixed. |
| `multi_agent/` | — | Three stub files, no code. |
| `body/mouth/`, `body/legs/`, `body/tools/` | — | Stub files, no code. **There is no shell execution in ZUZZ.** |
| `scripts/test_colab_live.py:21` | no | **Module-level `requests.post` to a hardcoded live URL, executed on import.** Not reachable from the server; should not be importable by accident. |

## Group 4 — Plugins

`plugins/__init__.py` is one docstring line. `plugin_registry.json` is empty.
There is **no loading path**: no `importlib`, `exec`, `eval`, `__import__` or
`spec_from_file_location` anywhere in `plugins/`, `face/` or `brain/`. Nothing
imports `plugins`. `INSTALLED_DIR.glob("*.py")` names files and never imports
them.

So a plugin cannot currently reach anything ungated, because a plugin cannot run.
The risk is latent, and Phase 7 is where it becomes real. Three invariants must
hold then, or a plugin becomes an ungated host actor:

1. A plugin receives a `PermissionGate` and `KillSwitch` handle, never a raw
   `Hands` or `FileManager`.
2. A plugin cannot reach `Actuator` or `ProcessManager.launch`.
3. The decision-free method lists pinned by
   `test_public_methods_all_require_a_decision` (`body/hands/hands.py`) and
   `test_system_methods_are_exactly_the_documented_system_methods`
   (`body/fingers/__init__.py`) are extended deliberately, never by accident.

The gate a plugin would have to use already exists in the right place:
`Cerebrum._check_may_infer` was written for exactly this case.

## The client bug

| File | Bug | Status |
|---|---|---|
| `hands.js:267` | Polled `GET /api/kill-switch/status` — no such route. 404, so the stop state never displayed. | **fixed** |
| `hands.js:403` | Posted `POST /api/kill-switch/trigger` — no such route. **The Emergency Stop button did nothing.** | **fixed** |

The real routes are `GET /api/kill-switch` and `POST /api/kill-switch/stop`.
`test_the_routes_the_frontend_calls_actually_exist` now parses every frontend
script and fails on any kill-switch URL with no matching route, so a third such
mismatch cannot appear unnoticed.

## Standing rule

A guard belongs on the **boundary**, not on the function someone was looking at.

Three of the six defects found across Sessions 8 and 9 were the same mistake: the
check existed on one entry point and not its siblings. `think_local` was guarded
while `think_cloud` and `stream` were not. The ladder's first rung was checked
while its retries were not. The auto-routed chat path was checked while the
forced-level path was not.

The tests that follow from that rule are the ones that assert a sibling behaves
identically, rather than the ones that assert a function was called:

| Test | Guards against |
|---|---|
| `test_inference_is_gated_for_every_cerebrum_entry_point` | a fourth inference path appearing ungated |
| `test_a_cloud_level_is_never_refused_for_vram` | the resource check creeping onto cloud levels |
| `test_a_forced_local_level_is_refused_when_the_card_is_full` | a route reaching local inference around the ladder |
| `test_the_watchdog_cannot_be_loosened_at_full_trust` | a safety threshold becoming adjustable at some trust level |
| `test_the_routes_the_frontend_calls_actually_exist` | a UI control silently addressing nothing |
| `test_disable_safety_is_blocked_at_every_trust_level` | the config row the watchdog fix depends on being relaxed |

## Test coverage of this audit

`tests/test_bypass_audit.py` — 16 tests, all of which assert that something *did
not happen*: a threshold unchanged, a local inference skipped, a request refused.
A test that proves a guard ran is not a test that proves the guard stopped
something.
