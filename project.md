### File 1: `web\zuzz-assistant\PROJECT.md`

```markdown
# 🧠 ZUZZ — Digital Brain AI Agent
## Master Project Document

**Version:** 1.0  
**Last Updated:** 2025  
**Developer:** Jayanthan  
**AI Assistant:** Antigravity  
**Status:** Phase 1 — Development Starting  

---

## TABLE OF CONTENTS
1. [Project Vision](#1-project-vision)
2. [What ZUZZ Does](#2-what-zuzz-does)
3. [System Specifications](#3-system-specifications)
4. [Architecture — Digital Brain Model](#4-architecture--digital-brain-model)
5. [Technology Stack](#5-technology-stack)
6. [Pre-Built Repos We Use](#6-pre-built-repos-we-use)
7. [Infrastructure Already Set Up](#7-infrastructure-already-set-up)
8. [Complete Feature Requirements](#8-complete-feature-requirements)
9. [Task Routing Logic](#9-task-routing-logic)
10. [Safety System](#10-safety-system)
11. [Multi-Agent System](#11-multi-agent-system)
12. [Communication System](#12-communication-system)
13. [Self-Improvement System](#13-self-improvement-system)
14. [Folder Structure](#14-folder-structure)
15. [Build Phases](#15-build-phases)
16. [API Keys Available](#16-api-keys-available)
17. [Implementation Guidelines](#17-implementation-guidelines)
18. [Current Progress](#18-current-progress)

---

## 1. PROJECT VISION
Build a personal AI assistant called **ZUZZ** that:
- Lives inside the user's computer (Windows laptop)
- Controls the computer like a human (mouse, keyboard, apps, files, browser)
- Thinks using a Digital Brain architecture modeled after human brain anatomy
- Uses small local AI models for fast tasks (runs on 6GB VRAM GPU)
- Uses free cloud AI models for complex tasks (Groq, Colab, HuggingFace)
- Learns from user behavior and improves itself over time
- Creates new capabilities by writing its own code (plugins)
- Works 24/7 via background cloud agents even when laptop is off
- Contacts user via web popup, Telegram, email, push notifications
- Phone calls via Bolna AI + Twilio (deferred to later phase)
- Maintains strict safety system that AI cannot override
- Accessible from any device via web browser (localhost:8080)
- Runs 100% free (optional paid APIs for premium features)

---

## 2. WHAT ZUZZ DOES

### User Interactions
- Chat via web interface (text, like ChatGPT)
- Voice conversation in browser (real-time, like ChatGPT voice mode)
- Voice input via microphone (Whisper speech-to-text)
- Voice output via speakers (Piper/Edge TTS)
- Wake word activation ("Hey ZUZZ")
- Access from phone browser on same WiFi network

### Computer Control
- Mouse: click, double-click, right-click, drag, scroll, move
- Keyboard: type text, press keys, hotkey combos (Ctrl+C, Alt+Tab)
- Apps: open, close, switch, manage windows
- Files: create, read, write, move, copy, rename, delete, search
- Browser: open URLs, click elements, fill forms, scrape data, manage tabs
- Code: write and execute Python, Shell, JavaScript
- Terminal: run any command, capture output
- Screenshots: capture screen, analyze with vision AI

### Intelligence
- Simple tasks: handled by local 1.5B model in <1 second
- Medium tasks: handled by local 3B model in 2-5 seconds
- Complex tasks: handled by Groq 70B cloud in 5-15 seconds
- Very complex: handled by multi-agent cloud teams in 30s-2min
- Continuous tasks: handled by 24/7 background cloud agents

### Learning & Memory
- Remembers all conversations
- Learns how it solved past tasks and reuses methods
- Detects user habits and creates automated routines
- Stores user preferences
- Learns from mistakes and never repeats them

### Self-Improvement
- Writes new plugins to add capabilities it lacks
- User approves plugins before they activate
- Can modify its own non-core code
- Cannot modify safety rules, kill switch, or activity log

---

## 3. SYSTEM SPECIFICATIONS

### Hardware (Jayanthan's Laptop)
Processor:  AMD Ryzen 5 7235HS (3.20 GHz, 8 threads)
RAM:        16 GB (15.7 GB usable, ~8 GB available after Windows)
GPU:        NVIDIA GeForce RTX 4050 Laptop GPU (6 GB VRAM)
Storage:    477 GB SSD (~70 GB free after project install)
External:   1 TB HDD (plug-in when needed)
OS:         Windows 11 64-bit
Display:    Standard laptop (no touch/pen)
Audio:      Built-in microphone and speakers
Network:    WiFi + Ethernet

### Resource Budget
VRAM Budget (6 GB total):
Qwen 1.5B (always loaded):  ~1 GB
Qwen 3B (on demand):        ~2 GB
Moondream (on demand):       ~1.5 GB
RULE: Never exceed 4.5 GB VRAM (leave 1.5 GB headroom)
RULE: Never load all models simultaneously
RULE: Unload on-demand models within 60s after task

RAM Budget (8 GB available after Windows):
Ollama runtime:    ~500 MB
FastAPI server:    ~300 MB
Playwright:        ~500 MB - 1 GB
Python + Memory:   ~500 MB
RULE: Total agent RAM must stay under 3 GB
RULE: Leave 5 GB headroom for Windows + user apps

Storage Budget (~70 GB free):
Models:            ~5 GB
Python packages:   ~3 GB
Agent code:        ~200 MB
Memory database:   ~200 MB (grows)
RULE: Total project footprint under 10 GB

---

## 4. ARCHITECTURE — DIGITAL BRAIN MODEL
ZUZZ's AI system is modeled after human brain anatomy. Each brain region maps to a specific functional AI component.

### 19 Brain Components
COMPONENT              FUNCTION                            IMPLEMENTATION
─────────────────────────────────────────────────────────────────────────
1. Cerebrum           Main reasoning & language            Ollama + LiteLLM
2. Cerebellum         Task coordination & personality      CrewAI + prompts
3. Brainstem          Background services & heartbeat      FastAPI + APScheduler
4. Frontal Lobe       Planning & decision-making           CrewAI + LangGraph
5. Parietal Lobe      System & spatial awareness           psutil + pyautogui
6. Temporal Lobe      Voice input & language               Whisper + intent parsing
7. Occipital Lobe     Screen vision & images               Moondream + screenshots
8. Thalamus           Smart router (MOST CRITICAL)         LiteLLM + pattern matching
9. Hippocampus        Memory (short→long term)             JSON + ChromaDB
10. Amygdala           Threat detection & safety            Pattern matching + scoring
11. Prefrontal Cortex  Permission & impulse control         Trust levels + immutable rules
12. Hypothalamus       Resource management                  psutil + model load/unload
13. Corpus Callosum    Local ↔ Cloud bridge                 LiteLLM + API vault
14. Basal Ganglia      Habit learning & routines            Pattern detection + scheduler
15. Spinal Cord        Emergency kill switch                Global hotkey + watchdog
16. Reticular Formation Attention & wake word               pvporcupine + idle detection
17. Broca's Area       Speech production (TTS)              Piper TTS / Edge TTS
18. Wernicke's Area    Deep language comprehension          LLM + specialized prompts
19. Mirror Neurons     Learning by watching user            Input monitoring + macros

### Body Components
COMPONENT    FUNCTION                    IMPLEMENTATION
───────────────────────────────────────────────────────
Hands        Mouse & keyboard control    PyAutoGUI + pynput
Fingers      File operations             os + shutil + pathlib
Legs         Browser automation          Playwright
Mouth        Code execution              subprocess
Eyes         Screenshot capture          PyAutoGUI + Pillow + mss
Tools        Specialized capabilities    Plugin system

---

## 5. TECHNOLOGY STACK
Category             Technology              Purpose
──────────────────────────────────────────────────────────
Language             Python 3.10+            Primary language
Web Server           FastAPI + Uvicorn       Backend API + WebSocket
Frontend             HTML + CSS + JS         Web chat interface
Base Chat UI         Open WebUI              Pre-built chat (reference)
Local AI Runtime     Ollama                  Model management
Local Models         Qwen 2.5 1.5B + 3B     Local brain (fast + smart)
Local Vision         Moondream 1.8B          Screen reading
Cloud AI Manager     LiteLLM                 Unified API for 100+ providers
Cloud Free           Groq + HuggingFace      Free cloud AI
Cloud Free           OpenRouter + NVIDIA     Additional free providers
Cloud Heavy          Google Colab            Free GPU for big models
Multi-Agent          CrewAI                  Agent orchestration
Background Tasks     Open-DOTS + APScheduler Autonomous task loop
PC Control           Open Interpreter        Computer control engine
PC Control           PyAutoGUI + pynput      Mouse, keyboard
Browser              Playwright              Web automation
STT                  Whisper Tiny            Speech-to-text (75MB)
TTS                  Piper TTS / Edge TTS    Text-to-speech
Voice Chat           LiveKit Agents          Real-time browser voice
Phone Calls          Bolna AI + Twilio       Phone calls (DEFERRED)
Messaging            python-telegram-bot     Telegram bot
Push Notifications   ntfy                    Push alerts
Email                smtplib                 Email notifications
Memory               JSON + ChromaDB         Conversation + semantic search
Encryption           cryptography            API key vault
System Monitor       psutil + pynvml         RAM/VRAM/CPU tracking
Wake Word            pvporcupine             "Hey ZUZZ" detection
Skill Learning       OS-Copilot              Self-improvement
Windows UI           UFO (Microsoft)         Windows app control
Workflows            LangGraph               Complex task flows
Auto-Restart         GitHub Actions          Colab keep-alive
Remote Access        Tailscale               Access from anywhere
Kill Switch          keyboard                Emergency stop hotkey

---

## 6. PRE-BUILT REPOS WE USE
Repository           Stars    Use For                    Install
─────────────────────────────────────────────────────────────────
Open WebUI           70K+     Web interface reference    pip install open-webui
Open Interpreter     55K+     Computer control engine    pip install open-interpreter
Open-DOTS            New      Background task engine     git clone
CrewAI               25K+     Multi-agent teams          pip install crewai
LiteLLM              12K+     API management             pip install litellm
LiveKit Agents       4K+      Real-time voice chat       pip install livekit-agents
Bolna AI             4K+      Phone calls (DEFERRED)     git clone
OS-Copilot           3K+      Skill learning             git clone
UFO (Microsoft)      8K+      Windows UI automation      pip install ufo
LangGraph            8K+      Complex workflows          pip install langgraph

---

## 7. INFRASTRUCTURE ALREADY SET UP
SERVICE                STATUS    DETAILS
──────────────────────────────────────────────────────────
Ollama (Local)         ✅ Done   Installed on laptop
Groq API               ✅ Done   API key obtained, LLaMA 70B access
HuggingFace API        ✅ Done   API token obtained
OpenRouter API         ✅ Done   API key (pre-existing)
NVIDIA Build API       ✅ Done   API key (pre-existing)
Google Colab           ✅ Done   Notebook running with:
                                 - Ollama installed
                                 - LLaMA 3.1 8B downloaded
                                 - Flask server running
                                 - ngrok tunnel active
                                 - Chat endpoint working
                                 - Health endpoint working
ngrok                  ✅ Done   Auth token configured, tunnel active
Telegram Bot           ✅ Done   Bot created via BotFather, token obtained
ntfy                   ✅ Done   Topic created for push notifications
GitHub Repo            ✅ Done   Repository created
Cron-Job.org           ✅ Done   Pinging Colab every 15 min

Local Models to Download:
ollama pull qwen2.5-coder:1.5b    (~1 GB)
ollama pull qwen2.5-coder:3b      (~2 GB)
ollama pull moondream             (~1.5 GB)

---

## 8. COMPLETE FEATURE REQUIREMENTS

### 8.1 Chat & Conversation
- Text chat via web browser (real-time WebSocket)
- Natural flowing conversation with context memory
- Streaming responses (text appears word by word)
- Conversation history (last 20 messages in session, all saved to disk)
- Multiple conversation threads (future)
- Code syntax highlighting in responses
- Markdown rendering in responses
- File/image sharing in chat (future)

### 8.2 Computer Control
- Mouse: click, double-click, right-click, drag, scroll at any coordinate
- Keyboard: type any text, press any key, any hotkey combination
- Clipboard: copy, paste, read clipboard content
- Apps: open by name, close, switch between, window management
- File operations: full CRUD with safety classification
- Terminal: execute any command with timeout protection
- Code execution: Python, Shell, JavaScript with sandboxing

### 8.3 Screen Vision
- Full screen capture and region capture
- Send screenshots to Moondream vision model
- Describe screen content in natural language
- Find UI elements (buttons, text fields) and return coordinates
- OCR (read text from images)
- Before/after comparison to verify actions succeeded

### 8.4 Browser Automation
- Open any URL in any browser
- Click elements by selector, text, or coordinates
- Fill forms automatically
- Extract text and data from web pages
- Manage multiple tabs
- Handle dynamic/JavaScript pages
- Web search (Google, DuckDuckGo)
- Login to websites (with permission)

### 8.5 Voice Interaction
- Speech-to-text via Whisper Tiny (local, fast, 75MB)
- Text-to-speech via Piper TTS or Edge TTS
- Real-time voice conversation in browser via LiveKit
- Wake word "Hey ZUZZ" via Porcupine
- Interruption handling (user can interrupt mid-response)
- Streaming speech (starts speaking before full response)
- Emotion in voice (concerned, excited, casual)

### 8.6 Memory & Learning
- Short-term: current session (last 20 messages in RAM)
- Long-term: all conversations saved to JSON on disk
- Learned tasks: remembers how it solved past problems
- User preferences: detects and stores preferences
- Mistakes: logs errors and corrections, never repeats
- Semantic search: finds relevant past memories by meaning
- Backup: auto-backup to external HDD when connected

### 8.7 Habit & Routine Engine
- Detects repeated user behaviors automatically
- After 5+ repetitions → suggests creating automation
- Context triggers: time-based, app-based, command-based
- Example: "Start work" → opens VS Code + Chrome + to-do list

### 8.8 Self-Improvement
- AI writes Python plugins for new capabilities
- Plugin safety checker scans code before execution
- User approves plugins via web dashboard
- Skill learning from successful tasks (OS-Copilot)
- Self-modification of non-core code only
- A/B testing of new vs old methods (future)

### 8.9 Multi-Agent System
- Level 1: Single local model (simple, <1s)
- Level 2: Local team of 2-3 models (medium, 2-5s)
- Level 3: Single cloud model (complex, 5-15s)
- Level 4: Multi-agent cloud team (very complex, 30s-2min)
- Level 5: Background cloud agents (continuous, 24/7)
- Smart API selection based on task type and cost preference
- User can force specific provider or "free only" mode

### 8.10 Communication System
- Web popup notifications (primary, instant)
- Telegram bot messaging (free, text + voice + images + buttons)
- Email notifications (daily briefings, reports)
- Push notifications via ntfy (quick alerts)
- Phone calls via Bolna AI + Twilio (DEFERRED)
- Escalation chain: popup(60s) → call → telegram → email → retry
- Works even when laptop is off (via cloud agents)

### 8.11 Background Agents
- Deploy on Google Colab (free GPU)
- Run scheduled tasks (email monitoring, research, price tracking)
- Auto-restart via GitHub Actions every 11 hours
- Keep-alive via Cron-Job.org every 15 minutes
- Sync results when laptop comes back online
- User manages agents from web dashboard

---

## 9. TASK ROUTING LOGIC

### Thalamus Decision Tree
Input received
│
├─ Is it a simple command? (open app, list files, hello)
│  YES → Level 1 → Qwen 1.5B local → <1 second
│
├─ Is it medium complexity? (organize folder, write short script)
│  YES → Level 2 → Qwen 3B local → 2-5 seconds
│
├─ Is it complex? (debug code, explain algorithm, analyze data)
│  YES → Level 3 → Groq 70B or Colab 8B → 5-15 seconds
│
├─ Is it very complex? (build full app, multi-step research)
│  YES → Level 4 → Multi-agent cloud team → 30s-2min
│
├─ Is it continuous? (monitor email, track prices daily)
│  YES → Level 5 → Background cloud agent → 24/7
│
└─ Is internet available?
   NO → Force local model regardless of complexity

### API Selection Rules
Task Type       → Best API
───────────────────────────
Fast chat       → Groq (fastest, free)
Code writing    → DeepSeek / Claude (if user has key)
Complex reason  → Groq LLaMA 70B
Web research    → Perplexity (if user has key)
Vision/Images   → GPT-4V / Claude (if user has key)
Creative        → Claude (if user has key)
Math/Science    → GPT-4 (if user has key)
Cheap bulk      → DeepSeek / Groq (cheapest)
DEFAULT (free)  → Groq → Colab → HuggingFace → OpenRouter

---

## 10. SAFETY SYSTEM

### Immutable Rules (AI CANNOT modify these)
1. Never delete operating system or system files
2. Never send user data to unauthorized external parties
3. Never disable safety checks or modify safety code
4. Never run destructive commands without explicit user confirmation
5. Never access passwords/credentials without confirmation
6. Log ALL actions to unmodifiable activity log
7. Never install software without user approval
8. Kill switch (Ctrl+Shift+Q) is always active and cannot be disabled

### Trust Levels
Level 1: Ask permission for EVERYTHING
Level 2: Auto-approve safe actions (open apps, read files)
         Ask for risky actions (mouse/keyboard, write files)
Level 3: Auto-approve most actions
         Ask for dangerous only (delete, install, admin)
Level 4: Auto-approve all except immutable rules
Level 5: Full autonomy (still respects 8 immutable rules)

DEFAULT: Level 1 (safest)
AI cannot change its own trust level.

### Permission Matrix
Action                  Lvl1  Lvl2  Lvl3  Lvl4  Lvl5
──────────────────────────────────────────────────────
Chat/answer              ✅    ✅    ✅    ✅    ✅
Read files               ❓    ✅    ✅    ✅    ✅
Open apps                ❓    ✅    ✅    ✅    ✅
Close apps               ❓    ❓    ❓    ❓    ❓
Fullscreen control       ❓    ❓    ❓    ❓    ❓
Mouse/keyboard           ❓    ❓    ✅    ✅    ✅
Run safe commands        ❓    ❓    ✅    ✅    ✅
Write new files          ❓    ✅    ✅    ✅    ✅
Browse internet          ❓    ❓    ❓    ✅    ✅
Run Python code          ❓    ❓    ❓    ❓    ✅
Modify own plugins       ❓    ❓    ❓    ❓    ✅
Delete user files        ❓    ❓    ❓    ❓    ❓
Install software         ❓    ❓    ❓    ❓    ❓
Send emails/messages     ❓    ❓    ❓    ❓    ❓
Run as admin             ❓    ❓    ❓    ❓    ❓
Access passwords         ❓    ❓    ❓    ❓    ❓
Delete OS files          🚫   🚫   🚫   🚫   🚫
Share data externally    🚫   🚫   🚫   🚫   🚫
Disable safety           🚫   🚫   🚫   🚫   🚫
Manage models            ❓    ❓    ✅    ✅    ✅

✅ = Auto-approve  ❓ = Ask user  🚫 = ALWAYS BLOCKED
* Delete checks file priority before asking
* Config: config/trust_levels.json (20 action categories, 5 trust levels)

### Kill Switch
- Hotkey: Ctrl+Shift+Q
- Instantly freezes ALL AI actions
- Runs in separate process (works even if brain crashes)
- Also detects: runaway processes, infinite loops, mass file deletion

### Anti-Hallucination
- AI must rate its own confidence (if <70% → ask user)
- Verify file paths exist before acting
- Cross-check cloud output with local model
- Never fabricate system commands

---

## 11. MULTI-AGENT SYSTEM

### Local Agents
Sprint Agent (Level 1):
Model: Qwen 1.5B (always loaded)
Use: quick answers, simple commands
Speed: <1 second

Local Squad (Level 2):
Planner: Qwen 3B (creates plan)
Executor: Qwen 1.5B (runs steps)
Eyes: Moondream (verifies visually)
Use: multi-step local tasks

### Cloud Agents
Cloud Expert (Level 3):
Primary: Groq LLaMA 70B (free, fastest)
Backup: Colab LLaMA 8B, HuggingFace, OpenRouter
Use: complex single-answer tasks

Cloud Orchestra (Level 4):
Architect Agent → designs solution
Coder Agent → writes code
Reviewer Agent → checks quality
Debugger Agent → fixes issues
Runs on: Google Colab
Framework: CrewAI
Use: full project building, complex research

Background Agents (Level 5):
Runs on: Google Colab 24/7
Auto-restart: GitHub Actions every 11 hours
Keep-alive: Cron-Job.org every 15 minutes
Examples: email monitoring, price tracking, research
Syncs results when laptop comes online

---

## 12. COMMUNICATION SYSTEM

### Channels (Priority Order)
1. Web popup in browser (instant, primary)
2. Phone call via Bolna + Twilio (DEFERRED)
3. Telegram bot message (free, rich features)
4. Push notification via ntfy (quick alert)
5. Email (detailed reports)

### Escalation Chain
Need to contact user:
Web popup (wait 60s) → Telegram message (wait 2min) → Push notification (wait 2min) → Email (wait 5min) → Retry from beginning

If truly urgent and unreachable:
Take safe default action → Log everything → Notify when reconnected

---

## 13. SELF-IMPROVEMENT SYSTEM

### Plugin Creation Flow
1. User requests feature ZUZZ doesn't have
2. ZUZZ writes Python plugin code
3. Plugin safety checker scans for danger
4. User reviews plugin in web dashboard
5. User approves → plugin loaded dynamically
6. Plugin persists across restarts
7. ZUZZ can now do the new thing forever

### Restrictions on Self-Modification
CAN modify:
✅ Its own plugins
✅ Its personality prompts
✅ Routine/habit definitions
✅ Non-core utility code

CANNOT modify:
🚫 Immutable safety rules (read-only file)
🚫 Kill switch code
🚫 Permission gateway
🚫 Activity log system
🚫 Trust level definitions
🚫 API key vault encryption

---

## 14. FOLDER STRUCTURE
```text
web\zuzz-assistant                     # Project root (moved from C:\ZUZZ, see CHANGELOG Session 1)
├── start.py                          # Main entry point
├── stop.bat                          # Emergency stop script
├── setup.bat                         # First-time setup
├── requirements.txt                  # Python dependencies
├── .env                              # API keys (never commit)
├── .env.example                      # Template for keys
├── .gitignore                        # Git ignore rules
├── PROJECT.md                        # THIS FILE
├── CHANGELOG.md                      # AI updates this file
├── README.md                         # GitHub readme
├── core/                             # Shared layer (approved in CHANGELOG Session 1)
│   ├── __init__.py
│   └── config.py                     # Settings + secrets loader for all regions
│
├── brain/
│   ├── __init__.py
│   ├── cerebrum/
│   │   ├── __init__.py
│   │   ├── cerebrum.py               # Main LLM reasoning
│   │   ├── prompts.py                # System prompts
│   │   └── reasoning.py              # Chain-of-thought
│   ├── cerebellum/
│   │   ├── __init__.py
│   │   ├── coordinator.py            # Task step execution
│   │   ├── personality.py            # ZUZZ character
│   │   └── personality_config.json   # Editable personality
│   ├── brainstem/
│   │   ├── __init__.py
│   │   ├── server.py                 # FastAPI + WebSocket
│   │   ├── heartbeat.py              # Health monitoring
│   │   ├── scheduler.py              # Background tasks
│   │   ├── process_manager.py        # Ollama management
│   │   └── activity_logger.py        # Immutable action log
│   ├── frontal_lobe/
│   │   ├── __init__.py
│   │   ├── planner.py                # Task decomposition
│   │   ├── decision_maker.py         # Strategy selection
│   │   └── priority_manager.py       # Task queue
│   ├── parietal_lobe/
│   │   ├── __init__.py
│   │   ├── system_awareness.py       # Active window, mouse pos
│   │   └── context_detector.py       # User context
│   ├── temporal_lobe/
│   │   ├── __init__.py
│   │   ├── speech_to_text.py         # Whisper
│   │   ├── text_to_speech.py         # Piper/Edge TTS
│   │   ├── intent_parser.py          # Wernicke's Area
│   │   └── language_utils.py         # Text utilities
│   ├── occipital_lobe/
│   │   ├── __init__.py
│   │   ├── screen_reader.py          # Screenshot + Moondream
│   │   ├── element_finder.py         # UI element detection
│   │   └── visual_memory.py          # Before/after compare
│   ├── thalamus/
│   │   ├── __init__.py
│   │   ├── router.py                 # Smart task routing
│   │   ├── complexity_analyzer.py    # Level 1-5 classification
│   │   └── load_balancer.py          # Work distribution
│   ├── hippocampus/
│   │   ├── __init__.py
│   │   ├── memory_manager.py         # Main memory control
│   │   ├── short_term.py             # Session memory
│   │   ├── long_term.py              # Persistent memory
│   │   └── memory_search.py          # Semantic search
│   ├── amygdala/
│   │   ├── __init__.py
│   │   ├── threat_detector.py        # Danger scanning
│   │   ├── risk_scorer.py            # Risk level 1-10
│   │   └── danger_patterns.json      # Known dangerous commands
│   ├── prefrontal_cortex/
│   │   ├── __init__.py
│   │   ├── permission_gate.py        # Trust level enforcement
│   │   ├── trust_levels.py           # Level definitions
│   │   ├── immutable_rules.py        # 🔒 READ-ONLY safety
│   │   └── confirmation.py           # User approval handler
│   ├── hypothalamus/
│   │   ├── __init__.py
│   │   ├── resource_monitor.py       # RAM/VRAM/CPU tracking
│   │   ├── model_manager.py          # Load/unload models
│   │   └── power_manager.py          # Power modes
│   ├── corpus_callosum/
│   │   ├── __init__.py
│   │   ├── cloud_bridge.py           # Cloud API manager
│   │   ├── api_vault.py              # Encrypted key storage
│   │   ├── groq_connector.py         # Groq API
│   │   ├── huggingface_connector.py  # HF API
│   │   ├── openrouter_connector.py   # OpenRouter API
│   │   ├── nvidia_connector.py       # NVIDIA API
│   │   ├── colab_connector.py        # Colab connection
│   │   └── custom_api_connector.py   # User's own APIs
│   ├── basal_ganglia/
│   │   ├── __init__.py
│   │   ├── pattern_detector.py       # Habit detection
│   │   ├── routine_builder.py        # Automation creation
│   │   └── routines.json             # Saved routines
│   ├── spinal_cord/
│   │   ├── __init__.py
│   │   ├── kill_switch.py            # Ctrl+Shift+Q handler
│   │   ├── watchdog.py               # Process watchdog
│   │   └── emergency_stop.py         # Full system stop
│   ├── reticular_formation/
│   │   ├── __init__.py
│   │   ├── attention_manager.py      # Focus management
│   │   └── wake_word.py              # "Hey ZUZZ"
│   └── communication_hub/
│       ├── __init__.py
│       ├── hub.py                    # Communication controller
│       ├── escalation_chain.py       # Escalation logic
│       ├── telegram_bot.py           # Telegram messaging
│       ├── email_sender.py           # Email
│       ├── push_notifier.py          # ntfy push
│       ├── voice_conversation.py     # LiveKit voice chat
│       └── channel_config.json       # Contact preferences
│
├── body/
│   ├── __init__.py
│   ├── hands/
│   │   ├── __init__.py
│   │   ├── mouse.py                  # Mouse control
│   │   ├── keyboard.py              # Keyboard control
│   │   ├── clipboard.py             # Clipboard management
│   │   └── app_controller.py        # App management
│   ├── fingers/
│   │   ├── __init__.py
│   │   ├── file_manager.py          # File CRUD
│   │   ├── directory_manager.py     # Folder operations
│   │   └── file_classifier.py       # File safety levels
│   ├── legs/
│   │   ├── __init__.py
│   │   ├── browser_controller.py    # Playwright
│   │   ├── web_scraper.py           # Data extraction
│   │   └── web_search.py            # Search engines
│   ├── mouth/
│   │   ├── __init__.py
│   │   ├── code_executor.py         # Safe code execution
│   │   ├── code_writer.py           # Code generation
│   │   └── terminal.py              # Shell commands
│   └── tools/
│       ├── __init__.py
│       ├── tool_registry.py         # Available tools list
│       ├── screenshot_tool.py       # Screenshot utility
│       └── system_info_tool.py      # System information
│
├── face/
│   ├── __init__.py
│   ├── frontend/
│   │   ├── index.html               # Main page
│   │   ├── static/
│   │   │   ├── css/
│   │   │   │   └── main.css         # Styles
│   │   │   ├── js/
│   │   │   │   ├── app.js           # Main logic
│   │   │   │   ├── chat.js          # Chat functionality
│   │   │   │   ├── websocket.js     # WS connection
│   │   │   │   └── dashboard.js     # Dashboard
│   │   │   └── assets/
│   │   │       └── images/          # Logo, icons
│   │   └── pages/
│   │       ├── dashboard.html       # System dashboard
│   │       ├── settings.html        # Settings
│   │       └── plugins.html         # Plugin manager
│   └── api/
│       ├── __init__.py
│       ├── server.py                # FastAPI main server
│       ├── routes_chat.py           # Chat endpoints
│       ├── routes_brain.py          # Brain status endpoints
│       ├── routes_settings.py       # Settings endpoints
│       └── routes_plugins.py        # Plugin endpoints
│
├── multi_agent/
│   ├── __init__.py
│   ├── orchestrator.py              # Multi-agent coordinator
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── architect_agent.py       # Plans solutions
│   │   ├── coder_agent.py           # Writes code
│   │   ├── reviewer_agent.py        # Reviews quality
│   │   └── researcher_agent.py      # Web research
│   └── background/
│       ├── __init__.py
│       ├── background_manager.py    # Always-on agent manager
│       └── templates/
│           ├── colab_base.ipynb     # Base Colab notebook
│           └── colab_keepalive.ipynb # Auto-restart
│
├── plugins/
│   ├── __init__.py
│   ├── plugin_engine.py             # Plugin loader
│   ├── plugin_creator.py            # AI writes plugins
│   ├── plugin_safety.py             # Safety checker
│   ├── plugin_registry.json         # Installed plugins list
│   └── installed/                   # AI-created plugins go here
│       └── README.md
│
├── storage/
│   ├── memory/
│   │   ├── conversations.json       # Chat history
│   │   ├── learned_tasks.json       # Learned methods
│   │   ├── preferences.json         # User preferences
│   │   ├── mistakes.json            # Error log
│   │   └── routines.json            # Automations
│   ├── logs/
│   │   ├── activity_log.jsonl       # Immutable action log
│   │   ├── error_log.jsonl          # Error log
│   │   └── brain_activity.jsonl     # Brain region activity
│   ├── screenshots/                 # Captured screens
│   ├── temp/                        # Temporary files
│   └── cache/                       # Cloud response cache
│
├── config/
│   ├── settings.json                # General settings
│   ├── models.json                  # Model configs
│   ├── trust_levels.json            # Permission definitions
│   ├── danger_patterns.json         # Dangerous commands
│   └── personality.json             # ZUZZ character
│
├── tests/
│   ├── test_cerebrum.py
│   ├── test_thalamus.py
│   ├── test_safety.py
│   └── test_integration.py
│
└── scripts/
    ├── install_models.py            # Download all models
    ├── setup_colab.py               # Setup Colab notebook
    ├── backup_memory.py             # Backup to HDD
    └── health_check.py              # Full system check

