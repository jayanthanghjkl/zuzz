# Resource Budget

How much of this machine ZUZZ is allowed to take, and why the number is a hard
cap rather than a suggestion. The figures live in `config/resource_limits.json`
and `config/models.json`; the enforcement lives in `brain/hypothalamus/`.

- Implementation: `brain/hypothalamus/`
- API: `face/api/routes_resources.py`
- Permission flow: `face/api/resource_confirmations.py`
- Config: `config/resource_limits.json`, `config/models.json`,
  `config/thermal_limits.json`
- Numbers in this document were measured on **2026-10-03** on the development
  machine, not estimated. Section 1 says which reading each number came from.

---

## 1. The machine these numbers were measured on

Read from NVML and psutil on 2026-10-03, on the machine ZUZZ actually runs on:

| Reading | Value |
|---|---|
| GPU | NVIDIA GeForce RTX 4050 Laptop GPU |
| Total VRAM | 6,439,305,216 bytes — **6141.0 MiB** as NVML reports it |
| Driver version | 616.56 |
| VRAM source | `pynvml` |
| GPU temperature at rest | 50 °C |
| CPU temperature | **unreadable** — psutil 7.2.2 on Windows has no `sensors_temperatures` at all |
| System RAM | 16,068.7 MiB |
| Agent RAM budget | 3072 MB (`MAX_AGENT_RAM_MB`) |
| Agent RSS with the resource layer imported | 51.0 MB — 1.7% of that budget |
| Battery | 80%, on AC |
| Integrated GPU | no — `Hypothalamus.calibrate()` returns `integrated_gpu: False` |

Three of those rows matter more than they look.

**The card is a discrete GPU, and it is a laptop.** Discrete means the
integrated-GPU reduction in `calibrate()` does not fire here — it only triggers
on a name containing `intel`, `uhd`, `iris`, `apple`, `mali`, `adreno` or `vega`
(`hypothalamus.py:152-158`), and the RTX 4050 matches none of them. Laptop means
the battery path is live: below 20% on battery the power manager forces idle mode
and refuses performance mode, because a machine that shuts down mid-task is how
work is lost.

**The card reports 6141 MiB, not the nominal 6144.** 6144 MiB is the number the
card is sold as; 6,439,305,216 bytes is what the driver answers. The budget is a
fraction of the physical card, so it is worth being precise about which of the two
it is a fraction of.

**The CPU has no temperature sensor to read.** This is normal on Windows:
`psutil.sensors_temperatures()` does not exist in the 7.2.2 Windows build, so
`ThermalGuardian._read_cpu_temp` returns `None` (`thermal_guardian.py:142-157`).
A live `check()` on this machine returns:

```
{'gpu_temp': 50, 'cpu_temp': None, 'level': 'normal',
 'action': 'restore', 'sensors': {'gpu': True, 'cpu': False}}
```

The CPU channel is excluded from the comparison rather than treated as 0 °C, and
the level stays `normal` because the GPU channel is working. `unmonitored` is a
whole-region state that only appears when *every* sensor is missing — one missing
sensor is reported in `sensors` and skipped.

---

## 2. The budget in one table

| Line | Value | Source |
|---|---|---|
| Hard cap on model VRAM | **4608 MB** | `resource_limits.json` → `vram.total_budget_mb`; `MAX_VRAM_MB` overrides |
| Reserved headroom | **1500 MB** | `vram.reserved_headroom_mb`; `VRAM_HEADROOM_MB` overrides |
| Usable for models | **3108 MB** | `total - headroom`, clamped at zero (`vram_budget.py:67`) |
| Free for a new load | `3108 - allocated` | `vram_budget.py:75`, clamped at zero |
| Auto-unload threshold | 85% of the cap | `vram.auto_unload_percent`, read at `model_manager.py:646` |
| Agent RAM budget | 3072 MB | `MAX_AGENT_RAM_MB`, default literal at `resource_monitor.py:96` |

