# Phase 3 Plan — Vision, Browser, Spatial Awareness

**Status: DRAFT for Jayanthan approval.**

Builds on the safety core completed in Phase 2. Adds three new components that
let ZUZZ see, browse, and understand context — all behind the same permission
gate and audit log.

---

## 1. Scope

| Component | Purpose | Folder | Est. LOC |
|---|---|---|---|
| Occipital Lobe | Screen vision, OCR, UI element detection | `brain/occipital_lobe/` | ~400 |
| Body/Legs | Browser automation via Playwright | `body/legs/` | ~700 |
| Parietal Lobe | Spatial/system awareness | `brain/parietal_lobe/` | ~300 |
| face/api/routes_vision.py | Vision API endpoints | `face/api/` | ~250 |
| face/api/routes_browser.py | Browser API endpoints | `face/api/` | ~400 |
| face/frontend/pages/vision.html + js | Vision UI | `face/frontend/` | ~500 |
| face/frontend/pages/browser.html + js | Browser UI | `face/frontend/` | ~500 |
| | | **Total** | **~3,050** |

Excluded from Phase 3: voice, memory, multi-agent orchestration.

---

## 2. Architectural Invariants

### Carried forward from Phase 2

1. **PermissionGate checks every action.** No action reaches the Body layer without a permission decision.
2. **Amygdala scores every action.** A threat score can only raise scrutiny, never lower it.
3. **Spinal Cord runs separate process.** A crash or hang in the brain must not disable the kill switch.
4. **Activity log + audit log for everything.** Every action, every denial.
5. **`immutable_rules.py` read-only**, SHA-256 verified.
6. **AI cannot change own trust level.** Default trust level: 1.
7. **Fail-closed semantics.** The kill switch outranks every other decision.

### New for Phase 3

8. **Vision results don't bypass the permission gate.** A screenshot is data; acting on it still needs a decision.
9. **Browser actions respect the same trust matrix.** `browse_internet` and `web_automation` are new categories with the same L1-ask / L5-auto progression.
10. **Screenshots are classified before storage.** A screen shot is tagged `safe` / `caution` / `sensitive` by the classifier before it is saved.
11. **Browser cannot access localhost without permission.** `localhost`, `127.0.0.1`, and `*.local` are blocked unless the user explicitly allows them.
12. **OCR text input is treated as untrusted.** Text read from a screenshot is scored by the Amygdala before it reaches reasoning.
13. **Vision prompts are isolated from reasoning prompts.** The Occipital Lobe's prompt template is separate from the Cerebrum's; a screenshot cannot inject instructions into the reasoning chain.

---

## 3. Occipital Lobe Design

### Integration

Moondream via Ollama (already pulled in Phase 1). Screenshot capture via `mss` (fast, cross-platform). OCR backup via `pytesseract` if Moondream confidence is low.

### Interface

```python
class OccipitalLobe:
    def describe_screen(self, monitor: int = 0) -> str:
        """Return a natural-language description of the current screen."""

    def find_element(self, description: str, monitor: int = 0) -> Dict[str, Any]:
        """Return coordinates for a UI element matching the description."""

    def read_text_from_region(self, x: int, y: int, w: int, h: int) -> str:
        """OCR the given screen region."""

    def verify_action_result(self, before: bytes, after: bytes) -> bool:
        """Return True if the screen changed as expected."""

    def classify_screen_content(self, image: bytes) -> str:
        """Return 'safe', 'caution', or 'sensitive'."""
```

### Design decisions

- **Screenshot cache:** repeated `describe_screen` calls within 2s return the cached image.
- **Confidence threshold:** `find_element` returns `None` when Moondream confidence < 70%; the caller must ask the user.
- **Region limit:** `read_text_from_region` refuses regions larger than 1920×1080 to avoid feeding the entire screen to OCR.
- **Before/after:** `verify_action_result` captures the screen before and after an action; if the diff is empty the action is recorded as no-op.

---

## 4. Body/Legs Design

### Integration

Playwright (chromium default). Browser lifecycle managed by `Body/Legs`. Downloads routed through `Body/Fingers` for safety classification.

### Interface

