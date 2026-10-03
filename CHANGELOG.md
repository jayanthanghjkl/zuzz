--

### File 2: `C:\ZUZZ\CHANGELOG.md`

```markdown
# 📋 ZUZZ Changelog
This file is automatically updated by the AI assistant. It tracks every change made to the project.

## Format

## [Date] Session X

## Changed
- What was modified

## Added
- What was created

## Fixed
- What was fixed

## Notes
- Important observations

## Next Steps
- What to do next session

---

## [2026-10-03] Session 7 — Phase 2 Step 5: Body/Hands

Desktop control behind the gate. Every public method in `body/hands/` takes a
`PermissionDecision` as its first parameter, so the gate is enforced by the type
signature rather than by convention — the same rule Step 4 established for files.

### Added

**`body/hands/` — 19 modules**

| File | Lines | Contents |
|---|---|---|
| `hands.py` | 272 | the facade: one actuator, one switch, one logger |
| `mouse.py` | 261 | `move`, `click`, `double_click`, `right_click`, `drag`, `scroll` |
| `keyboard.py` | 264 | `type_text`, `press`, `press_keys`, `hotkey`, held keys |
| `input_base.py` | 116 | the checks the mouse and keyboard share |
| `window_control.py` | 216 | decision-guarded window operations |
| `window_ops.py` | 230 | the Win32 calls, with no gate imports |
| `window_manager.py` | 212 | window discovery and resolution |
| `clipboard.py` | 194 | read, write, clear, with credential escalation |
| `app_manager.py` | 275 | open, close, kill, list |
| `text_scanner.py` | 188 | dangerous to type; looks like a credential |
| `hotkey_policy.py` | 196 | the four hotkey lists and their treatments |
| `action_classifier.py` | 190 | operation → trust category; target resolution |
| `app_registry.py` | 149 | alias → executable; never-launch; protected processes |
| `screens.py` | 197 | every attached monitor; coordinate validation |
| `actuator.py` | 182 | the only place PyAutoGUI is imported, behind the fail-safe |
| `recorder.py` | 158 | the recording actuator the tests and dry-run share |
| `window_guards.py` | 141 | windows and processes ZUZZ will not drive |
| `hands_guards.py` | 158 | the decision check + the dangerous-text check |
| `exceptions.py` | 127 | the refusal hierarchy, mapped onto HTTP statuses |

**Config**
- `config/apps_whitelist.json` — 18 aliases, 10 never-launch commands, 12
  protected processes.
- `config/dangerous_hotkeys.json` — `always_blocked`, `requires_confirmation`,
  `reserved_by_zuzz`, `always_blocked_by_zuzz`.
- `config/trust_levels.json` gained `close_apps` and `fullscreen_control`, both
  asking at L1–L5.

**`face/api/routes_hands.py`** — 890 lines, 26 endpoints. Status codes 200 /
202 / 400 / 403 / 404 / 409 / 503 / 500.

**Frontend** — `pages/hands.html` (115), `static/js/hands.js` (417),
`static/css/hands.css` (168), plus a Desktop tab in `index.html` and
`pages/files.html`. The page shows what ZUZZ will refuse alongside the controls,
so the refusals are visible rather than discovered.

**Watchdog** — four additive signals: click bursts (50/10s), keystroke bursts
(500/10s), app launches (10/60s), refused hotkeys (3/60s). Each limit is
constructor-configurable; every limit is a report field even at zero.

**Tests — 135 new, `726 passed, 2 skipped`**
- `tests/test_body_hands.py` — 92 attack tests. Every one forges a genuinely
  *allowed* level-5 decision and asserts the Hands still refuses.
- `tests/test_hands_api.py` — 22 tests on the API flow and the frontend wiring.
- `tests/test_watchdog_desktop.py` — 21 tests on the new signals.

### Bugs the tests caught

Six real defects, all found by writing tests rather than by reading code:

1. **`hotkey_blocked` was counted as a burst of keystrokes.** The watchdog matches
   action names as substrings, and `"hotkey_blocked"` contains `"key"`. A refused
   hotkey is the *absence* of input, so this would let an agent trip the
   keystroke signal by having its keystrokes **refused** — punishing ZUZZ for
   the safe outcome. Refusals are now logged as `combo_refused`, which shares no
   fragment with the other three sets; `test_the_signal_sets_do_not_overlap`
   pins it.
2. **`InvalidInputRefused` became a 500.** `_run` matched a hand-maintained
   tuple of concrete exception classes rather than the `HandsRefused` base, so a
   newly added refusal silently fell through to "internal error". Now it checks
   the base class, which cannot drift.
3. **`confirmable_hotkey` never found its descriptions.** Combinations are sorted
   for order-independent comparison, so `win+l` became `l+win` and missed a
   lookup keyed on the unsorted string. Consequences are now matched by
   combination rather than by joined text.
4. **The `exfil_upload` pattern had the argument order backwards.** It looked for
   `host:/path user@` rather than `user@host:/path`, so `scp file user@host:/tmp`
   typed cleanly.
5. **The API test wrote a real stop flag.** It engaged the process-wide
   singleton to prove the stop outranks everything, which wrote to the
   developer's real storage and left ZUZZ unable to start. The fixture now
  confines the switch to `tmp_path`.
6. **Two test suites were not hermetic.** `Screens` on Windows reads the real
   virtual desktop, so coordinate tests passed or failed depending on how many
   monitors the machine had. Monitors are now pinned explicitly.

### Notes
- **Live hardware verification has not been performed.** Every test drives
  `RecordingActuator`; nothing in the suite can reach the mouse or keyboard. The
  procedure is in `docs/BODY_HANDS.md` §8 and needs an explicit go-ahead.
- **Full screen is a category, not a check.** The category is read from the
  screen at the moment of the click, and the decision is verified against it
  afterwards. A decision made against an ordinary window is therefore stale once
  a full-screen window appears — a replay refusal naming both categories.
- **DPI scale is reported, never applied.** Scaling coordinates would put every
  click in the wrong place on a scaled display.
- **Closing is not killing.** `close_app` posts `WM_CLOSE` so the application can
  save or cancel; `kill_process` does not. Both ask at every level.
- **A fragment matching two windows is refused** rather than resolved by taking
  the first. Guessing which of two windows the user meant is how input lands
  somewhere they did not choose.
- `config/apps_whitelist.json` is re-read on every lookup, so edits take effect
  without a restart. The whitelist removes prompts; it does not grant access.

### Next Steps
- Live hardware verification, with approval and a prepared machine
- Phase 2 Step 6: Body/Mouth

---

## [2026-10-03] Session 6 — Phase 2 Step 4: Body/Fingers

File operations behind the gate. Every method in `body/fingers/` takes a
`PermissionDecision` as its first parameter, so the gate is enforced by the type
signature rather than by convention.

### Added

**`body/fingers/` — 2,058 lines across 8 modules**

| File | Lines | Contents |
|---|---|---|
| `file_manager.py` | 447 | `read`, `write`, `copy`, `move`, `rename`, `delete`, `stat` |
| `trash_manager.py` | 467 | `trash`, `restore`, `list_trash`, `trash_stats`, `empty_trash`, `auto_purge_old` |
| `directory_manager.py` | 314 | `mkdir`, `list`, `search`, `tree` |
| `guards.py` | 274 | the four checks every operation runs, and the operation->category map |
| `file_classifier.py` | 254 | `classify_path`, `resolve_symlink`, `is_secret_file`, `is_zuzz_own_file`, `get_size_info` |
| `file_backup.py` | 172 | `backup_before_write`, `list_backups`, `restore_backup` (`.bak.1`-`.bak.3`) |
| `exceptions.py` | 76 | the refusal hierarchy, mapped onto HTTP statuses |
| `__init__.py` | 54 | the closed list of decision-free methods |

**`face/api/` — 998 lines**
- `routes_files.py` (748) — all 16 endpoints: read, write, copy, move, rename,
  delete, mkdir, list, search, tree, stat, trash, restore, empty-trash, backups,
  restore-backup. Status codes 200 / 202 / 403 / 409 / 413 / 423 / 500.
- `file_confirmations.py` (250) — the in-memory registry (capped at the last 10)
  and the preview the popup renders: file count, total size, reversibility.
  Previews are capped at 10,000 files and never count secrets or protected trees.

**Frontend — 749 lines**
- `pages/files.html` (106), `static/js/files.js` (506), `static/css/files.css`
  (137), plus the Files tab in `index.html` as a sibling of chat.
- Tree sidebar, file list with pagination, preview panel, right-click menu,
  confirmation modal with a dry-run checkbox, progress bar, trash view with
  restore, per-file backup list.

**Other**
- `send2trash==2.1.0` added to `requirements.txt` — purging hands items to the OS
  recycle bin rather than destroying them.
- `trash_purge` scheduler job (daily) in `face/api/server.py`, calling
  `auto_purge_old`: removes items past 30 days, and purges oldest-first when free
  disk drops below 5 GB.
- `.zuzz_trash/` and `*.bak.*` added to `.gitignore`.

**Tests — `tests/test_body_fingers.py`, 2,142 lines, 139 tests**

All 19 specified attack tests are present, plus the signature and limitation
tests around them. Every attack test forges a *genuinely allowed* decision at
trust level 5 and asserts the Body still refuses on its own classification — the
assumption is that the gate has been bypassed.

### Bugs the attack tests caught

Nine real defects. All were found by the tests, not by reading the code.

| # | Bug | Impact | Fix |
|---|---|---|---|
| 1 | `list_backups` passed a `str` to a helper expecting `Path` | `GET /api/files/backups` raised `AttributeError` on every call — the endpoint was 100% broken | `_version()` takes the string it is given |
| 2 | `TrashManager` had no kill switch; `_require` fell back to the global singleton | `delete` consulted a *different* switch than the `FileManager` it was called through. A stop enforced on every other operation could be missed by delete | switch carried on the manager and passed down; `FileManager` hands its own to the trash it holds |
| 3 | API resolved the path before the guard saw it | path traversal was undetectable — `../../../etc/passwd` became an absolute path and skipped the relative-path check entirely, returning 202 with a confirmation popup instead of 403 | `_precheck` now receives the raw path |
| 4 | Body logged `trash_file` / `purge_trash` | the watchdog counts log records whose action contains `delete`, so **mass deletion through the Body was invisible to the detector built to stop it**. 1,000 deletions did not trip the switch | logged as `delete_file` / `delete_purged`; documented why the names are load-bearing |
| 5 | `_decode` tried `utf-8` first, which succeeds on a BOM'd file | a leading `\ufeff` was returned as part of the text; `utf-8-sig` was never reached | BOM stripped explicitly |
| 6 | `verify_decision` compared the decision's target against the resolved target | any decision made for a relative path could never match, so legitimate relative-path operations were all refused | both sides resolved before comparison (stricter, not looser) |
| 7 | kill switch checked *after* the decision's own verdict | an action needing confirmation anyway reported "ask" while every operation was actually being stopped | switch now checked first |
| 8 | `restore` left an empty per-item folder in the trash | one dead directory accumulated per operation, forever | folder removed when the manifest entry is dropped; regression test added |
| 9 | `trash_stats` was decision-free but undocumented | the signature test could not pin the decision-free surface | added to the closed list and the package docstring |

Two further defects were found while repairing the suite, both in the API layer:
`restore` was unreachable without a confirmation round-trip that no test exercised
(the route is correct, the test was wrong), and `TrashManager.trash_stats` would
have failed the "exactly the documented decision-free methods" check.

### Test-count arithmetic

- Before: **450 passing**
- After: **589 passing**, 2 skipped (symlink creation needs elevation on Windows)
- Added by this session: **139 tests** in `tests/test_body_fingers.py`, and 1
  updated pinned job set in `tests/test_stop_dashboard_api.py` (the scheduler now
  registers `trash_purge`, which the dashboard test listed by exact name)

### Design decisions worth noting

- **The Body never decides.** It re-checks the decision, the switch, the resolved
  real path and the classification, then acts. It has no opinion about whether an
  action is safe, because two places answering that question would eventually
  disagree and the disagreement would be settled by whichever ran first.
- **Symlinks are resolved before classification, and the target is classified —
  not the link.** A link in the project directory pointing at `C:\Windows` is a
  Windows file whatever it is called here. Resolution is logged.
- **Deletion moves; it never unlinks.** No `os.remove`, `shutil.rmtree` or
  `Path.unlink` anywhere in `body/`. Backup rotation uses *moves*, so no Body
  code destroys a file even to make room for another backup. Purging goes to the
  OS recycle bin through `send2trash`.
- **Secrets are hard-blocked at every trust level, including 5**, and
  `include_hidden=True` does not expose them. There is no path through the API
  that lists or reads `.env`, `.ssh/`, `.aws/`, `*.pem`, `*.key` or the API vault.
- **ZUZZ's own code is read-only to the agent**: `brain/`, `body/`, `core/`,
  `config/`, `storage/logs/` and the kill switch flags. `plugins/` is
  deliberately writable — that is how ZUZZ is allowed to grow.
- **The 7-day retention floor is not configurable.** `auto_purge_old` uses
  `max(30, 7)`; no config value can make the scheduled purge touch a fresher item.
  `empty_trash` ignores the floor because the user explicitly asked.
- **Restore is a write.** It maps to `write_files`, so it asks at low trust
  levels rather than being free because it undoes something.
- **`delete_files` asks at every level, L1 through L5.** Trust 5 is autonomy over
  routine work. The only exception is the approved Step 3 rule: deleting a temp
  file may auto-approve from level 3.

### Limitations, pinned as tests

Each of these is a real limit, written down as a test so it cannot be quietly
forgotten (`tests/test_body_fingers.py`, "Limitations, pinned as tests"):

- **Purged items go to the OS recycle bin, not oblivion** — emptying the trash is
  recoverable outside ZUZZ. `test_purged_items_go_to_the_recycle_bin_not_oblivion`
- **`stat()["owner"]` is `None`** until the Windows SID resolver is wired, which
  needs pywin32 — not a dependency yet. Every other metadata field is present.
  `test_stat_owner_requires_pywin32`
- **A `PermissionDecision` is data.** A forged decision cannot reach a protected
  target because the Body re-checks, but the gate remains the real boundary and
  the Body is the backstop, not the lock. `test_a_forged_decision_is_the_last_line_not_a_boundary`
- **Pending confirmations are in-memory only** — a restart clears them, so a stale
  popup can never become an approval. `test_confirmations_do_not_survive_a_restart`
- **Only the last 10 operations await an answer**; older prompts expire.
  `test_confirmation_registry_is_capped_at_ten`
- **Previews count at most 10,000 files** and say so, rather than hanging.
  `test_preview_counts_are_bounded`

### Deviations from spec

- **`stat()["owner"]` returns `None` rather than a resolved SID.** Needs pywin32,
  which is not a dependency. Pinned as a test rather than left as a stub that
  looks implemented.
- **Symlink resolution is one level, as specified.** A link pointing at a link
  pointing at `C:\Windows` would be classified on the first target. Recorded here
  because "one level" is a deliberate limit, not an oversight; raising it is a
  Step 5 decision.
- **`trash_stats` is a third decision-free method** beyond the two the spec
  listed. It is read-only metadata about ZUZZ's own trash, the same category as
  `list_trash`, and the surface is pinned by a test so it cannot grow again.
- **Source files exceed the project's 200-line rule** (7 Step 4 files, 254-748
  lines; the test file is 2,142). Every other file is still within it. Flagged in
  `docs/ARCHITECTURE.md` section 9 with the obvious split, rather than silently
  dropping the rule.
- **Bulk-operation WebSocket progress and rollback were not built.** The progress
  bar exists in the UI, but there is no streaming progress channel and no
  rollback; a confirmed bulk operation runs to completion. Deferred — it is a
  larger piece of work than Step 4's scope and touches the WebSocket layer.
- **Compressed files are treated as single opaque files**, as specified. No
  listing, no extraction.
- **No file watcher.** Deferred to Phase 6 by agreement.
- **Test naming:** `test_secret_files_blocked_even_at_L5` keeps the capital `L5`
  from the spec rather than lowercasing it, so the delivered names match the
  specification exactly.

### Verified live

Against a real server on port 9111, with real files on disk:

- **Files page and assets serve**: `/pages/files.html` 200, `files.css` and
  `files.js` 200.
- **Five live attacks, all 403 with no prompt offered**: write to `.env`, read
  `C:\Windows\win.ini`, traversal `../../../etc/passwd`, UNC `\\?\C:\Windows\win.ini`,
  write to `brain/prefrontal_cortex/immutable_rules.py`. `.env` verified
  byte-identical afterwards.
- **Real operation with the permission popup**: write at trust level 1 returned
  202 with the full popup payload — operation, target, file count 1, size 9 B,
  threat 0/10 safe, trust level, `reversible: true`, `dry_run_available: true`,
  and a `confirmation_id`. The file was still `version 1` at that point.
  Confirming returned 200, wrote `version 2`, and left `live-demo.txt.bak.1`
  containing `version 1`.
- **Replaying a spent `confirmation_id` returned 400** and changed nothing.
- **Dry run** returned the plan and decision and performed no I/O.
- **Delete -> trash -> restore**: confirmed delete moved the file (not destroyed),
  `GET /api/files/trash` showed 1 item with its `trash_id` and age, confirmed
  restore put `version 2` back.
- **Kill switch**: engaging it made all five file endpoints return 409 and left the
  file untouched; reset returned the machine to `engaged: false` with all three
  scheduler jobs (`heartbeat`, `model_reaper`, `trash_purge`) running again.

### Notes

- `pytest` -> 589 passed, 2 skipped.
- Nothing in the test suite touches the developer's real filesystem: protected
  paths are guarded by canaries (a patched `move` or `open` that fails the test if
  reached), the kill switch is isolated in `tmp_path`, and the trash lives in
  `tmp_path` with a purge transport that never reaches the real recycle bin.
- One trap worth recording: pytest's `tmp_path` sits under the system temp
  directory, which `core.paths` classifies as `temp`, and temp files auto-approve
  deletion from trust level 3. An autouse fixture now replaces the `tempfile`
  reference *inside* `core.paths` so tests exercise the general rules. Without it,
  every test silently proved the temp exception instead, and assertions about
  `safe`/`caution` classification were wrong by construction.
- Session 5's "Next Steps" listed Steps 4 and 5 the wrong way round (Fingers as
  mouse/keyboard, Hands as files). Step 4 is the file layer, Step 5 is
  mouse/keyboard, matching the actual spec.

### Next Steps

- Phase 2 Step 5: Body/Hands (mouse and keyboard behind the gate)
- Split `face/api/routes_files.py` (748 lines) and `file_manager.py` before Step 5
  copies the pattern
- Phase 2 Step 6: Hypothalamus VRAM-aware model loading
- Phase 2 Step 7: end-to-end integration test
- Bulk-operation progress streaming and rollback (deferred from Step 4)

---

## [2026-10-02] Session 3 — Phase 2 Step 1: Prefrontal Cortex

Phase 2 approved by Jayanthan on 2026-10-02 with all five open questions
answered and dry run mode added. Decisions are recorded in
`docs/PHASE_2_PLAN.md` section 14.

### Added
- `config/trust_levels.json` rewritten as the machine-readable matrix: 17
  action categories x 5 trust levels, plus policy refinements and the
  trust-change confirmation policy. Read fresh on every decision, so changing
  what ZUZZ may do is a config edit rather than a code change.
- `brain/prefrontal_cortex/action.py` — `Action` and `ActionCategory`. A
  category missing from the matrix is refused rather than assumed harmless.
- `brain/prefrontal_cortex/trust_matrix.py` — matrix lookup and clamping
- `brain/prefrontal_cortex/permission_decision.py` — the verdict, with
  `allowed` / `requires_confirmation` / `dry_run` / `rule_id` and an API form
- `brain/prefrontal_cortex/permission_gate.py` — the gate: immutable rules,
  then the matrix, then target-based overrides
- `brain/prefrontal_cortex/immutable_rules.py` — the nine approved rules with a
  SHA-256 over their canonical serialisation, so reformatting the file cannot
  mask a change to the rules
- `brain/prefrontal_cortex/trust_manager.py` — trust level state plus the
  escalating confirmation (instant to L1, simple to L2, explanation to L3,
  oversight warning to L4, typed `CONFIRM` plus a 10s cooldown to L5)
- `core/paths.py` — protected / temp / caution / safe classification, shared by
  the gate and the Body's file layer so the two cannot disagree
- `core.config.get_trust_levels()` — deliberately not memoised, unlike the other
  config readers
- `face/api/routes_safety.py` — `GET /api/safety/trust`,
  `POST /api/safety/trust/preview`, `POST /api/safety/trust`,
  `POST /api/safety/check`, `GET /api/safety/rules`,
  `GET /api/safety/categories`
- The server now verifies the immutable rules at startup and refuses to boot on
  a digest mismatch
- `tests/test_safety.py`, `tests/test_trust_matrix.py`,
  `tests/test_safety_trust.py`, `tests/test_paths.py` — 123 attack-based tests

### Decisions implemented
- **Q1** writes to Desktop/Documents ask at L1 and auto-approve from L2
- **Q2** the kill switch stops scheduled jobs and background processes too;
  cloud agents are flagged and pause on sync; reset is manual only (Step 3)
- **Q3** user file deletion asks at every level including L5; temp paths
  auto-delete from L3; protected and OS paths are blocked outright
- **Q5** escalating confirmation for trust level changes; the AI cannot change
  it, structurally, because only the HTTP route reaches the manager
- **Dry run** `dry_run` rides on the decision, so the Body layer receives it
  rather than reading global state

### Fixed
- **`/api/providers` reported untried providers as `healthy`.** Status was
  derived from the failure count alone, so a configured provider that had never
  answered — including one that was completely down — showed the same green dot
  as one that had just replied. Zero failures is not evidence of health. Added an
  `untested` state, set from a new `answered` flag that only `record_success`
  sets, plus a dashed-dot style. Verified live: after four local Ollama answers
  all five cloud providers correctly read `untested`; after a forced Groq call
  Groq alone reads `healthy`.
- A dead Colab runtime was reported as a bare `HTTP 404`, sending the user
  hunting for a wrong URL when the URL was correct. ngrok keeps the tunnel
  registered while the runtime is down, so it answers with its own HTML error
  page — the connector now says so explicitly.
- `core/paths.py` compared caution and credential file names case-sensitively,
  so `C:\Users\me\Desktop` classified as `safe` instead of `caution` — meaning
  the gate would not have asked before writing there. NTFS is case-insensitive,
  so the name sets are now lower-cased before comparison.
- The first draft of the gate treated immutable rules R5 and R7 (no silent
  credential access, no silent software install) as hard blocks. The rules say
  *without confirmation*, not *never*, and blocking would have made the
  confirmation dialog unreachable. R1, R2 and R3 block; R4, R5 and R7 force
  confirmation and cite the rule id in the decision.

### Not built yet
Steps 3-7 of the approved build order: Spinal Cord kill switch, Body/Fingers,
Body/Hands, Hypothalamus, integration. No Body method exists yet, so nothing can
execute an action.

### Bugs caught in Session 3 — five, all before anything shipped

| # | Bug | Caught by |
|---|---|---|
| 1 | Path names compared case-sensitively, so `C:\Users\me\Desktop` classified `safe` and the gate would not have asked before writing there | attack tests |
| 2 | Immutable rules R5/R7 implemented as hard blocks instead of forced confirmation, making the confirmation dialog unreachable | attack tests |
| 3 | `/api/providers` reported never-called providers as `healthy` | live verification |
| 4 | Dead Colab reported as a bare `HTTP 404`, blaming the URL when the runtime was simply stopped | live verification |
| 5 | `import requests.py` saved in the project root — the Colab test script under a broken filename, containing an ngrok URL | live verification |

### Phase 1 local path verified (parallel track)
Ollama was already installed; `qwen2.5-coder:1.5b` and `qwen2.5-coder:3b` were
pulled, and the previously unverified local path now answers:

| Prompt | Level | Model | Time | Reply |
|---|---|---|---|---|
| Say hello in exactly 5 words | 2 | qwen2.5-coder:3b | 8.1s | Hello there! |
| What is 2 + 2? | 1 | qwen2.5-coder:1.5b | 3.7s | 4 |
| Name three colors | 2 | qwen2.5-coder:3b | 0.7s | Red, blue, green |
| Capital of France? | 2 | qwen2.5-coder:3b | 0.6s | Paris |
| What is 10 * 7? | 1 | qwen2.5-coder:1.5b | 0.6s | 70 |

Also done: removed the stray `import requests.py` from the project root (it was
the Colab test script, saved with a broken filename, and it contains an ngrok URL
that should not sit in the repo root) and moved it to
`scripts/test_colab_live.py`; removed the `COLAB_API_URL` line from `.env` that
held a Colab notebook link instead of the ngrok API URL, with a backup at
`%TEMP%\zuzz_env_backup.env`.

Colab is currently reported dead — the ngrok tunnel is registered but the Colab
runtime is stopped, so the notebook cannot answer until it is restarted.

---

## [2026-10-02] Session 4 — Phase 2 Step 2: Amygdala

Threat detection. Scores every action 0-10 *before* the Prefrontal Cortex
decides, and can only ever raise scrutiny.

### Added
- `brain/amygdala/threat_assessment.py` — `ThreatAssessment`, the four levels,
  and `level_for` with default bands so a missing config block cannot collapse
  everything to "safe"
- `brain/amygdala/threat_detector.py` — `ThreatDetector`. Patterns compiled once
  via `lru_cache`, assessments cached by (category, target) and bounded at 512
  entries
- `brain/amygdala/root_wipe`-style pattern set in `config/danger_patterns.json`,
  extended from 8 literal strings to 11 regex patterns that declare which action
  categories they apply to
- `core/audit.py` — `storage/logs/audit_log.jsonl`, append-only, for
  safety-critical events only (Q8). In `core/` because three regions write to it
- `POST /api/safety/assess` — score an action without deciding or executing
- `GET /api/safety/audit` — the safety audit trail, filterable by event type
- `POST /api/safety/check` now returns the threat assessment alongside the decision
- `tests/test_amygdala.py` — 58 attack tests; `tests/test_audit_log.py` — 8

### Context-awareness, not just regex
Each pattern declares which categories it applies to, so command patterns are
never consulted for file actions. `docs/reg delete.txt` scores 0 even though the
registry pattern matches its text, and the skip is recorded in the assessment's
context notes rather than silently dropped — a detector you cannot audit is one
you cannot trust.

### Decisions implemented
- **Q6** a forbidden score blocks *and* explains itself: the reason reaches the
  user verbatim ("amygdala scored this forbidden (10/10): level forbidden:
  matched recursive_delete, root_wipe"), so the safety system teaches rather than
  appearing broken
- **Q7** `dangerous` and `forbidden` always require confirmation, including at
  trust level 5. Additionally, `FORCE_CONFIRM_PATTERNS` implements immutable rule
  R4 directly: a destructive pattern forces confirmation at *every* level, so
  `rm -rf node_modules` cannot auto-approve at L5 just because it scores 5
- **Q8** separate `audit_log.jsonl` for blocks, vetoes, confirmations, trust
  changes, kill switch events, rule violations and threat assessments. It
  refuses non-safety events so ordinary activity cannot dilute the record

### Fixed
- `core/paths.py` resolved relative paths against the CWD when classifying temp,
  so `temp/scratch.bin` — the exact form the body layer produces — was not
  recognised as the project's temp directory, silently denying the approved
  temp-deletion exception. Relative paths now resolve against the project root.
- `docs/ARCHITECTURE.md` was full of UTF-8 mojibake (`â€"`, `Â·`, `â†’`) from an
  earlier write. Repaired; a scan of the other docs found none.
