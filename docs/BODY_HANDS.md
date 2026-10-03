# Body/Hands — Desktop Control

Step 5 gives ZUZZ a pointer, a keyboard, a clipboard and control over
applications and windows. Everything here sits behind the same gate as the file
layer, with the same rule: **the Body executes, it never decides.**

- Implementation: `body/hands/`
- API: `face/api/routes_hands.py`
- Desktop tab: `face/frontend/pages/hands.html`
- Config: `config/apps_whitelist.json`, `config/dangerous_hotkeys.json`

---

## 1. The shape of the layer

```
face/api/routes_hands.py   score -> decide -> confirm -> execute
        |
   body/hands/hands.py     Hands: the facade, wires everything to one
        |                  actuator, one kill switch, one logger
        +-- mouse.py       pointer          (InputLayer)
        +-- keyboard.py    keys             (InputLayer)
        +-- clipboard.py   clipboard
        +-- app_manager.py applications, processes
        +-- window_control.py  window ops, decision-guarded
        |
   input_base.py           the checks every input layer shares
   hands_guards.py         the decision check + dangerous-text check
   hotkey_policy.py        which combinations may be synthesised
   window_guards.py        windows and processes ZUZZ will not drive
   action_classifier.py    operation -> trust category, and target resolution
   text_scanner.py         dangerous to type; looks like a credential
   screens.py              every attached monitor, and coordinate validation
   actuator.py             the only place PyAutoGUI is imported
   recorder.py             the recording actuator the tests share
```

Each module is under 200 lines apart from `hands.py` (272), `mouse.py` (261),
`keyboard.py` (264), `app_manager.py` (275) and the three window modules
(212–230), which are longer because they are dispatch tables with
documentation. The split exists so that the modules which talk to Windows
contain no gate imports, and the module which enforces the gate contains no
Windows calls.

---

## 2. Three lines of defence, in order

```
1. the gate        may this action happen at this trust level?
2. the Body        is this decision valid for *this* target, right now?
3. the fail-safe   has the cursor reached a screen corner?
```

The middle one is the interesting one, because a decision can go stale between
being made and being used. Every input operation re-checks:

1. **The decision** — real, not a dry run, allowed, for this category, for this
   target. The kill switch is consulted *before* the decision's own verdict, so
   a stop is reported as a stop.
2. **The window** — not a credential prompt, not elevated above ZUZZ.
3. **The coordinate** — on an attached monitor.

### Full screen is a category, not a check

The category an input operation needs is read from the screen **at the moment of
the click**:

```python
category = (
    "fullscreen_control" if focus["fullscreen"]
    else "mouse_keyboard"
)
verify_decision(decision, category, target)
```

A decision issued while an ordinary window had focus therefore cannot be spent
on a full-screen window that appeared afterwards — by then the operation needs
`fullscreen_control` and the decision says `mouse_keyboard`. The failure is a
replay refusal naming both categories, which is far more useful than "not
permitted".

Full screen is tested **geometrically** (does the window cover its monitor),
not by the full-screen style bit, because a borderless window covering the
screen behaves like full screen to the user and a maximised window carries a
border the monitor does not.

---

## 3. What is refused outright

These are refusals, not questions. No trust level and no dialog unlocks them,
and that is the point — a confirmation the Body would refuse anyway is not worth
showing.

| Refusal | Why |
|---|---|
| `Ctrl+Alt+Del`, `Ctrl+Shift+Esc`, `Ctrl+Alt+Backspace` | Reaching for the secure-attention sequence means trying to get past a boundary the user can see. |
| `Ctrl+Shift+Q` | ZUZZ's own emergency stop. An agent that can synthesise the stop can stop itself, and a stop it can cause is not a stop. |
| `rm -rf /`, `format C:`, `curl … \| sh`, `sudo …`, `cat ~/.ssh/id_rsa` | Typing has no target. ZUZZ cannot see which window has focus, so the text is judged on its own content. |
| `cmd.exe /c`, `powershell.exe -enc`, `regedit.exe`, … | A whitelist entry that opens a shell would defeat the whitelist. |
| Killing `lsass`, `csrss`, `winlogon`, `svchost`, PID 0, PID 4 | The machine, not the user. |
| Driving a login or credential window | Windows will not let a normal process type into one; ZUZZ says no first. |
| Driving an elevated window from a non-elevated process | ZUZZ never requests elevation for itself, so the answer is an explanation. |
| A coordinate on no attached monitor | Refused, never clamped. Clamping turns "off the edge" into a click in the corner. |