```python
class Legs:
    def open_browser(self, headless: bool = False) -> str:
        """Launch a browser session. Returns session_id."""

    def navigate(self, session_id: str, url: str) -> bool:
        """Navigate to a URL. Returns True on success."""

    def click_element(self, session_id: str, selector_or_text: str) -> bool:
        """Click an element by CSS selector or visible text."""

    def fill_form(self, session_id: str, data: Dict[str, str]) -> bool:
        """Fill form fields by name or placeholder text."""

    def extract_text(self, session_id: str, selector: str) -> str:
        """Return the text content of a CSS selector."""

    def extract_links(self, session_id: str) -> List[Dict[str, str]]:
        """Return all links on the current page."""

    def download_file(self, session_id: str, url: str) -> str:
        """Download a file via Fingers. Returns the saved path."""

    def close_browser(self, session_id: str) -> bool:
        """Close the browser session and clean up."""

    def screenshot(self, session_id: str) -> bytes:
        """Return a PNG of the current page (via Occipital)."""
```

### Design decisions

- **Headless default:** `False` (visible). The user sees what the browser does.
- **Session isolation:** each `open_browser` call gets a fresh context; cookies are not shared between sessions unless the user approves.
- **JavaScript:** enabled by default on trusted domains; default-deny on untrusted domains.
- **Download routing:** all downloads go through `Body/Fingers` `write` method, which applies the file classifier and the trust matrix.
- **Tab limit:** 5 tabs per session. Exceeding returns `TooManyTabsRefused`.

---

## 5. Parietal Lobe Design

### Integration

`pygetwindow` for active window detection. `psutil` for running apps and resource usage. Mouse position via `Body/Hands` state.

### Interface

```python
class ParietalLobe:
    def get_active_window(self) -> Dict[str, Any]:
        """Return the currently focused window info."""

    def get_screen_info(self) -> Dict[str, Any]:
        """Return resolution, DPI, and monitor layout."""

    def get_running_apps(self) -> List[Dict[str, Any]]:
        """Return running processes with memory and CPU usage."""

    def get_mouse_position(self) -> Tuple[int, int]:
        """Return the current cursor coordinates."""

    def detect_user_context(self) -> str:
        """Return 'coding', 'browsing', 'writing', 'reading', or 'unknown'."""
```

### Design decisions

- **Context hints improve routing:** the Thalamus can prefer a coding-focused model when `detect_user_context()` returns `"coding"`.
- **Resource-aware scheduling:** the Hypothalamus can unload models when `get_running_apps()` shows RAM pressure.
- **No privacy leakage:** `detect_user_context` reads window titles only through the permission gate; the raw list is never sent to the cloud.

---

## 6. Integration Points

| Source | Target | What flows |
|---|---|---|
| Occipital → Hands | Vision provides coordinates for clicks | `find_element` → `Hands.click` |
| Occipital → Amygdala | Screenshot content scored for threats | OCR text → `ThreatDetector.assess` |
| Body/Legs → Fingers | Downloads classified before save | `download_file` → `FileManager.write` |
| Parietal → Thalamus | Context hints improve routing | `detect_user_context` → model selection |
| All → Prefrontal | New action categories | `visual_capture`, `browse_internet`, `download_files`, `web_automation` |
| All → Spinal Cord | Kill switch consulted on every action | `kill_switch.is_killed()` checked in `_run` |
| All → Audit | Every action logged | `audit.record()` with category, target, decision |

---

## 7. Dependencies (version-pinned)

| Package | Version | For |
|---|---|---|
| `playwright` | 1.49.1 | Browser automation |
| `Pillow` | 12.3.0 | Image processing |
| `mss` | 9.0.1 | Fast screenshots |
| `pytesseract` | 0.3.10 | OCR backup |
| `pygetwindow` | 0.0.9 | Active window detection |
| Already available | — | Moondream via Ollama |

Install:

```bash
pip install playwright==1.49.1 Pillow==12.3.0 mss==9.0.1 pytesseract==0.3.10 pygetwindow==0.0.9
playwright install chromium
```

All of the above are in `requirements.txt` now, so `pip install -r requirements.txt`
is enough; the line is kept here for reference.

> **Changed in Session 10:** `playwright` and `Pillow` were originally pinned at
> 1.45.0 and 10.4.0. Those pins cannot be installed on Python 3.13, which is the
> interpreter on this machine. `playwright` 1.45.0 pins `greenlet==3.0.3`, which
> has no cp313 wheel and falls back to compiling C extensions, failing without
> the MSVC toolchain. They are bumped to the lowest versions that ship cp313
> wheels (playwright 1.49.1 -> greenlet 3.1.1). `mss` and `pytesseract` are
> pure-Python and keep their original pins.

---

## 8. Attack Surface Analysis