- The first `root_wipe` regex used `/*`, which is a quantifier rather than a
  literal asterisk, so `rm -rf /*` scored 5 instead of 10. Now written `/\\*?`.
- The first `root_wipe` regex also matched any path ending in `/`, so
  `rm -rf build/` scored as a filesystem wipe. It now requires the trailing
  token to be preceded by whitespace or start of string.
- `recursive_delete_windows` used `\b/s\b`, which can never match — `/` is not a
  word character, so there is no word boundary before it. Replaced with
  `(?<!\w)/s(?!\w)`.
- `level_for` fell back to `safe` for scores 3-8 when no config was supplied.

### Verified
- `pytest` -> 305 passed
- `rm -rf /` and `RM -Rf /*` and `rm -rf ~` -> score 10, forbidden, blocked at
  every trust level
- `diskpart` -> 7, dangerous, asks at L5
- `git status`, `npm run build`, `ls -la` -> score 0, auto at L5
- `docs/notes about rm -rf.txt`, `src/delete_folder.py` -> score 0
- Slowest assessment across 50 samples is well under the 50ms budget

---

## [2026-10-02] Session 5 — Phase 2 Step 3: Spinal Cord

The emergency stop. Setting the flag is only half of it — an emergency stop that
leaves a runaway job running has not stopped anything, so this session also
built what the stop actually reaches.