`Win+L`, `Win+R`, `Alt+F4` and `Win+D` are allowed **after a confirmation that
names the consequence** — "locks the machine immediately", "opens a run box,
which executes whatever is typed into it" — rather than one that names the keys.

---

## 4. Resolution happens before classification

The whitelist maps a name to an executable, and the gate is asked about the
executable:

```
"chrome"  ->  chrome.exe  ->  open_apps (auto from L2)
"skype"   ->  skype       ->  run_safe_commands (asks at L1-L5)
```

An unknown application is **not blocked**. It maps to `run_safe_commands` and
asks at every level. Removing an entry from `apps` removes an auto-approval,
not an ability; blocking is the separate `never_launch` list.

The clipboard is the same idea. A read whose content looks like a credential
reclassifies itself to `access_credentials`, which asks at every level including
5. The heuristic is deliberately loose — a false positive costs one
confirmation, a false negative writes a live credential into the activity log.
**The finding is logged by kind, never by content**: the log says
`api_key`, which is what an auditor needs and cannot itself leak the secret.

---

## 5. The watchdog's desktop signals

`brain/spinal_cord/watchdog.py` gained four additive signals, counted from the
activity log so the watchdog sees what *ZUZZ* did and not what the user did.

| Signal | Limit | Window |
|---|---|---|
| Clicks | 50 | 10s |
| Keystrokes | 500 | 10s |
| Application launches | 10 | 60s |
| Refused hotkeys | 3 | 60s |

The refused-hotkey limit is deliberately above 1: one refusal is the blocklist
working. A run of them is an agent working through a blocklist, and that is what
trips.

**The four action-name sets are mutually exclusive as substrings.** This is not
cosmetic. `"hotkey_blocked"` contains `"key"`, so a refused hotkey used to be
counted as both a refusal *and* a burst of keystrokes — which would let an agent
trip the keystroke signal by having its keystrokes refused. Refusals are
therefore logged as `combo_refused`, sharing no fragment with the other three
sets. `test_the_signal_sets_do_not_overlap` pins it.

---

## 6. Decision-free methods, and why there are three

`Hands.release_held_keys`, `Hands.describe_layout` and `Hands.describe_policy`
take no decision.

- The first two are configuration the Desktop tab shows the user without asking,
  exactly as it shows the trust level without asking.
- The third undoes damage ZUZZ itself caused. A stuck modifier key is a safety
  problem, and requiring a fresh decision to release one would leave the user's
  keyboard in a state they never chose — the operation that would have
  authorised the release has already failed.

The list is closed and `test_public_methods_all_require_a_decision` pins it: the
other 25 public methods must take `decision` as their first parameter, so a new
decision-free method fails a test rather than quietly widening the surface.

---

## 7. Testing

```
tests/test_body_hands.py     92 tests   the attack suite
tests/test_hands_api.py      22 tests   the API flow and the frontend wiring
tests/test_watchdog_desktop.py 21 tests the four new signals
```

The attack tests follow the Step 1 and Step 4 pattern: they **assume the gate
has been bypassed** and hand the Hands a genuinely *allowed* `PermissionDecision`
forged at trust level 5. If the Hands executes anyway, the safety net is gone —
so for every dangerous target it must still refuse, on its own classification.

Nothing in the suite can reach the hardware:

- every test drives `RecordingActuator`, and the recurring assertion is
  `actuator.calls == []` — a refused action left no trace at all;
- `Screens` is given explicit monitors, because on Windows it otherwise reads
  the **developer's real** virtual desktop and the tests would pass or fail
  depending on how many monitors the machine has;
- the API tests replace `app.state.kill_switch` with one confined to `tmp_path`.
  They engage the stop to prove it outranks everything, and the process-wide
  singleton would write its flag to real storage — leaving a stop flag behind
  disables the running assistant.