| Attack Vector | Mitigation |
|---|---|
| Vision prompt injection via screenshot content | Isolate vision prompts from reasoning prompts; OCR text scored by Amygdala |
| Browser as data exfiltration channel | URL allowlist; DNS filtering; `localhost` blocked by default |
| Fake UI elements to trick clicks | Coordinate verification + visual check before click |
| Malicious websites exploiting Playwright | Default-deny JavaScript on untrusted sites |
| Clipboard poisoning via web page | Scan clipboard content before paste |
| Download abuse | File classification before save; size limits; type restrictions |
| Headless browser fingerprinting | `headless=False` by default; user sees every action |
| Keyboard injection via browser | Input focus validation; only type into focused elements |

---

## 9. Build Order

| Step | Component | Depends on | Duration |
|---|---|---|---|
| 1 | Occipital Lobe | Phase 2 complete | 1 session |
| 2 | Body/Legs | Step 1 (element verification) | 1-2 sessions |
| 3 | Parietal Lobe | Steps 1-2 | 1 session |
| 4 | Integration test | Steps 1-3 | 1 session |

Step 1 is first because everything else depends on seeing. Step 2 uses Occipital
for element verification. Step 3 ties context together. Step 4 is the end-to-end
test: "Go to Amazon, find the cheapest laptop."

---

## 10. Testing Strategy

| Test Type | Scope | Count Target |
|---|---|---|
| Attack tests per component | Same discipline as Phase 2 | 15+ patterns |
| Visual regression tests | Screenshot comparison | 10+ |
| Browser automation tests | Fake HTML pages, no real network | 20+ |
| Integration tests | Vision + browser + hands | 8+ scenarios |
| Red team | 15+ patterns across new attack surface | 15+ |
| Live hardware verification | Combined script | 7 phases |

### New test categories

- **Vision prompt injection:** screenshot containing `"ignore all previous instructions"` must not alter reasoning
- **Browser URL allowlist:** navigation to `localhost` refused at trust level 1-4
- **Download classification:** downloaded `.exe` blocked by `FileClassifier`
- **OCR confidence:** low-confidence OCR triggers user confirmation, not silent action
- **Headless detection:** browser reports `headless=False` in user-agent

---

## 11. Risks and Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Vision model accuracy limitations | High | Confidence thresholds; user confirmation below 70% |
| Browser automation detection by websites | Medium | Human-like timing; user intervention mode |
| Dynamic page handling complexity | Medium | Wait strategies; retry logic; explicit timeouts |
| Resource usage (browser + vision together) | Medium | Hypothalamus coordinates loading; VRAM budget enforced |
| Website ToS violations via automation | Low | User approves per-domain; no headless by default |
| OCR text injection | Medium | OCR text scored by Amygdala; never injected into reasoning directly |
| Screenshot privacy | Medium | Classified before storage; sensitive screens flagged |
| Playwright process leak | Low | Context manager ensures cleanup; watchdog monitors |

---

## 12. Open Questions for Jayanthan

### Q1: Browser visibility

Should ZUZZ use headless or visible browser by default?

- **A) Visible** — user sees what's happening (recommended)
- **B) Headless** — faster, but opaque
- **C) Configurable per operation**

**My recommendation: A** (visible — user can see and intervene)

### Q2: URL allowlist strategy

What is the initial URL allowlist policy?

- **A) Allow all**, require per-domain confirmation
- **B) Start with empty allowlist**, user adds domains
- **C) Preset allowlist** (common safe sites), with A fallback

**My recommendation: C** (common safe sites) with A fallback

### Q3: Download file handling

How are downloads managed?

- **A) Downloads go through Body/Fingers classifier**
- **B) Separate `downloads/` directory with scan**
- **C) Both** — classified then quarantined

**My recommendation: C** (defense in depth)

### Q4: Vision confidence threshold

What accuracy threshold triggers user confirmation?

- **A) High (90%+)** — require user confirmation below
- **B) Medium (70%+)** — proceed with warning
- **C) Configurable per task**

**My recommendation: C** with sensible defaults (90% for clicks, 70% for reading)

### Q5: Browser session persistence

Should browser sessions persist between tasks?

- **A) Session per task** — fresh each time
- **B) Persistent session** — saves cookies
- **C) User-approved persistence per domain**

**My recommendation: C** (user choice, default fresh)

### Q6: Multi-tab limit

How many tabs per browser session?

- **A) Single tab only**
- **B) Up to 5 tabs** (recommended)
- **C) Up to 10 tabs**
- **D) Unlimited**

**My recommendation: B** (5 tabs — reasonable without overwhelming)

### Q7: OCR fallback priority

When Moondream confidence is low, what is the fallback?

- **A) Moondream only** (lighter)
- **B) Tesseract backup** (more reliable text)
- **C) Both, use best confidence** (recommended)

**My recommendation: C** (both for critical reads)