### Added
- `brain/spinal_cord/stop_state.py` — the two flag locations, JSON read/write
  that never raises, and the cooldown ladder
- `brain/spinal_cord/reset_policy.py` — the rules for re-arming: confirmation,
  the typed word, the progressive wait, and the surviving-flag refusal. Isolated
  because these are the rules someone would want to loosen
- `brain/spinal_cord/switch_status.py` — status payload for the UI and API
- `brain/spinal_cord/kill_switch.py` — `KillSwitch.trigger` / `.is_killed` /
  `.reset`, plus `get_kill_switch()`
- `brain/spinal_cord/stop_scope.py` — carries the stop out: pauses jobs,
  terminates registered processes, flags cloud agents
- `brain/spinal_cord/terminate.py` — polite terminate then hard kill after 2s,
  isolated so the hotkey process does not import the safety layer
- `brain/spinal_cord/watchdog.py` — CPU-saturation streak and mass-deletion
  detection; trips the same switch, never clears it
- `brain/spinal_cord/emergency_stop.py` — standalone process for `Ctrl+Shift+Q`,
  importable without the gate, audit log or config
- `brain/brainstem/scheduler.py` — `pause()` / `resume()`. Pausing keeps the job
  registry, so a job registered *during* the stop runs again after the reset
- `face/api/routes_kill_switch.py` — `GET /api/kill-switch`,
  `POST /api/kill-switch/stop`, `POST /api/kill-switch/reset`,
  `GET|POST /api/kill-switch/watchdog`