`pytest` currently reports **726 passed, 2 skipped**. The two skips are the
Windows symlink tests, which need elevation.

### The fail-safe

PyAutoGUI is configured with `FAILSAFE=True` and `PAUSE=0.1`, imported lazily so
that importing the module never needs a display. `FailSafeException` is
translated to `FailSafeTriggered`. This is the third line: it fires even when
ZUZZ asked politely and was told yes, which is why the Desktop tab's stop banner
tells the user that moving the mouse into a corner aborts a stuck action.

---

## 8. Live verification

The automated suite drives a `RecordingActuator`, so it can prove a
refusal happened but never that an allowed action actually moved the
real cursor or typed a real keystroke. `scripts/live_verify_hands.py`
is that missing proof. It is interactive by design: a human decides,
step by step, whether each action happens, because a script that runs
unattended would defeat the point of verifying something that can
move a cursor onto whatever is on screen.

The runner has six phases and 22 steps. Every step prints what is
about to happen and what it changes on the machine, shows the real
`PermissionDecision` the gate issued, and waits for exactly `GO` on
a line. Any other input skips the step — when the screen does not
look right, skipping is the correct answer. Three seconds pause
between steps. One JSONL line per step is appended to
`storage/logs/live_verify_<timestamp>.jsonl`, and screenshots taken
at the key moments land in `storage/logs/live_verify_<timestamp>/`.

Exit codes: `0` every assertion passed, `2` the run finished but an
assertion failed, `1` the run did not complete, `130` you
interrupted it with Ctrl+C.

### 8.1 Prerequisites

**Close:**

- Every window. Phase D clicks the middle of the primary display up
  to 60 times, and whatever is under the cursor receives those
  clicks. A clear desktop is the only safe target.
- Full-screen applications (games, presentations, video players).
  Input into a full-screen window is refused under
  `fullscreen_control`, which would fail Phases B and C for a reason
  that has nothing to do with the hardware.
- Anything with unsaved work. **Do not run this while anything
  unsaved is open.** The run types real keystrokes, clicks the real
  cursor, engages the real emergency stop, and can be interrupted at
  any moment; an interrupted edit is lost work.

**Open:**

- One terminal running the ZUZZ server, from the repo root:
  `python start.py`. The run shares state with the assistant — the
  trust level, the activity log, the audit log and the stop flag —
  so it verifies against the machine the assistant actually drives.
- A second terminal for the runner, also from the repo root.

**Have ready:**

- The reset word `RESET` (Phase E prompts for it).
- The trust confirmation word `CONFIRM` — only needed if you restore
  a level-5 trust level afterwards (section 8.6).
- A clipboard you can afford to lose. Phase F clears it, and whatever
  you copied before the run does not survive that step.

**Confirm the server is running:**

```
curl http://127.0.0.1:8080/api/safety/trust
```

A JSON object comes back; its `"current"` field is the trust level.
If the curl fails, the server is not running — start it and retry.
The runner probes the same endpoint and refuses to start without it.

**Confirm the trust level:** the same response's `"current"` field.
The run needs level 3 or higher — mouse and keyboard actions
auto-approve from level 3, and below that the gate answers `ask`,
which an in-process runner cannot answer. Level 5 is recommended
because it auto-approves everything except the immutable rules,
leaving the refusals as the interesting part. The runner prints the
level it read and stops with the raise command if it is below 3.

### 8.2 Running it

In order, exactly:

```
python start.py
```

```
curl http://127.0.0.1:8080/api/safety/trust
```

Raise the trust level to 5 in the web UI's Safety tab, or:

```
curl -X POST http://127.0.0.1:8080/api/safety/trust -H "Content-Type: application/json" -d "{\"level\": 5, \"confirmed\": true, \"typed_word\": \"CONFIRM\", \"acknowledged_cooldown\": true}"
```

Confirm it took effect (`"current": 5`):

```
curl http://127.0.0.1:8080/api/safety/trust
```

Run the verification, from the repo root, in the second terminal:

```
python scripts/live_verify_hands.py
```

### 8.3 What you should see

