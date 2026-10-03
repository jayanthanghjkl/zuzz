# Phase 2 Plan — Computer Control + Safety Core

**Status: APPROVED by Jayanthan on 2026-10-02. Building.**

All five open questions were answered; the decisions are recorded in section 12
and folded into the design below. Build order and testing strategy are approved
unchanged. Section 6 adds the requested dry-run mode.

Scope from PROJECT.md section 15: computer control, safety core, kill switch.

---

## 1. Scope

| Component | Purpose | Folder | Est. LOC |
|---|---|---|---|
| Prefrontal Cortex | Permission gate, trust levels, immutable rules | `brain/prefrontal_cortex/` | ~600 |
| Amygdala | Threat detection, risk scoring | `brain/amygdala/` | ~400 |
| Spinal Cord | Kill switch, watchdog (separate process) | `brain/spinal_cord/` | ~400 |
| Body/Hands | Mouse, keyboard, clipboard, apps | `body/hands/` | ~600 |
| Body/Fingers | File and directory operations | `body/fingers/` | ~500 |
| Hypothalamus | RAM/VRAM/CPU, model load/unload | `brain/hypothalamus/` | ~350 |
| Safety tests | Tests for all of the above | `tests/test_safety.py` | ~500 |
| | | **Total** | **~3350** |

Excluded from Phase 2: browser automation, vision, voice, memory.

---

## 2. Architectural Invariants

These come from PROJECT.md section 17 and must hold in every action path:

1. **The Prefrontal Cortex checks every action before execution.** No action
   reaches the Body layer without a permission decision.
2. **The Amygdala scores every action before permission is asked.** A threat
   score can only *raise* scrutiny, never lower it.
3. **The Spinal Cord runs in a separate process.** A crash or hang in the brain
   must not disable the kill switch.
4. **The activity log records every action**, including every denial. Denials
   are the most interesting records.
5. **`immutable_rules.py` is read-only** — write it once, never modify it, and
   verify its hash at startup.
6. **The AI cannot change its own trust level.** Only a user action through the
   UI changes it.
7. **The default trust level is 1** — ask for everything.

### The one path every action takes

```
Thalamus → Amygdala (threat score)
         → Prefrontal Cortex (permission decision)
         → [if needed] user confirmation
         → Body layer (hands/fingers execute)
         → Activity log (always, allowed or denied)
```

A component that bypasses this chain is a bug, and there should be a test
asserting no Body method is reachable without a decision object.

---

## 3. Prefrontal Cortex — Permission Design

### Interfaces

```python
class PermissionDecision:
    allowed: bool          # may this proceed?
    requires_confirmation: bool
    reason: str
    trust_level: int
    rule_id: str | None    # which immutable rule fired, if any

class PermissionGate:
    def classify(action: Action) -> ActionCategory
    async def check(action: Action, trust_level: int) -> PermissionDecision
```

### Action categories (from PROJECT.md section 10)

`chat`, `read_files`, `open_apps`, `mouse_keyboard`, `run_safe_commands`,
`write_files`, `browse`, `run_python`, `modify_plugins`, `delete_files`,
`install_software`, `send_messages`, `run_admin`, `access_credentials`,
`delete_os_files`, `share_data`, `disable_safety`.

### Trust level matrix

Machine-readable source of truth: `config/trust_levels.json`. The gate reads it
so the UI and the gate cannot disagree.

| Action | L1 | L2 | L3 | L4 | L5 |
|---|---|---|---|---|---|
| Chat / answer | auto | auto | auto | auto | auto |
| Read files | ask | auto | auto | auto | auto |
| Open apps | ask | auto | auto | auto | auto |
| Mouse / keyboard | ask | ask | auto | auto | auto |
| Run safe commands | ask | ask | auto | auto | auto |
| Write new files | ask | auto | auto | auto | auto |
| Browse internet | ask | ask | ask | auto | auto |
| Run Python code | ask | ask | ask | ask | auto |
| Modify own plugins | ask | ask | ask | ask | auto |
| Delete user files | ask | ask | ask | ask | ask |
| Install software | ask | ask | ask | ask | ask |
| Send emails / messages | ask | ask | ask | ask | ask |
| Run as admin | ask | ask | ask | ask | ask |
| Access passwords | ask | ask | ask | ask | ask |
| Delete OS files | **blocked** | **blocked** | **blocked** | **blocked** | **blocked** |
| Share data externally | **blocked** | **blocked** | **blocked** | **blocked** | **blocked** |
| Disable safety | **blocked** | **blocked** | **blocked** | **blocked** | **blocked** |