Precedence for both VRAM numbers is constructor argument → environment variable →
config file → literal default in the code (`vram_budget.py:44-53`). `env_int`
returns `0` for an unset variable, so a missing `MAX_VRAM_MB` falls through to
the config rather than to zero. `.env.example:40` sets `MAX_VRAM_MB=4608`, so
env and config agree today.

Both `usable_mb` and `available_for_load` clamp at zero rather than going
negative. A negative number reads as "room" to anything that compares it without
checking the sign, and the safe direction for a cap is zero room.

### Does 4608 MB agree with the 4.5 GB rule?

`project.md` §3, line 115: `RULE: Never exceed 4.5 GB VRAM (leave 1.5 GB
headroom)`. `config/resource_limits.json` says `total_budget_mb: 4608`.

**They are the same budget, and the units are what differ.** 4608 MiB is
4.5 × 1024 exactly — 4.5 GiB. The configured cap is the 4.5 GB rule read in
binary units, and the 1500 MB headroom is the 1.5 GB the same rule reserves.

Read as decimal gigabytes the config would be 4608 MB = 4.608 GB, which is 108 MB
over the written rule. Nothing in the code treats the figure as decimal and
nothing enforces 4.5 decimal GB, and the rest of the project already reads "6 GB"
as this card's 6141 MiB (`project.md:102`), so the binary reading is the
consistent one. Stating it plainly rather than rounding it away: the two numbers
agree only under the GiB reading, and that is the reading this project uses
everywhere else.

### Why 4608 and not more

4608 is 6144 × 0.75 — three quarters of a nominal 6 GiB card, chosen as a round
fraction so the number reads as a proportion of the hardware rather than as a
measurement of it. It is **not** derived from measuring what the Windows desktop
actually needs; nothing in this repository measures that, and the honest
statement is that 1500 MB of headroom is a judgement, not a reading.

The headroom could in principle go lower. 6141 measured minus 4608 leaves 1533
MiB, and the configured headroom is 1500 — so the region plans for 6108 MiB and
leaves 33 MiB of the card unclaimed. That 33 MiB belongs to nobody: it is not
allocated, not counted, and not available. It is the small price of rounding the
cap down to a memorable fraction.

Going the other way, the cap could rise to about 4640 MB before it started eating
the declared headroom. It is not raised, because the headroom is the only thing
standing between a model load and a compositor that has been paged out — see
section 4 — and 33 MiB is not worth spending on it.

---

## 3. Per-model profiles

From `config/models.json` → `_models`, with the verdict `can_load` actually
returned against the live budget in measured mode with nothing loaded:

| Model | VRAM MB | RAM MB | Priority | Role | Idle timeout | Fits 3108 MB? |
|---|---|---|---|---|---|---|
| `qwen2.5-coder:1.5b` | 1200 | 500 | `always_loaded` | `fast_responses` | none — never reaped | yes |
| `moondream` | 1600 | 500 | `on_demand` | `vision` | 60 s | yes |
| `qwen2.5-coder:3b` | 2200 | 700 | `on_demand` | `complex_responses` | 60 s | yes |
| `qwen2.5-coder:7b` | 4200 | 1200 | `on_demand` | `deep_reasoning` | 120 s | **no** |

The 7B's refusal is the point of the table, not a gap in it:

```
qwen2.5-coder:7b needs 4200MB and only 3108MB is free after the
1500MB headroom; route to the cloud or unload something first
```

And the two that do fit cannot be combined with much:

| Combination | MB | Fits? |
|---|---|---|
| `1.5b` + `moondream` | 2800 | yes — 308 MB to spare |
| `1.5b` + `3b` | 3400 | no |
| `3b` + `moondream` | 3800 | no |
| the three cheapest, `1.5b` + `moondream` + `3b` | 5000 | no |