```

---

## 15. BUILD PHASES

Phase 1  (Week 1-2):   Web app + Chat + Local/Cloud brain + Smart routing
Phase 2  (Week 3-4):   Computer control + Safety core + Kill switch
Phase 3  (Week 5-6):   Screen vision + Browser automation
Phase 4  (Week 7-8):   Code execution + Cloud integration + Multi-provider
Phase 5  (Week 9-10):  Voice interaction (STT + TTS + wake word)
Phase 6  (Week 11-12): Memory + Habits + Personality
Phase 7  (Week 13-14): Multi-agent + Self-improvement + Plugin system
Phase 8  (Week 15-16): Telegram + Background agents + Colab deployment
Phase 9  (Week 17-18): Polish + Auto-start + Dashboard + Testing
Phase 10 (Later):      Phone calls (Bolna + Twilio)

---

## 16. API KEYS AVAILABLE

Service          Key Format              Free Tier
─────────────────────────────────────────────────────
Groq             gsk_xxx                 30 req/min, LLaMA 70B
HuggingFace      hf_xxx                  Rate limited inference
OpenRouter       sk-or-xxx               Some free models
NVIDIA Build     nvapi-xxx               Free tier available
Colab            (Google account)        Free T4 GPU, 12hr sessions
Telegram         123456:ABC-xxx          Completely free
ntfy             (no key needed)         Completely free
ngrok            (auth token)            Free, random URL

---

## 17. IMPLEMENTATION GUIDELINES

### Code Quality Rules

1. Every file must have a module docstring explaining purpose
2. Every class must have a class docstring
3. Every public method must have a docstring
4. Use type hints for all function parameters and returns
5. Handle all exceptions with specific error types
6. Log all important actions to activity log
7. Follow PEP 8 style guide
8. Maximum 200 lines per file (split if larger)
9. Use relative imports within packages
10. Environment variables for all configuration (never hardcode)

### Architecture Rules

1. Each brain component is independent and modular
2. Components communicate through defined interfaces only
3. Thalamus is the ONLY router — no component bypasses it
4. Prefrontal Cortex checks EVERY action before execution
5. Activity logger records EVERY action (immutable)
6. Hypothalamus approves model loading BEFORE it happens
7. Spinal Cord runs in a separate process always
8. Shared utilities used by 3+ brain regions belong in `core/`, not duplicated per region

### Performance Rules

1. Never exceed 4.5 GB VRAM
2. Never exceed 3 GB RAM for agent
3. Unload on-demand models within 60 seconds after task
4. Cache cloud responses to avoid duplicate API calls
5. Use connection pooling for API requests
6. Async/await for all I/O operations
7. Lazy imports for heavy libraries

### Safety Rules

1. immutable_rules.py is READ-ONLY — never modify
2. AI cannot change trust level
3. AI cannot modify activity log
4. AI cannot disable kill switch
5. All file deletions require user confirmation
6. All software installations require user confirmation
7. All credential access requires user confirmation

---

## 18. CURRENT PROGRESS

### Completed

* [x] Full project architecture designed
* [x] Digital Brain model with 19 components mapped
* [x] Technology stack selected
* [x] All infrastructure services set up
* [x] API keys obtained (Groq, HF, OpenRouter, NVIDIA)
* [x] Colab cloud brain running (LLaMA 3.1 8B)
* [x] ngrok tunnel active
* [x] Cron-Job.org keep-alive configured
* [x] Telegram bot created
* [x] GitHub repository created
* [x] Requirements document complete
* [x] Create project folder structure (all of section 14, every package has __init__.py)
* [x] Build Cerebrum (local Ollama + cloud LiteLLM calls, streaming and non-streaming)
* [x] Build Thalamus (level 1-5 classification, offline capping, fallback chain)
* [x] Build Brainstem (FastAPI server, immutable activity log, heartbeat, scheduler, model reaper)
* [x] Build web chat interface (streaming WebSocket UI with status panel)
* [x] Test suite written and passing (450 tests, incl. routing regressions)
* [x] HEALTH CHECK SCRIPT passing
* [x] Build Corpus Callosum (5 cloud connectors: Groq, Colab, HuggingFace, OpenRouter, NVIDIA)
* [x] Build API Vault (Fernet encrypted credential storage)
* [x] Model liveness checker (`scripts/check_models.py`) wired into the health check
* [x] **PHASE 1 CODE COMPLETE** (approved by Jayanthan, see CHANGELOG Session 1, 1b, 1c, 2)
* [x] **CLOUD PATH VERIFIED LIVE** against real Groq and Colab accounts
* [x] `core/` shared layer approved, documented in section 14 and section 17
* [x] Project root path change approved (was C:\ZUZZ, now web\zuzz-assistant)
* [x] **AGENT ACTION LOOP LIVE** — a chat reply can now make ZUZZ *do* something,
  not only say something. See "Agent Action Loop Status" below.

### Phase 1 Status

CODE complete and verified. 589 tests passing (113 Phase 1, 192 Phase 2 safety,
145 Phase 2 Spinal Cord, 139 Phase 2 Body/Fingers). Server WebSocket streaming
checked live, and the CLOUD PATH is proven against real Groq and Colab accounts
(live answers, correct routing, 1.0s Groq round trip).

Remaining: the LOCAL path only. Ollama is not installed on this machine, so
Qwen 1.5B / 3B cannot answer yet. See SETUP.md.

Model liveness is automated in scripts/check_models.py — see
docs/MODEL_MAINTENANCE.md.

### Phase 2 Status

APPROVED and building, four steps in.

* **Step 1 — Prefrontal Cortex**: the permission matrix, the immutable rules
  with a startup digest check, the gate, and the escalating trust-level
  confirmation. Rules: R1-R3 hard blocks, R4/R5/R7 force confirmation, R9 keeps
  the AI out of trust changes.
* **Step 2 — Amygdala**: threat scoring 0-10, safe 0-2 / caution 3-5 / dangerous
  6-8 / forbidden 9-10. It can only raise scrutiny; a forbidden score vetoes at
  every trust level including 5. Every assessment is written to the audit log.
* **Step 3 — Spinal Cord**: the emergency stop. The gate consults it before
  anything else, so while engaged every action is blocked with no further checks.
  Verified live: stop, restart, still stopped; reset requires typing RESET; the
  progressive cooldown blocks an immediate second reset.
* **Step 4 — Body/Fingers**: file operations behind the gate. Read, write, copy,
  move, rename, delete, stat, mkdir, list, search, tree, trash, restore,
  empty-trash, backup and restore-backup. Every method takes a
  `PermissionDecision` as its first parameter, so the gate is enforced by the
  type signature rather than by convention. Deletion is always to a trash and
  overwrite always snapshots `.bak.1` first; nothing in `body/` calls `unlink`
  or `rmtree`. Secrets are hard-blocked at every trust level, ZUZZ's own code is
  read-only to the agent, and symlinks are resolved so the *target* is
  classified. 16 API endpoints and a Files tab in the UI. **File operations
  verified safe through attack tests** — 19 attack tests, each assuming the gate
  was bypassed and handing the Body a forged trust-level-5 decision, plus a
  signature test proving no operation is reachable without a decision at all.

Decisions are recorded in docs/PHASE_2_PLAN.md section 14, and the Body layer
pattern in docs/ARCHITECTURE.md section 8.

### Agent Action Loop Status

LIVE, verified on this machine 2026-10-04. ZUZZ executes **15 tools** from a chat
reply, every one of them through the same gate the Desktop and Files tabs use.

The pipeline, per action, in this order — and the order is the safety property:

1. **Parse.** `brain/cerebrum/action_parser.py` splits the reply into prose and
   `ToolCall` objects. A block that will not parse becomes a visible `errors`
   entry, never silence.
2. **Resolve.** `ToolSpec.resolve` runs *before* the gate, so the gate judges
   `chrome.exe` rather than `chrome`. An alias cannot be re-pointed at something
   else between the decision and the launch.
3. **Assess.** The Amygdala scores the resolved action. `forbidden` vetoes;
   `dangerous` forces a confirmation.
4. **Decide.** The gate decides at the current trust level. blocked / ask / auto.
5. **Confirm.** An `ask` parks the call and returns a single-use
   `confirmation_id`. Nothing executes.
6. **Execute.** `ToolSpec.execute` is called with an allowed decision and with
   nothing else.
7. **Report.** `executed` / `blocked` / `pending` / `errors` / `warnings` /
   `summary`.

**Endpoints.** `POST /api/chat` returns `{response, actions:{...}}` with every
action fence stripped from `response`. `POST /api/chat/confirm` takes
`{confirmation_id, approval}` and returns the execution result — re-deciding from
scratch first, so a stop that landed while the popup was open still blocks and a
replayed id finds nothing. The WebSocket streams tokens, emits `done`, then emits
`actions` carrying the authoritative prose and the badges.

**The 15 tools.** `mouse_click`, `mouse_move`, `mouse_scroll`, `keyboard_type`,
`keyboard_hotkey`, `clipboard_read`, `clipboard_write`, `open_app`, `close_app`,
`file_read`, `file_write`, `file_delete`, `file_list`, `file_search`,
`get_system_resources`.

**Live verification (2026-10-04, trust level 3):**

| Scenario | Result |
|---|---|
| "Open Notepad" | Notepad launched, pid 4080. Prose clean, fence stripped. |
| Write `hello.txt` to Desktop | File created, content `ZUZZ works`. |
| "Delete C:\Windows" | Refused. Model declined in prose *and* immutable rule R1 blocked the forced block. |
| "What is 2+2?" | Pure chat, empty action envelope. |

**Known gap.** `core/paths.py` classifies a bare volume root (`C:\`) as `safe`,
because `PROTECTED_PREFIXES` lists `C:\Windows` and not the root. A delete of
`C:\` therefore parks for approval instead of being refused outright. It fails
safe — `delete_files` asks at every trust level, and `TrashManager` physically
cannot move a volume root into itself — but the prompt is unnecessary. Worth
adding the drive roots to `PROTECTED_PREFIXES`.


### Next Step

* [ ] Phase 1 END-TO-END VERIFICATION on Jayanthan's laptop (needs Ollama + the 3 models)
* [ ] Set up Python virtual environment
* [x] Create .env with API keys (Groq, HuggingFace, OpenRouter, NVIDIA, Colab, Telegram, ntfy)
* [ ] Download local models (Qwen 1.5B, 3B, Moondream)
* [x] Connect to Colab cloud brain (colab_connector.py) — verified live
* [x] Connect to Groq cloud brain (groq_connector.py) — verified live
* [x] PHASE 2 APPROVED (2026-10-02) — all 5 open questions answered, dry run added
* [x] Phase 2 Step 1: Prefrontal Cortex (trust matrix, immutable rules, gate, confirmation flow)
* [x] Phase 2 Step 2: Amygdala threat detection (0-10 scoring, context-aware, audit log)
* [x] Phase 2 Step 3: Spinal Cord kill switch (flag file in two locations, gate blocks everything, watchdog, hotkey process, Stop button)
* [x] Phase 2 Step 3: Spinal Cord kill switch (flag file in two locations, gate blocks everything, watchdog, hotkey process, Stop button)
* [x] Phase 2 Step 4: Body/Fingers (file operations behind the gate) — file operations verified safe through attack tests
* [ ] Phase 2 Step 5: Body/Hands (mouse and keyboard behind the gate)
* [ ] Phase 2 Step 6: Hypothalamus VRAM-aware model loading
* [ ] Phase 2 Step 7: integration test

### Not Started

* [ ] Phase 2-10 (see Build Phases above)
* [ ] Agent Action Loop: drive-root protection (add volume roots to
      `core/paths.PROTECTED_PREFIXES`), and stop the identity line teaching the
      model to invent a username in a path — see the Session 12 changelog entry

```
* [x] Local Ollama path VERIFIED LIVE — both models answering, routing L1 to 1.5B and L2 to 3B
* [ ] Colab cloud brain — offline, awaiting Jayanthan's notebook restart (tunnel URL unchanged)