Quoted literally. Coordinates, monitor counts and timestamps vary by
machine; the words around them do not. Every step follows the same
shape — a header, what is about to happen, the consequence, the real
decision, the prompt, and the result.

The banner, first:

```
================================================================
 ZUZZ LIVE VERIFICATION — Body/Hands (docs/BODY_HANDS.md, section 8)
================================================================
 THIS MOVES THE REAL CURSOR AND TYPES REAL KEYSTROKES.
 Keep your hands off the mouse and keyboard until the run is done.
 Ctrl+C at any moment: engages the emergency stop, releases
 every held key, and prints how to recover.
 Every step asks before it acts. Type GO to run it; any other
 input skips it, and the skip is recorded like any other outcome.
================================================================
```

Startup guards, then (level 5, one 1920x1080 monitor shown as an
example):

```
ZUZZ server: probing http://127.0.0.1:8080/api/safety/trust
ZUZZ server is running at http://127.0.0.1:8080
trust level reported by the server: 5
kill switch: disengaged
trust level (storage/trust_level.json): 5
 display: primary 1920x1080, 1 monitor(s), scale 1.0
 log: storage/logs/live_verify_20261003T093000.jsonl
 screenshots: storage/logs/live_verify_20261003T093000
----------------------------------------------------------------
 The run has 6 phases and 22 steps, with a 3-second pause
 between steps. Phase D deliberately trips the watchdog: the
 emergency stop will engage for real, and Phase E resets it.
 Press Ctrl+C now if you are not ready.
```

**Phase A — safe mouse, no clicks.** Step 1 reads the position and
records it as the origin; step 2 moves to the screen centre over one
second — watch the cursor travel; step 3 moves back; step 4 reads the
position again and asserts it matches the origin:

```
[Phase A] step 1/4: report-start-position
ABOUT TO: Read the cursor position through the permission gate.
CONSEQUENCE: Nothing moves and nothing is typed. The position is recorded as the origin that Phase F returns the cursor to.
DECISION: read_files target='' trust_level=5 -> auto: trust level 5 auto-approves read_files
Type GO to proceed (anything else skips this step): GO
RESULT: ok — cursor at (512, 384)
```

```
[Phase A] step 2/4: move-to-centre
ABOUT TO: Move the real cursor to the centre of the primary display (960, 540) over 1 second.
CONSEQUENCE: The cursor visibly travels across the screen. This is the live-hardware check: a recording actuator can prove the command was issued but not that a real pointer moved.
DECISION: mouse_keyboard target='960,540' trust_level=5 -> auto: trust level 5 auto-approves mouse_keyboard
Type GO to proceed (anything else skips this step): GO
RESULT: ok — cursor at (960, 540) on monitor 0
  screenshot: storage/logs/live_verify_20261003T093000/phase-a-centre.png
```

**Phase B — scratch Notepad.** Notepad opens — the gate's verdict is
printed above the prompt: at level 3+ the whitelist auto-approves
`notepad.exe` and no confirmation popup appears. The window is
focused, the literal text `ZUZZ live verification test` is typed,
and you confirm you see it. The run never closes Notepad: the text
is the thing being verified, so closing it is left to you.

**Phase C — dangerous hotkey refusal.** The single most important
assertion in the suite, and the only one mocks cannot prove. The
attempt must be refused *before* any key is sent — the Body checks
the combination before it reads the decision:

```
[Phase C] step 1/2: attempt-ctrl-alt-del
ABOUT TO: Ask the Body to press Ctrl+Alt+Del — the secure-attention sequence, reserved by the hotkey blocklist.
CONSEQUENCE: The blocklist must refuse this BEFORE any key is sent. No trust level unlocks it and no decision can, because the Body checks the combination before it reads the decision. If any key reaches Windows, the run's most important assertion has failed.
DECISION: mouse_keyboard target='ctrl+alt+delete' trust_level=5 -> auto: trust level 5 auto-approves mouse_keyboard
Type GO to proceed (anything else skips this step): GO
RESULT: refused (expected) — DangerousHotkeyRefused: alt+ctrl+delete is blocked at every trust level. It is the secure-attention or task-manager sequence, and reaching for it means getting past a boundary the user can see.
PASS: the blocklist fired before the actuator was touched, so no key was sent.
  screenshot: storage/logs/live_verify_20261003T093000/phase-c-after-refusal.png
```