So the machine holds **two** models, and the second one has to be the vision
model. The 7B cannot be resident at all — not alongside anything, and not alone
through a swap: `hot_swap` releases the outgoing model's slot before judging the
incoming one, and 4200 MB still exceeds the 3108 MB usable
(`model_manager.py:559-569`). `config/models.json:73` records the intent as
*"Falls back to cloud when 4.5GB budget is insufficient"* — on this machine that
is not a fallback path, it is the only path for that model.

The `always_loaded` model has no idle timeout because it is skipped by the reaper
regardless (`model_manager.py:517-518`): a model the user chose to always have
has no idle window to breach. It also cannot be evicted by memory pressure or by
thermal cloud-only mode (`model_manager.py:461-466`), and unloading it by request
needs trust level 3 or above (`model_manager.py:354-365`).

---

## 4. Why headroom is reserved and never allocated

The 1500 MB is not slack. It belongs to the Windows desktop compositor, the
browser, the driver and the swap file, and ZUZZ has no authority over any of
them. A model that takes the last of VRAM does not merely fail — it takes the
compositor's memory with it, and the desktop starts stuttering, then blanking,
then paging to system RAM. On a machine that also carries the user's actual work,
that is a failure ZUZZ caused and cannot undo.

The direction of the clamp is the argument. `usable_mb` is `total - headroom`,
and `available_for_load` is `usable - allocated`, both floored at zero. Headroom
is subtracted before any question of whether a model fits is asked, so there is
no arithmetic path in which a load can consume it. A budget that published a
"total" models were allowed to reach and then applied the reserve afterwards
would be a budget with a footnote.

The second reason is that a Windows machine which starts paging **feels broken**.
It is not a measurable failure — nothing raises an exception, every call still
returns — but the UI stops responding to input while the cursor still moves, and
the user's conclusion is that ZUZZ is broken. Keeping the desktop out of swap is
worth more VRAM than any model in the catalogue would have been worth.

---

## 5. Why the cap is hard, and enforced before the load

Running out of VRAM mid-inference does not just fail the request. It crashes the
model, and on Windows it can take the GPU driver down with it — which takes the
user's whole desktop with it, every other application included. That is a
different class of failure from "the answer was slow", and it is the reason the
budget is a hard cap rather than a target.

So the check happens before the model is touched, not after:

- `can_load()` (`model_manager.py:222-274`) answers with a reason instead of
  raising, because the usual caller is a router choosing between local and cloud
  and it routes better with an explanation than with an exception.
- `load()` (`model_manager.py:369-430`) reserves the budget **before** the
  transport is called, and releases it if the load fails. Two callers each
  passing the check separately could both see room for one model and together
  breach the cap, so the check and the assignment are one critical section under
  a single lock.
- A failed reservation is released (`model_manager.py:416`). Leaving it behind
  would leak budget no model is using, and the leak would accumulate into a
  refusal that looks like a full disk.

The error hierarchy carries meaning rather than collapsing to one failure:
`BudgetExceededError` maps to HTTP 429 — the system is healthy and full, and the
caller may wait, drop a model, or route to the cloud — while
`ModelLoadError` maps to 503, "the backend refused" (`resource_confirmations.py:56-97`).
A 500 for a full budget would train the user to ignore the one status that means
something.

---

## 6. Measured versus estimated, and why the label is not optional

`Hypothalamus.calibrate()` decides once at startup whether VRAM figures are
measured or estimated, because the answer is a property of the machine rather
than of the moment (`hypothalamus.py:116-150`).

| Reporting | When | What the number is |
|---|---|---|
| `measured` | `pynvml` or `GPUtil` answered | A real sensor reading the card |
| `estimated` | neither library available | 1.5 × the documented footprint (`CONSERVATIVE_VRAM_FACTOR`, `model_manager.py:82`) |