Two refinements approved on 2026-10-02:

1. **File deletion always asks**, at every trust level including L5. Deletion is
   irreversible, the cost of asking is one popup, and the cost of a wrong
   deletion is hours of work.
2. **Temp files are the only exception.** Files under the project's `temp/`
   directory or the system temp directory may auto-delete from L3 upward.
   Anything classified `caution` (Desktop, Documents, Downloads) still asks.

Delete therefore resolves in three steps, not two:

```
protected path?          -> blocked (immutable rule, every level)
temp path and L3+?       -> auto
otherwise                -> ask at every level
```

### Immutable rules (approved)

1. Never delete operating system or system files.
2. Never send user data to unauthorised external parties.
3. Never disable safety checks or modify safety code.
4. Never run destructive commands without explicit user confirmation.
5. Never access passwords or credentials without confirmation.
6. Log ALL actions to the unmodifiable activity log.
7. Never install software without user approval.
8. The kill switch (Ctrl+Shift+Q) is always active and cannot be disabled.
9. **The AI cannot change the trust level.** Only a human acting through the UI
   can, and that action must itself be confirmed (section 5).

`immutable_rules.py` holds these as data, is chmod-read-only, and its SHA-256 is
checked at startup. A mismatch fails the boot rather than running unguarded.

---

## 4. Amygdala — Threat Design

```python
class ThreatAssessment:
    score: int            # 0-10
    level: str            # safe | caution | dangerous | forbidden
    matched_patterns: list[str]
    explanation: str

class ThreatDetector:
    async def assess(action: Action) -> ThreatAssessment
    async def score_command(command: str) -> ThreatAssessment
```

Scoring, starting from 0:

| Signal | Points |
|---|---|
| Recursive delete (`rm -rf`, `del /s /q`, `rd /s /q`) | +5 |
| System path referenced (`C:\Windows`, `C:\Program Files`) | +4 |
| Disk or partition command (`diskpart`, `format`, `cipher /w`) | +6 |
| Registry modification | +5 |
| Shutdown / reboot | +3 |
| Credential access (`password`, `api key`, `.env`, ssh keys) | +4 |
| Force flag (`-f`, `--force`, `/f`) | +2 |
| Piped to shell (`curl ... \| sh`) | +5 |
| Writes outside the project directory | +2 |

Levels: 0-2 safe, 3-5 caution, 6-8 dangerous, 9-10 forbidden.

**A `forbidden` score is blocked regardless of trust level.** The Amygdala can
veto; the trust level can only grant.

Patterns live in `config/danger_patterns.json`, extended in Phase 2 with the
regex forms above. Score contributions are in code, patterns in config — so new
patterns need no code change.

---

## 5. Spinal Cord — Kill Switch

### Design

Three separate pieces, deliberately:

1. **`emergency_stop.py`** — a standalone process started before the brain.
   Listens for `Ctrl+Shift+Q` via `keyboard`, then writes a stop flag and
   terminates the brain's PID.
2. **`kill_switch.py`** — in-process state. The Amygdala and Prefrontal Cortex
   read `is_killed()` on every action; while set, every action is denied without
   further checks.
3. **`watchdog.py`** — polls for runaway processes, infinite loops and mass file
   deletion, and trips the same switch.

```
[emergency_stop.py]  --Ctrl+Shift+Q-->  stop.flag + terminate PID
        |
        v
[brain process] reads stop.flag --> kill_switch.is_killed() = True
        --> every action denied, front end shows "stopped"
```

`Ctrl+Shift+Q` must work when the brain is frozen — hence a separate process.
The front end also needs a Stop button, because a global hotkey is easy to
forget while testing.

### Scope of the stop (approved)

The switch is an emergency stop, so it stops everything local — not just the
action on screen:

| Target | Behaviour |
|---|---|
| Local interactive actions | Stopped immediately |
| Local scheduled jobs | Stopped — a runaway job may be the cause |
| Local background processes | Stopped |
| Cloud agents (Colab) | **Cannot be stopped remotely.** Flagged instead |
| Kill switch reset | Manual UI action only, never automatic |

When the switch trips, every cloud provider is marked `kill switch active` in
`/api/providers` and on the dashboard. When Colab syncs back it reads the flag
and pauses, so a cloud agent cannot continue working on a task the user just
aborted. ZUZZ never auto-resets: an agent allowed to un-kill itself could
recover from a runaway loop.