- `face/frontend/static/js/killswitch.js` — Stop button, stopped banner, and the
  RESET dialog
- 145 tests across `test_kill_switch.py`, `test_switch_reset.py`,
  `test_switch_persistence.py`, `test_reset_policy.py`, `test_stop_scope.py`,
  `test_watchdog.py`, `test_watchdog_deletions.py`, `test_kill_switch_gate.py`,
  `test_kill_switch_api.py`, `test_stop_dashboard_api.py`,
  `test_scheduler_stop.py`

### Changed
- `PermissionGate` consults the kill switch **first**, before the Amygdala and
  before the matrix. While engaged every action is `blocked` with no further
  checks — no trust level and no threat score reaches it. `PermissionGate()` with
  no arguments uses the process-wide switch, so the stop cannot be bypassed by
  forgetting to pass it.
- `lifespan` in `face/api/server.py` pauses the scheduler at startup if the flag
  is already set, so a stop that survived the previous run holds.
- `/api/providers` reports `kill_switch_active` and shows every provider as
  `kill_switch_active` — a distinct state from `degraded` or `unconfigured`,
  because a paused cloud agent reads as deliberate, not broken.
- `/api/brain/status` includes `kill_switch` and the scheduler's pause state.

### Verified live
- Stop via `POST /api/kill-switch/stop`: gate returns `blocked | kill switch
  engaged (ui): live verification`, scheduler paused, all five providers flagged