(The message says `alt+ctrl+delete` because the policy compares
combinations order-independently; it is the same combination.) Step 2
reads the focused window and asserts it is *not* the Windows
security screen.

**Phase D — watchdog trigger.** Step 1 picks the click spot — the
centre of the primary display, validated against every monitor — and
asks you to look at the screen and confirm it is empty desktop.
Step 2 clicks up to 60 times; the watchdog's click signal (50 clicks
in 10 seconds) trips partway through and engages the emergency stop
for real. The cursor stops mid-burst and stays stopped. That is the
pass condition:

```
RESULT: ok — 50 clicks sent; watchdog TRIPPED: watchdog: 50 clicks in 10s
  screenshot: storage/logs/live_verify_20261003T093000/phase-d-after-burst.png
```

Step 3 attempts an ordinary cursor read and asserts it comes back as
a kill-switch refusal — the HTTP-409 equivalent. Note the decision is
already `blocked` at this point, because the gate consults the same
on-disk flag the watchdog tripped:

```
[Phase D] step 3/4: verify-stop-engaged
ABOUT TO: Attempt an ordinary cursor read with a freshly built decision.
CONSEQUENCE: While the stop is engaged, every action is refused with a kill-switch refusal — the HTTP-409 equivalent. This proves the stop outranks the decision, which is the whole point of the switch.
DECISION: read_files target='' trust_level=5 -> blocked: kill switch engaged (watchdog): watchdog: 50 clicks in 10s
Type GO to proceed (anything else skips this step): GO
RESULT: refused (expected) — KillSwitchRefused: kill switch engaged since this decision was issued
PASS: an ordinary action was refused while the stop is engaged — the same refusal the API reports as HTTP 409.
```

Step 4 asks what you saw — the cursor freezing mid-burst is what
should have happened.

**Phase E — kill switch reset.** Step 1 reports what a reset requires
right now (the first reset after a stop waits 0 seconds; repeats
within the hour wait longer). Step 2 prompts for the word `RESET` —
the policy's confirmation word, typed and confirmed in one prompt.
Step 3 reads the cursor position again and asserts the switch is
disengaged:

```
[Phase E] step 2/3: type-reset-word
ABOUT TO: Disengage the emergency stop. The policy requires the word 'RESET' typed and an explicit confirmation; this prompt collects both at once.
CONSEQUENCE: The stop flag is removed from both locations and ZUZZ — including the running assistant — is armed again. A wrong word or an unmet cooldown is refused and the stop stays engaged; that refusal is recorded, not fatal.
Type RESET to proceed (anything else skips this step): RESET
RESULT: ok — emergency stop disengaged (1 reset(s) in the last hour)
```

**Phase F — clean shutdown.** Releases every held key, clears the
clipboard, moves the cursor back to the Phase A origin, writes the
audit entry, and prints the summary:

```
================================================================
 SUMMARY — 22 steps | log: storage/logs/live_verify_20261003T093000.jsonl
================================================================
 A  report-start-position              ok        cursor at (512, 384)
 ...
----------------------------------------------------------------
 outcomes: 20 ok, 2 refused (2 expected), 0 skipped, 0 error
 assertions: 6 checked, 0 failed

1. Reset the kill switch (skip this if Phase E already did):
    curl -X POST http://127.0.0.1:8080/api/kill-switch/reset -H "Content-Type: application/json" -d "{\"typed_word\": \"RESET\", \"confirmed\": true}"
2. Release any key the run left held:
    curl -X POST http://127.0.0.1:8080/api/hands/release-keys
3. Clear the clipboard:
    curl -X DELETE http://127.0.0.1:8080/api/hands/clipboard
4. Restore the trust level to its value before the run (it was 5):
    curl -X POST http://127.0.0.1:8080/api/safety/trust -H "Content-Type: application/json" -d "{\"level\": 5, \"confirmed\": true, \"typed_word\": \"CONFIRM\", \"acknowledged_cooldown\": true}"
5. Close the Notepad window Phase B left open — the run never closes it, so the text you verified is still in it.
================================================================
```