---

## 6. Body Interfaces

The Body layer executes only. It never decides whether it may act — it receives
a `PermissionDecision` and refuses to run without one.

```python
class Mouse:
    def click(x, y, button="left", clicks=1)
    def move(x, y)
    def drag(x1, y1, x2, y2, duration=0.5)
    def scroll(amount, x=None, y=None)

class Keyboard:
    def type_text(text, interval=0.01)
    def press(key)
    def hotkey(*keys)
    def screenshot_region(x, y, w, h)
```

`failsafe = True` on PyAutoGUI: moving the mouse to a screen corner aborts the
action. This is the manual override for a bad coordinate.

### File operations

`file_manager.py` exposes `read`, `write`, `copy`, `move`, `rename`, `delete`,
`search`, `mkdir`. Every path goes through `file_classifier.py` first:

| Classification | Examples | Handling |
|---|---|---|
| Safe | project dir, temp | Normal |
| Caution | Desktop, Documents | Confirm before write/delete |
| Protected | `C:\Windows`, `C:\Program Files`, `.env`, ssh keys | Block unless the user explicitly unprotects |

### Hypothalamus

`resource_monitor.py` reports RAM, VRAM and CPU via `psutil` and `pynvml`;
`model_manager.py` decides load/unload against the VRAM budget. The existing
`process_manager.py` already unloads idle models after 60s — Phase 2 adds VRAM
awareness so a model is never loaded when it would breach the 4.5 GB ceiling.

---

## 7. Dry Run Mode (approved addition)

Every decision carries `dry_run: bool`. When set, the gate and the Amygdala do
their full normal work, and the Body layer reports what it *would* do instead of
doing it.

```
Thalamus → Amygdala (scores normally)
         → Prefrontal Cortex (decides normally, sets dry_run)
         → Body layer: describes the action, executes nothing
         → Activity log (logged as a dry run)
```

The response is a preview, not a refusal:

> I would click (500, 300) to open Chrome. Shall I proceed?

This is most useful at Trust Level 1, where the user wants to see the whole plan
before approving any step of it. It is also the safest way to test Steps 4 and 5
of the build order: dry-run first on the real machine, real execution only in
the `ZUZZ_Test` profile.

Body methods take the flag as a parameter rather than reading global state, so a
missing flag cannot accidentally mean "execute":

```python
def click(self, x: int, y: int, *, dry_run: bool = False) -> ActionResult:
    if dry_run:
        return ActionResult(executed=False, description=f"click at ({x}, {y})")
    ...
```

`ActionResult` carries `executed`, `description` and any data a real execution
would have produced, so callers and the UI work identically in both modes.

---

## 8. Trust Level Changes (approved)

The trust level is the most security-critical setting in ZUZZ, so changing it
requires escalating confirmation. `Any → L1` is instant, because a downgrade
reduces risk.

| Transition | Requirement |
|---|---|
| Any → L1 | Instant, no confirmation |
| L1 → L2 | Simple "Are you sure?" |
| L2 → L3 | Confirmation explaining the new permissions |
| L3 → L4 | Confirmation warning about reduced oversight |
| L4 → L5 | Type `CONFIRM`, plus a 10-second cooldown |

The dialog shows the current level and what it allows, the new level and what it
would allow, and a clear warning about the autonomy being gained. The AI cannot
trigger any of this — it is immutable rule 9 — so the only path is a human in
the UI.

The 10-second cooldown on L5 exists to defeat a click-through: an agent that
manages to render a fake dialog and induce a click still cannot outrun it.

---

## 9. Dependencies

| Package | For | Notes |
|---|---|---|
| `pyautogui` | Mouse and keyboard | Set `failsafe = True` at import |
| `pynput` | Global hotkeys | Preferred over pyautogui for the kill switch |
| `keyboard` | Ctrl+Shift+Q | Needs no window focus |
| `psutil` | RAM, CPU | Already a dependency |
| `pynvml` | VRAM | Needs an NVIDIA driver |

`pip install pyautogui pynput keyboard pynvml`

---