- Reset refused with HTTP 409 without confirmation, and with the wrong word; the
  switch stayed engaged through both
- Correct reset (`RESET` + confirmed) re-armed the machine and returned the gate
  to `auto`; `cooldown_remaining` came back as 5s for the next reset
- **Restart persistence**: stopped the server, started it again, and it came back
  `engaged=True` with the original reason and source intact, the scheduler
  paused, and jobs still registered. Reset then worked normally.
- Audit trail recorded all four transitions in `storage/logs/audit_log.jsonl`

### Notes
- `pytest` -> 450 passed, every Python file at or under 200 lines.
- The switch is only as strong as files an agent cannot reach: deleting both
  flag files by hand does re-arm. That limit is now pinned by a test
  (`test_deleting_both_flags_by_hand_does_re_arm`) rather than left implicit. What
  holds the line is that the reset path refuses without a human and R1 protects
  the storage directory.
- `keyboard` is not in `requirements.txt`; `emergency_stop.py` reports a clear
  message and points at the Stop button when it is missing.

### Next Steps
- Phase 2 Step 4: Body/Fingers (file operations behind the gate)
- Phase 2 Step 5: Body/Hands (mouse and keyboard behind the gate)
- Phase 2 Step 6: Hypothalamus VRAM-aware model loading
- Phase 2 Step 7: end-to-end integration test