A happy run is `outcomes: 20 ok, 2 refused (2 expected)` and
`assertions: 6 checked, 0 failed`, exit code 0. The two expected
refusals are Phase C step 1 (the hotkey) and Phase D step 3 (the
stopped machine). Anything else — `ASSERTION FAILED`, `error`, or an
unexpected `refused` — means the run did not pass; the JSONL log
holds the per-step record, including the decision and the refusal
reason.

One JSONL line, from Phase C step 1:

```json
{"timestamp": "2026-10-03T09:30:07.123456+00:00", "phase": "C", "step": "attempt-ctrl-alt-del", "decision": {"category": "mouse_keyboard", "target": "ctrl+alt+delete", "allowed": true, "requires_confirmation": false, "decision": "auto", "reason": "trust level 5 auto-approves mouse_keyboard", "trust_level": 5, "dry_run": false, "rule_id": null, "description": "mouse_keyboard on ctrl+alt+delete"}, "outcome": "refused", "detail": {"expected": true}, "refusal_reason": "DangerousHotkeyRefused: alt+ctrl+delete is blocked at every trust level. It is the secure-attention or task-manager sequence, and reaching for it means getting past a boundary the user can see.", "summary": "refused, as it must be — alt+ctrl+delete is blocked at every trust level. It is the secure-attention or task-manager sequence, and reaching for it means getting past a boundary the user can see."}
```

### 8.4 If something unexpected happens

| Symptom | What it means | What to do |
| --- | --- | --- |
| The refused hotkey reached Windows anyway (a Windows Security screen appeared) | The blocklist failed — the run's critical assertion. This should be impossible: the Body checks the combination before the decision, before the actuator, before any key exists. | The run stops itself at that step (`CRITICAL`, exit code 1 or 2). Press `Ctrl+Shift+Q` — ZUZZ's human emergency stop — yourself, dismiss any security screen with `Esc`, then reset the stop (section 8.6). Do not re-run; report the finding with the JSONL log, the screenshot and the exact step. |
| The watchdog tripped too early (or never tripped) | The watchdog counts clicks in the shared activity log (`storage/logs/activity_log.jsonl`) inside a rolling 10-second window. A previous burst, or another ZUZZ process clicking, adds to the count; a slow machine can also spread 60 clicks past the window so the count never reaches 50. | Wait 10 seconds and re-run so the window is empty. If it never trips, check nothing else is clicking (another terminal, another agent) and that the machine is fast enough for 50 clicks inside 10 seconds. The failed assertion is recorded; the machine is safe either way. |
| The confirmation popup did not appear | At trust level 3+ no popup appears for this run's actions — they auto-approve, which is why the run requires level 3+. Popups (HTTP 202 with a `confirmation_id`) only appear when the gate answers `ask`: level 1-2, or `fullscreen_control`. If you lowered the level to watch one and saw nothing, the response may have been a plain refusal (403), the confirmation may already be consumed, or the server may have restarted — the confirmation store is in-memory only, and a restart clears every pending prompt. | Check the level with `curl http://127.0.0.1:8080/api/safety/trust`. To answer a real 202, re-POST the same operation with `"confirmed": true` and the `confirmation_id` the 202 returned. The runner itself refuses to start below level 3, so it never sits waiting for a popup it cannot answer. |
| The kill switch will not reset (cooldown / wrong confirmation word) | Two causes. Wrong word: the policy needs exactly `RESET` (case-insensitive) typed *and* an explicit confirmation — the API needs both `"typed_word": "RESET"` and `"confirmed": true` in the body; a missing one is refused with `type RESET to confirm the reset`. Cooldown: resets within one hour climb `0s, 5s, 30s, 60s`, refused with `wait Ns before resetting again`. | Read the refusal message — it names which of the two it is. For a cooldown, wait the stated seconds and retry. A refused reset leaves the stop engaged: ZUZZ stays stopped until a reset succeeds, which is the safe direction. |
| Stuck modifier keys | The run releases every held key on every exit path — step completion, error abort and Ctrl+C all call the decision-free `release_held_keys()`. A modifier still held afterwards was not held by the run. | Press the modifier physically once, or `curl -X POST http://127.0.0.1:8080/api/hands/release-keys` (decision-free, always accepted), or press `Ctrl+Shift+Q` to stop everything. |
| PyAutoGUI failsafe triggered (`FailSafeTriggered`) | PyAutoGUI aborts when the cursor reaches a screen corner. The step is recorded `refused` and nothing further is sent. The run's own targets are the desktop centre, never a corner, so this only happens if you moved the cursor during the run. | Move the cursor to the middle of the screen, keep hands off the mouse and keyboard, and re-run. |