Conservative mode errs high deliberately. An over-estimate costs one load that
might have fitted; an under-estimate performs the load the cap exists to prevent.
`calibrate()` records the reason in `degraded` rather than failing to start — a
resource monitor that refuses to boot without an NVIDIA driver has just taken the
agent offline.

The consequence is not cosmetic. On this machine in **estimated** mode the
budgets become:

| Model | Measured | Estimated (×1.5) | Verdict flips? |
|---|---|---|---|
| `qwen2.5-coder:1.5b` | 1200 | 1800 | still fits |
| `moondream` | 1600 | 2400 | still fits |
| `qwen2.5-coder:3b` | 2200 | 3300 | **fits → refused** |
| `qwen2.5-coder:7b` | 4200 | 6300 | refused either way |

The 3B is the interesting one. A budget shown as "2200 MB, fits" on a machine
that can only estimate would be a budget the user trusts further than it deserves,
and the failure it hides is a 3B that silently routes to the cloud and bills the
user for it. So the mode is not a debug field: `reporting` is returned by
`GET /api/resources/vram`, `GET /api/models/loaded`, the live WebSocket snapshot
and every `can_load` verdict (`routes_resources.py:110`, `:184`;
`model_manager.py:243`; `hypothalamus.py:198`). The UI is expected to label the
gauges from it. An estimate in the same typeface as a measurement is a lie by
omission.

---

## 7. The KV cache: a loaded model is not a static cost

`vram_mb` in `config/models.json` is the loaded-and-idle footprint. It is not
what the model costs while it is working. The KV cache grows with context length
during a long inference, so the predictor charges a `kv_factor` on top of the
weights (`resource_predictor.py:35-41`):

| Task type | Weights | `kv_factor` | KV cache | Dynamic | Upper bound (assumed) | Route |
|---|---|---|---|---|---|---|
| `fast_responses` / `chat` | 1200 | 1.10 | +120 | 1320 | 1518 | local |
| `vision` | 1600 | 1.15 | +240 | 1840 | 2116 | local |
| `complex_responses` | 2200 | 1.20 | +440 | 2640 | 3036 | local |
| `deep_reasoning` | 4200 | 1.25 | +1050 | 5250 | 6037.5 | **cloud** |

Charging the static figure alone would under-count every long inference by 10–25%.
That is why `will_task_fit` compares against the **upper** end of the prediction
interval rather than the mean (`resource_predictor.py:162-207`): routing on the
mean is how a task that "should" have fitted breaches the cap halfway through
generating.

Two honest details about this table. With no observations recorded, every estimate
carries `confidence: 0.0` and `source: "assumed"`, with a ±15% band
(`ASSUMED_HALF_WIDTH_FRACTION`). Confidence rises with sample count *and*
stability — 40 observations with a wide spread is still a poor estimate — and
never reaches 1.0, because a model that reports certainty about a resource
estimate is claiming something it cannot know. And `chat` resolves to no model at
all: no profile in `config/models.json` carries `role: "chat"`, so the predictor
falls back to the generic chat estimate. Level 2 routing sits 72 MiB under the
line at zero observations, which is the margin a load has before it is refused.

`predict_ram_for_task` judges host RAM against the RAM budget in
`config/resource_limits.json` — 3072MB total less 1024MB reserved, so 2048MB
usable — rather than against the VRAM cap. The two pools are unrelated, and on
this machine the card is *smaller* than the declared RAM budget, so the old
comparison would have called almost every task oversized. The verdict is
published as `ram_budget` in `/api/resources/snapshot`, beside the host reading
it has to be compared against: an ordinary task is 500MB, 24.4% of the 2048MB
usable, and the host has 16069MB total. A prediction nothing reports is a
prediction nobody can act on.

---

## 8. Model size against speed, on this hardware

Quality claims are the judgement of the author, not a measurement; the VRAM,
timeout and route columns are read from the code and verified against the live
budget.