> Corrected in Session 6: this list originally had Steps 4 and 5 the wrong way
> round — Fingers is the file layer and Hands is mouse/keyboard.

---

## [2026-10-02] Session 2 — Model Maintenance Tooling + Phase 2 Planning

### Added
- `BaseConnector.list_models()` — queries each provider's live catalogue
- `HuggingFaceConnector.list_models()` — HuggingFace has no catalogue, so the
  configured repo is checked directly (200 = exists, 404 = renamed or removed)
- `ColabConnector.list_models()` — returns empty; the notebook publishes no
  catalogue and is judged by `/health` instead
- `scripts/check_models.py` — asks every configured connector whether the model
  ZUZZ would actually call is still served. Exit 1 on a dead model, `--json`
  for machine consumption
- Model check wired into `scripts/health_check.py`
- `docs/MODEL_MAINTENANCE.md` — how to check models, where each provider's
  catalogue lives, how to switch models, and the current defaults
- `docs/PHASE_2_PLAN.md` — Phase 2 scope, interfaces, trust matrix, threat
  scoring, kill switch design, risks, testing strategy, build order
- `tests/test_model_check.py` — 13 tests covering catalogue listing, matching
  and each check outcome
- `ColabConnector.health_detail()` — surfaces the notebook's own report
  (`flask running, ollama process alive, ollama api working`) instead of a bare
  boolean

### Fixed
- **HuggingFace repo check was broken and never proved anything.** Three
  separate defects: the repo id was URL-encoded (`%2F`), which HuggingFace
  rejects with HTTP 400; no `Authorization` header was sent, so the endpoint
  answered 401 for every request; and redirects were not followed. Unauthenticated,
  the endpoint returns 401 even for a repo that does not exist, so a 404 — the
  only signal that would have caught a deleted model — was unreachable. Now
  authenticated, un-encoded, and redirect-following, with 200 / 404 / 401-403
  distinguished.
- **`list_models()` conflated "unreachable" with "model gone".** It returned an
  empty list for a failed catalogue request, which the checker reported as
  `unknown` — so a provider being down and a model being deleted looked the
  same. It now raises `ConnectorError` for transport, HTTP and JSON failures,
  and an empty list means the provider really serves nothing.
- **Two more dead models found by the new checker.** OpenRouter's
  `meta-llama/llama-3.1-8b-instruct:free` and NVIDIA's
  `meta/llama-3.1-70b-instruct` are both gone. Switched to
  `qwen/qwen3.8-27b:free` and `nvidia/llama-3.1-nemotron-70b-instruct`, both
  confirmed present in the live catalogues. All five providers now report `ok`.
- The test `mock_http` helper captured an already-patched `AsyncClient.__init__`,
  so a second mock block in the same test silently reused the first handler. The
  real init is now captured once at import. This affected every connector test
  that used two mock blocks.
- `matches()` in the model checker compared names exactly, so a namespaced
  config could not match a bare catalogue entry.

### Changed
- Added `BaseConnector.PUBLISHES_CATALOGUE`. False for Colab, whose notebook
  publishes no catalogue and is judged by `/health`. This replaces an
  `isinstance` check against one specific connector, so any future
  catalogue-less provider declares itself instead of needing a code change here.
- A HuggingFace repo that answers 401 or 403 is reported as `unknown` with a
  "may be gated" note rather than `dead`: a permission problem is not proof the
  model was removed.
- `ColabConnector.health_check()` parses the notebook's health JSON and records
  a failed check when the notebook reports anything other than `healthy`