## 10. Risks and Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Agent moves mouse while the user is working | High | Failsafe corner, clear confirmation, kill switch |
| False sense of safety — gate exists but is bypassed | Critical | Test that no Body method runs without a decision |
| Kill switch deadlocks the machine | Critical | Separate process, 2s force-quit fallback |
| Agent deletes something important | Critical | File classifier + trust level 1 default |
| Screen coordinates wrong at high DPI | Medium | DPI awareness, verify before click |
| Vault loses the encryption key | Medium | Key file is git-ignored; losing it means re-entering keys |
| `pynvml` missing on non-NVIDIA machines | Low | Graceful degradation, VRAM reported as unknown |
| Rule file modified on disk | High | SHA-256 verified at startup, boot fails on mismatch |

---

## 11. Testing Strategy

Safety code is tested differently from everything else in this project: by
attacking it.

1. **Rule tests** — every immutable rule has a test that tries to violate it and
   asserts a block. Not a happy-path test.
2. **Trust matrix tests** — a table-driven test asserting every cell of the
   section 10 matrix, so a trust-level edit cannot silently widen access.
3. **Bypass tests** — attempt to reach every Body method without a
   `PermissionDecision` and assert a refusal.
4. **Threat scoring tests** — dangerous commands score as designed; benign
   lookalikes (`rm -rf` in a filename, `echo "delete"` as text) do not.
5. **Immutability test** — `immutable_rules.py` hash matches its recorded value.
6. **Kill switch test** — the switch stops action within 2 seconds, and the
   process survives being frozen.
7. **Manual sandboxing** — before any real desktop automation, test against a
   throwaway VM or a fresh Windows user profile. Never as the first test on the
   machine you are working on.

No test in this project may perform a real destructive action. Destructive paths
are exercised through mocks that assert the block.

---

## 12. Build Order

Each step is independently testable, and the system stays safe after every one.

1. **Prefrontal Cortex** — trust levels, permission matrix, immutable rules,
   confirmation flow. No Body code exists yet, so this proves the gate.
2. **Amygdala** — threat scoring from `danger_patterns.json`.
3. **Spinal Cord** — kill switch in a separate process, watchdog.
4. **Body/Fingers** — file operations behind the gate. File-only first: safe to
   test, and the classifier earns its keep immediately.
5. **Body/Hands** — mouse and keyboard. Last of the Body work, because it is the
   hardest to test safely.
6. **Hypothalamus** — VRAM-aware model loading.
7. **Integration** — "open Chrome and go to YouTube" with confirmation,
   logging, and the kill switch verified mid-action.

Steps 1-3 make ZUZZ *safe*. Steps 4-5 make it *capable*. Do not start 4 before 3
is proven with a manual test.

---

## 13. Test Environment (approved)

Desktop automation is tested in a separate Windows user profile named
`ZUZZ_Test` with **standard privileges, never administrator**. A mistake in the
test profile costs throwaway data; a mistake in the main profile costs real
work. A VM is deferred to Phase 8, when there is more RAM available — this
laptop has 16GB and a VM would leave too little for the agent itself.

Setup:

1. Settings -> Accounts -> Other users -> Add account
2. Create a local account named `ZUZZ_Test`
3. Leave it a standard user (NOT admin)
4. Copy the project folder to a location both profiles can reach
5. Run all Body/Hands tests from that profile first

Steps 1-3 of the build order touch no Body code, so they are tested in the
main profile. Step 4 onwards uses `ZUZZ_Test`.

---

## 14. Decisions Log

All five questions were answered on 2026-10-02. Recorded here so the reasoning
survives even if the matrix is edited later.

| # | Question | Decision |
|---|---|---|
| Q1 | File writes at L1 | Writes and deletes both ask at L1. Writes auto from L2; deletes always ask at every level. Temp paths auto-delete from L3. |
| Q2 | Kill switch scope | Stops local interactive actions, scheduled jobs and background processes. Cloud agents cannot be stopped remotely, so they are flagged and pause on sync. Reset is manual only. |
| Q3 | Auto-approve deletions | Never, for user files, at any trust level. Exception: project/system temp files from L3. Protected and OS files always blocked. |
| Q4 | Test environment | Separate standard-user Windows profile `ZUZZ_Test`. VM deferred to Phase 8. |
| Q5 | Trust level changes | Escalating confirmation: simple popup to L2, explanation to L3, oversight warning to L4, typed `CONFIRM` plus a 10s cooldown to L5. Any to L1 is instant. The AI cannot change it. |

Plus one addition: **dry run mode**, where the Body layer describes what it
*would* do and executes nothing. See section 7.

Build order, testing strategy and dependencies are approved unchanged.