### 8.5 Aborting mid-verification

`Ctrl+C` at any moment — at a prompt, during a step, during the
three-second pause. The handler does three things, in this order:

1. Triggers the real kill switch (source `ui`, reason
   `live verification interrupted by Ctrl+C`, last action = the step
   in flight). This is the same on-disk flag the assistant reads, so
   the running assistant stops too.
2. Releases every held key.
3. Prints the recovery command.

The handler is written so that it cannot itself raise: the stop and
the key release happen before a single line is printed, and every
call inside is guarded.

**State left behind:** the stop flag on disk (`storage/stop.flag` and
a copy in the system temp directory), so a restarted server comes back
up *stopped* until the flag is reset; the mouse where it stopped; the
clipboard untouched unless Phase F had already run; the JSONL log
holding every step up to the interrupt; and the audit log holding the
stop event, which the switch itself writes when it triggers. Exit
code `130`.

**Recover:** reset the stop (section 8.6, command 1), then either
re-run the script from the beginning or finish the remaining phases
by hand.

### 8.6 Re-engaging every safety guard afterwards

In order, copy-pasteable (Windows command syntax; the JSON bodies
carry the confirmation evidence each guard requires):

```
curl -X POST http://127.0.0.1:8080/api/kill-switch/reset -H "Content-Type: application/json" -d "{\"typed_word\": \"RESET\", \"confirmed\": true}"
```

```
curl -X POST http://127.0.0.1:8080/api/hands/release-keys
```

```
curl -X DELETE http://127.0.0.1:8080/api/hands/clipboard
```

Restore the trust level to its value before the run. The body
depends on the level, mirroring `config/trust_levels.json`: level 1
is instant, levels 2-4 need an acknowledged confirmation, and level
5 additionally needs the `CONFIRM` word and an acknowledged
cooldown:

```
curl -X POST http://127.0.0.1:8080/api/safety/trust -H "Content-Type: application/json" -d "{\"level\": 1}"
```

```
curl -X POST http://127.0.0.1:8080/api/safety/trust -H "Content-Type: application/json" -d "{\"level\": 3, \"confirmed\": true}"
```

```
curl -X POST http://127.0.0.1:8080/api/safety/trust -H "Content-Type: application/json" -d "{\"level\": 5, \"confirmed\": true, \"typed_word\": \"CONFIRM\", \"acknowledged_cooldown\": true}"
```

Close the scratch Notepad the run left open.

Confirm every guard took effect:

```
curl http://127.0.0.1:8080/api/kill-switch
```

`"engaged": false` and `"reset_instruction": ""` mean the stop is
reset. If the reset is refused with a cooldown, wait the stated
seconds and repeat command 1.

---

## 9. Operating notes

- **Edits to `config/apps_whitelist.json` take effect immediately.** The file is
  re-read on every lookup. The whitelist is a convenience, not a security
  boundary: it removes prompts, it does not grant access.
- **DPI scale is reported, never applied.** PyAutoGUI reports physical pixels and
  the OS delivers pointer input in the same space. Scaling the coordinates would
  put every click in the wrong place on a scaled display.
- **Closing is not killing.** `close_app` posts `WM_CLOSE` so the application can
  save, prompt or cancel. `kill_process` terminates immediately. Both land in
  `close_apps`, which asks at every level.
- **A fragment matching two windows is refused**, not resolved by taking the
  first. Guessing which of two windows the user meant is how input lands
  somewhere they did not choose.