| Model | VRAM MB | Quality | When it should load | Idle timeout | Load timeout |
|---|---|---|---|---|---|
| `qwen2.5-coder:1.5b` | 1200 | Good for short factual and edit work; loses long chains of reasoning | Always — this is the one the user should never wait for | never reaped | 120 s first load, 10 s cached |
| `moondream` | 1600 | The only vision path; no alternative locally | On the first request that carries an image | 60 s | 120 s / 10 s |
| `qwen2.5-coder:3b` | 2200 | Materially better on multi-file edits and refactors | On the first genuinely complex request | 60 s | 120 s / 10 s |
| `qwen2.5-coder:7b` | 4200 | Best of the four, and unreachable | Never, on this machine | 120 s | — |

The load timeout is split because a model already resident in the OS page cache
loads in seconds and one that has to fault a multi-gigabyte weight file off disk
does not (`FIRST_LOAD_TIMEOUT_SECONDS = 120`, `CACHED_LOAD_TIMEOUT_SECONDS = 10`,
`model_manager.py:73-77`). A single 10-second timeout would fail first loads on a
laptop whose SSD is busy; a single 120-second timeout would make the UI look hung
on every swap.

Hot-swap is atomic: the incoming model loads first, and the outgoing one is only
evicted once the incoming one is resident (`model_manager.py:533-626`). If the new
model fails to load, the old one is put back and the caller is told so. A swap
that leaves the user with neither model has made the failure worse than the
failure was.

---

## 9. Storage: when the external HDD earns its place

`project.md:104` lists a 1 TB external HDD as plug-in hardware. It is not used
for models, and the reason is not storage — 70 GB free on the SSD is plenty for
~5 GB of weights. The reason is **time**: a 7B-class weight file over USB makes
the first load a matter of minutes rather than seconds (an estimate, not a
measurement — no HDD-backed load has been run on this machine), and the manager
gives a first load 120 seconds before it gives up. A model that reliably misses
that is worse than a smaller model that answers.

The correct use is the one `project.md` already assigns it: cold storage for
weights ZUZZ is not using, fetched on demand, and memory backups
(`project.md:312`, and `scripts/backup_memory.py` at `project.md:768`).

This is deferred, and the deferral is a decision rather than a gap in the code.
`project.md:785` lists Phase 9 as "Polish + Auto-start + Dashboard + Testing",
and the HDD does not appear there — the only role the document gives it is memory
backup. So the honest statement is that HDD-backed model storage is **not yet
scheduled** in `project.md`, and Phase 9 is where a storage decision belongs if
one is wanted. Until then the answer to "which models do I keep?" is the budget in
section 3, and the answer is two.

---

## 10. Upgrades, and what would change

The budget is configuration, not code. Nothing in `brain/hypothalamus/` hardcodes
4608 or 1500 except as a last-resort default behind the config file
(`vram_budget.py:47`, `:51`), so a bigger card is a config edit.

| Upgrade | Change | What it buys |
|---|---|---|
| 8 GB VRAM card | `total_budget_mb` → ~6144, headroom stays 1500 | The 7B becomes resident (4200 ≤ 4644 usable); three-model combinations become possible |
| 12 GB VRAM card | `total_budget_mb` → ~9216 | The 7B coexists with the 1.5B; a 13B profile becomes arguable |
| 32 GB RAM | `MAX_AGENT_RAM_MB` → 4096+ | Room for Playwright and a second agent process without approaching the Windows floor |
| More RAM, not more VRAM | `ram_mb` per profile stays as-is | `ram_mb` is host process overhead, not memory-bandwidth-bound; a bigger card does not change it |

Two things to change alongside any of these, or the numbers will quietly disagree
with the hardware: `project.md` §3's 4.5 GB rule, which is the stated ceiling the
config is derived from, and `config/models.json`'s `_comment` on the 7B, which
currently documents a fallback to the cloud on this machine.

What does **not** change: the headroom. A bigger card gets a proportionally larger
desktop to keep responsive, and the 1500 MB that keeps the compositor out of swap
is not a number that should scale with the card.