- Connector defaults updated to live models: Groq `openai/gpt-oss-120b`,
  OpenRouter `qwen/qwen3.8-27b:free`, NVIDIA `nvidia/llama-3.1-nemotron-70b-instruct`
- `.env` and `.env.example` document per-provider model overrides

### Notes
- Jayanthan updated `.env` with keys and URLs. All providers verify as
  configured, including NVIDIA, which now has a key.
- `.env` has two `COLAB_API_URL` entries; the second (the ngrok URL) wins and is
  correct. The first has an inline comment and should be deleted to avoid
  confusion — flagged, not changed.
- The Colab notebook is alive: `/health` reports flask running, Ollama process
  alive, upstream API working.
- An early PowerShell probe suggested the tunnel was down. It was not —
  PowerShell's browser User-Agent receives ngrok's HTML interstitial, while
  httpx gets through. No bug.

### Verified
- `pytest` -> 113 passed
- `python scripts/check_models.py` -> exit 0, groq/openrouter/nvidia/huggingface ok,
  colab unknown by design (no catalogue, notebook healthy)
- `python scripts/health_check.py` -> All critical checks passed

### Next Steps
- Jayanthan: install Ollama and pull the three models, then run the Phase 1
  verification checklist
- Approve `docs/PHASE_2_PLAN.md` — five open questions are listed at the end
- Phase 2 coding starts only after that approval

---

## [2026-10-02] Session 1c — Corpus Callosum Connectors

### Added
- `brain/corpus_callosum/base_connector.py` — abstract connector contract:
  credential detection, quota enforcement, failure tracking, status reporting
- `brain/corpus_callosum/cloud_bridge.py` — registry, provider selection,
  fallback across providers, benching after hard failures
- Connectors: `groq_connector.py`, `colab_connector.py`, `huggingface_connector.py`,
  `openrouter_connector.py`, `nvidia_connector.py`
- `brain/corpus_callosum/api_vault.py` — Fernet-encrypted credential storage
- Support modules, split out to respect the 200-line rule: `connector_state.py`
  (errors, rate limiter, status), `connector_transport.py` (chat + streaming),
  `connector_status.py` (health/quota), `connector_stream.py` (SSE parsing),
  `bridge_support.py` (benching, attempt order, dashboard snapshot)
- `brain/cerebrum/backends.py` (Ollama + LiteLLM calls) and `brain/cerebrum/streaming.py`
- `GET /api/providers` — per-provider state, attempt order, bench timers
- Provider status dots in the chat UI header, refreshed every 15s
- `docs/ARCHITECTURE.md` — component diagram, request flow, connector role,
  built-vs-planned table
- `pytest.ini` with asyncio auto mode
- Tests: `test_connectors.py`, `test_groq.py`, `test_colab.py`, `test_huggingface.py`,
  `test_openrouter_nvidia.py`, `test_cloud_bridge.py`, `test_api_vault.py`,
  plus `tests/provider_mocks.py`. All providers are tested through
  `httpx.MockTransport` — no network call or API key needed.

### Changed
- `Cerebrum.think_cloud` now prefers dedicated connectors and falls back to LiteLLM only
  when every connector fails
- `Cerebrum.stream` streams from cloud connectors instead of buffering one chunk
- `Router` skips cloud levels entirely when no provider credential is configured
- `Router.route` responses now include `classified_level` alongside the level that
  actually answered, so an escalation is visible instead of confusing
- `LoadBalancer` takes provider names from the bridge so both agree on what is usable
- `core/config.py` loads `.env` at import; provider detection no longer depends on
  which component happened to read settings first
- `core/config.py` accepts aliases (`HF_API_KEY`, `COLAB_URL`, `OLLAMA_URL`, `WEB_PORT`)
  because Jayanthan's `.env` uses the short names

### Fixed
- `BaseConnector.headers()` built the auth headers then discarded them, so every
  request went out unauthenticated. Found by a test asserting the bearer token.
- An HTTP 429 marked a provider merely degraded; it now benches it and reports
  `rate_limited` with a retry timer.
- The Colab connector could not read an OpenAI-shaped response, only flat keys.
- HuggingFace chunk parsing returned fragments wrapped in JSON quotes.
- Tests picked up real credentials from `.env`; an autouse fixture now strips
  provider keys before every test.
- UTF-8 BOMs stripped from 24 generated files.
- **Groq's default model was dead.** `llama-3.3-70b-versatile` has been retired
  from Groq's catalogue and returned HTTP 404, so every cloud request silently
  fell through to Colab. Queried the live `/models` endpoint and switched the
  default to `openai/gpt-oss-120b`, their largest general model. Verified with a
  real call: 1.0s round trip. The spec's "complex tasks to a big cloud model"
  intent still holds.

### Decisions
- LiteLLM kept as a fallback, not removed, so a provider without a connector still works.
- The ngrok URL is treated as the Colab credential, since the notebook has no API key.
- Connectors are preferred over LiteLLM for provider-specific control: rate limits,
  benching, tailored errors and dashboard status.

### Verified
- `pytest` -> 97 passed
- `python scripts/health_check.py` -> All critical checks passed
- Every source and test file is under 200 lines
- Live end-to-end chat through the API against real providers:
  - "Explain quantum computing in 2 sentences" -> L3, answered by Colab (llama3.1:8b)
  - "Design a database schema for an Uber-like app" -> L3, answered by Groq
    (openai/gpt-oss-120b), first attempt, 1.0s, correct schema output
- `/api/providers` reports groq, colab, huggingface, openrouter as configured
  and healthy; nvidia is unconfigured (no key in `api keys.md`)
- Credentials from `api keys.md` are in `.env`, which `git check-ignore` confirms
  is untracked alongside `api keys.md` itself

### Notes
- Credentials from `api keys.md` are now in `.env` (git-ignored). NVIDIA Build has
  no key, so its connector stays unconfigured — expected, not an error.
- The Colab notebook is still alive and answered a live request. Its ngrok URL
  changes each session; update `COLAB_API_URL` in `.env` when it does.
- `ApiVault` is built but not yet wired into the connectors; they read from the
  environment. Phase 2's dashboard key entry will use it.