---

## 11. The pressure ladder

All four percentages are read. Together they form an escalating response, because
eviction alone does not cover every way a card gets into trouble:

| Key | Value | Read? | Effect |
|---|---|---|---|
| `vram.warn_percent` | 75 | **yes** — `VRAMBudget.pressure_tier` | advisory band, reported as `budget.tier` |
| `vram.auto_unload_percent` | 85 | **yes** — `memory_pressure_handler` | evict on-demand models, longest-idle first |
| `vram.emergency_cloud_percent` | 95 | **yes** — `memory_pressure_handler` | hold off on new local loads |
| `vram.panic_percent` | 98 | **yes** — `memory_pressure_handler` | force cloud-only |
| `ram.total_budget_mb` | 3072 | **yes** — `predict_ram_for_task` | RAM verdict total |
| `ram.reserved_headroom_mb` | 1024 | **yes** — `predict_ram_for_task` | subtracted, so 2048MB usable |
| `integrated_gpu_overhead_percent` | 25 | **yes**, only on an integrated GPU (`hypothalamus.py:162-167`) | inflated estimate |

Above 85%, on-demand models are evicted longest-idle-first, core models are
skipped, and the eviction is written to the audit log as a `dangerous` event
because the system changed what is loaded with no decision behind it
(`model_manager.py:628-691`). A pressure response that left the user with nothing
is a worse failure than the pressure was, so the core model is never a candidate.

The two tiers above it exist for the failures eviction cannot fix. Freeing models
on a card that is still nearly full only invites the next request to refill it, so
above 95% new local loads are held off while what is resident stays put. And a
card with nothing left to evict needs local inference switched off rather than
attempted and failed inside the driver, so at 98% the region goes cloud-only
outright and says so in the audit log.

`pressure_tier()` is what the snapshot and status payloads report as `tier`, so
the bands the dashboard colours and the bands the eviction logic acts on are read
from the same thresholds and cannot drift apart.

---

## 12. Operating notes

- **Loading a model is a gated operation.** `manage_models` is `ask` at trust
  levels 1–2 and `auto` from level 3 (`config/trust_levels.json:30`). A
  model-management call with no decision raises `DecisionRequiredError` rather
  than proceeding, so the gate cannot be skipped by forgetting a parameter
  (`model_manager.py:292-305`).
- **Protective evictions are exempt from the gate and from nothing else.** Idle
  timeout, memory pressure and thermal cloud-only unload without a decision,
  because requiring a prompt to relieve memory pressure means the prompt appears
  at the moment the system can least afford to wait for one. They still cannot
  touch a core model, and they are still audited (`model_manager.py:432-496`).
- **Ten swaps in 60 seconds is normal**; more than ten is a pattern worth scoring.
  The threshold is strictly greater than, because flagging a busy but ordinary
  conversation trains the operator to ignore the flag. Above it the burst is
  written to the audit log as `threat_assessed` with the Amygdala's score
  (`model_transport.py:148-196`, `model_manager.py:834-864`). Crossing the
  threshold *is* the detection and the Amygdala supplies severity only: it scores
  patterns in the target text, and a model name matches none, so gating the record
  on its agreeing the burst was dangerous made the record unreachable.
- **Read-only routes are not gated.** Showing how much memory is free is the same
  kind of read as showing the trust level, and a gauge that needed a permission
  would be a gauge nobody looked at (`routes_resources.py:13-15`).
- **The live socket carries readings only.** Nothing published on
  `/api/resources/live` can load a model or change a power mode, which is what
  makes it safe to leave open all day (`routes_resources.py:144-174`).
- **Cloud-only mode lifts only at `normal`.** Not merely below the critical line:
  re-enabling local inference at 88 °C would trip the guardian again within
  seconds, and a flapping safety state is worse than one that stays engaged
  (`hypothalamus.py:329-342`).