### Next Steps
- Phase 2: Prefrontal Cortex permission gate, Amygdala threat detection,
  Spinal Cord kill switch, Body/Hands and Body/Fingers
- Wire `ApiVault` into connector key resolution
- Install Ollama and pull the three models to exercise the local path

---

## [2026-10-02] Session 1b — Phase 1 Review Response

### Changed
- PROJECT.md section 14: project root changed from `C:\ZUZZ` to `web\zuzz-assistant`
- PROJECT.md section 14: `core/` added to the documented folder tree
- PROJECT.md section 17: new architecture rule — shared utilities used by 3+ brain regions
  belong in `core/`, not duplicated per region
- PROJECT.md section 18: Phase 1 marked CODE COMPLETE, with an explicit note that
  end-to-end chat verification is still outstanding

### Decisions
- **Path change: APPROVED by Jayanthan.** Keeping the project inside the workspace is more
  portable, easier to version control, and standard practice. The spec's `C:\ZUZZ` path is
  retired; `web\zuzz-assistant` is the root from now on.
- **`core/config.py`: APPROVED as a permanent deviation.** Four regions need identical
  settings access, and section 14 has no shared layer. Options to fold it into an existing
  region were rejected because they either duplicate config loading or put shared code in a
  brain region that does not own it. It is now documented in section 14 and covered by a new
  rule in section 17.

### Changed
- `brain/thalamus/complexity_analyzer.py` — conceptual questions now escalate to level 3:
  added a technical-topic pattern (quantum, transformers, cryptography, compilers, recursion,
  distributed systems, complexity analysis) and a deep-question rule (a message opening with
  explain/describe/how-does/compare and running over 45 characters). Bare "explain this"
  stays at level 2.

### Fixed
- Routing gap found during review verification: "Explain quantum computing" classified as
  level 2 (local 3B) instead of level 3 (cloud). Covered by three new regression tests.
- `transformer` pattern missed the plural "transformers" because of the trailing word boundary.

### Added
- `SETUP.md` — env setup, Ollama install, model downloads, running the server, end-to-end
  test procedure, and how to run the test suite

### Verification (step D)
- `python scripts/health_check.py` -> All critical checks passed
- `python -m pytest tests -q` -> 41 passed (38 original + 3 routing regressions)
- Server boots on port 8080; `/api/health` returns ok
- All four static assets return 200
- Routing matrix checked live over HTTP: 8/8 messages classify to the expected level
- Activity log records startup, scheduler start and job registration

### Known Limitations (block end-to-end verification)
- No `.env` on this machine, so no cloud provider credentials are configured
- Ollama is not installed or running here, so no local model can answer
- Streaming replies deliberately use local models only; cloud replies are buffered into one
  chunk because only Ollama streams natively
- `corpus_callosum/groq_connector.py` and `colab_connector.py` are not written yet — Phase 1
  reaches cloud models through LiteLLM directly, so the Colab and Groq brain connections are
  still open items

### Next Steps
- Jayanthan: run SETUP.md, then verify chat end-to-end and report results
- Write the Corpus Callosum connectors so Groq and Colab are first-class backends
- Phase 2: Prefrontal Cortex permission gate, Amygdala threat detection, Spinal Cord kill switch

---

## [2026-10-02] Session 1 — Phase 1 Implementation

### Added
- Project scaffold: full folder tree from PROJECT.md section 14, all package `__init__.py` files
- `requirements.txt`, `.env.example`, `.gitignore` (blocks `.env` and `api keys.md`)
- `config/settings.json`, `config/models.json`, `config/personality.json`, `config/trust_levels.json`, `config/danger_patterns.json`
- `core/config.py` — shared settings/secret loading (new top-level package, see Notes)
- Cerebrum: `brain/cerebrum/cerebrum.py`, `prompts.py`, `reasoning.py`
  - Local Ollama chat (non-streaming + streaming), cloud chat via LiteLLM with lazy import
  - Personality-driven system prompt, history trimming, confidence heuristic
- Thalamus: `brain/thalamus/router.py`, `complexity_analyzer.py`, `load_balancer.py`
  - Levels 1-5 classification, offline capping at level 2, fallback chain, provider cooldown benching
- Brainstem: `activity_logger.py` (append-only JSONL), `heartbeat.py`, `process_manager.py`, `scheduler.py`, `server.py`
- Face: `face/api/server.py` (composition root) plus `routes_chat.py`, `routes_brain.py`, `routes_settings.py`, `routes_plugins.py`
- Web UI: `face/frontend/index.html`, `static/css/main.css`, `static/js/{app,chat,websocket}.js`
- `start.py` entry point (`--host`, `--port`, `--reload`)
- `scripts/health_check.py`
- Tests: `tests/conftest.py`, `test_cerebrum.py`, `test_thalamus.py`, `test_integration.py` — 38 tests, all passing

### Fixed
- Chat failures now return HTTP 503 with a readable message instead of a 500 traceback (missing `litellm` and dead Ollama both surface as `CerebrumError`)
- FastAPI startup/shutdown migrated from deprecated `on_event` to a `lifespan` context manager
- Health check no longer fails the run when Ollama has no models downloaded (warn, not fail)

### Notes
- **Spec deviation:** added `core/config.py`. Cerebrum, Thalamus, Brainstem and Face all need the same settings and PROJECT.md section 14 has no shared layer. Fold this into the section 14 tree or keep it as the sanctioned common layer — decided by Jayanthan.
- Routing for cloud levels requires a provider key in `.env`; with no keys the router degrades to local levels 2 then 1.
- Streamed replies deliberately use local models only (levels 1-2) because Ollama streams natively; cloud requests are buffered and emitted as a single chunk.
- Verified live: server boots, `/api/health`, `/api/classify`, `/api/brain/status`, `/api/chat` (503 as expected with Ollama down), static assets and the WebSocket stream all behave correctly.
- Ollama is not currently installed/running on this machine, and no `.env` exists yet — both are required for a real end-to-end chat test.

### Next Steps
- Create `.env` from `.env.example` and fill in the cloud keys
- Install Ollama and pull `qwen2.5-coder:1.5b` / `qwen2.5-coder:3b`
- Run a real end-to-end chat test with a live model
- Phase 2: Prefrontal Cortex permission gate + Amygdala threat detection, then Spinal Cord kill switch

```
