"""Prefrontal Cortex — Action Model.

One action, as the brain proposes it, before any permission decision. The gate
reads these fields; the Body layer executes them. Nothing here decides anything
— deciding is :mod:`brain.prefrontal_cortex.permission_gate`'s job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict


class ActionCategory(str, Enum):
    """The kinds of action the gate knows about.

    The values match the keys in ``config/trust_levels.json`` so the matrix can
    be edited without touching code. Adding a category here without adding a row
    to that file makes the gate refuse the action, which is the safe direction
    to fail.
    """

    CHAT = "chat"
    READ_FILES = "read_files"
    OPEN_APPS = "open_apps"
    CLOSE_APPS = "close_apps"
    FULLSCREEN_CONTROL = "fullscreen_control"
    MOUSE_KEYBOARD = "mouse_keyboard"
    RUN_SAFE_COMMANDS = "run_safe_commands"
    WRITE_FILES = "write_files"
    BROWSE = "browse"
    RUN_PYTHON = "run_python"
    MODIFY_PLUGINS = "modify_plugins"
    DELETE_FILES = "delete_files"
    INSTALL_SOFTWARE = "install_software"
    SEND_MESSAGES = "send_messages"
    RUN_ADMIN = "run_admin"
    ACCESS_CREDENTIALS = "access_credentials"
    DELETE_OS_FILES = "delete_os_files"
    SHARE_DATA = "share_data"
    DISABLE_SAFETY = "disable_safety"
    #: Added by Session 11 for the Occipital Lobe. A screenshot is a read, but a
    #: more exposed one than ``read_files``: a single frame routinely holds a
    #: logged-in banking session, a password field or a private message, and the
    #: Body layer's refusal of a credential file has no equivalent to offer here,
    #: because the pixels are not a file. It therefore gets its own row rather
    #: than borrowing ``read_files``, so the two can be tuned apart on the
    #: evidence rather than on a guess about which number looks sensible.
    VISUAL_CAPTURE = "visual_capture"
    #: Added by Phase 3 Step 2 for Body/Legs. The existing ``browse`` row covers
    #: *reaching* a page, which is why it stays where it is. These three split out
    #: the parts of using a browser that are not reaching one, because they are not
    #: the same risk and tuning them together is how a browser ends up either
    #: unusable or unchecked.
    #:
    #: * ``browse_internet`` — navigating to, and reading, a URL. The body of what
    #:   ZUZZ does online: a page is untrusted content arriving, not an action
    #:   taken on the machine, so it is asked for right up to full autonomy and only
    #:   handed over at level 5.
    #: * ``web_automation`` — clicking, typing, selecting and submitting. These act
    #:   *on* the world: a click can send a message, place an order or delete
    #:   something on a site ZUZZ is merely reading. They therefore sit where
    #:   ``mouse_keyboard`` sits, not where ``browse`` does — trusted as the user
    #:   trusts a click, and no more trusted just because the click is remote.
    #: * ``download_files`` — fetching a file off a site and putting it on disk.
    #:   Its own row because it is the one that leaves the machine: the bytes are
    #:   unreviewed, the destination is a quarantine rather than a folder the user
    #:   chose, and an executable is the specific shape of this that has ended with
    #:   people running installers they never saw.
    BROWSE_INTERNET = "browse_internet"
    WEB_AUTOMATION = "web_automation"
    DOWNLOAD_FILES = "download_files"
    #: Added by Phase 3 Step 3 for the Parietal Lobe. Its own row rather than
    #: borrowing ``read_files``, and the reason is not politeness about a gauge —
    #: it is that a window title is not a file. Reading ``notes.txt`` shows the
    #: user what ZUZZ read; reading the title bar shows whatever the user's open
    #: document happens to be called, which for many people is a customer name, a
    #: salary figure or the first line of something they have not finished
    #: typing. That is a different disclosure with a different shape, so it gets
    #: its own row and can be tuned on the evidence rather than by analogy.
    #:
    #: The row itself is ``auto`` at every level, and that is a considered
    #: decision rather than an oversight. ``visual_capture`` asks at levels 1 and
    #: 2 because a screenshot can hold a bank portal; a title bar cannot hold a
    #: rendered form, and ZUZZ reads it without sending it anywhere — the
    #: inference stays local and only the resulting category reaches the model.
    #: Trust level 1 is "ask permission for everything", so this is the one
    #: documented departure from that, and :mod:`brain.parietal_lobe` states it
    #: in its own module docstring rather than leaving it to be discovered.
    SYSTEM_AWARENESS = "system_awareness"
    #: Added by Phase 6 for the Hippocampus. Its own row rather than borrowing
    #: ``read_files``, and the reason is that memory is a *disclosure with a
    #: lifetime*, which a file read is not. Reading ``notes.txt`` shows the user
    #: exactly what ZUZZ read and the read ends; a recall can return a
    #: preference the user gave in a conversation three months ago and act on it,
    #: which is a different kind of exposure from the one ``read_files`` was
    #: tuned for.
    #:
    #: The row is ``auto`` at every level, which is the same deliberate departure
    #: ``system_awareness`` documents, for the same underlying reason. Memory is
    #: local — nothing here sends anything off the machine — and the alternative
    #: is a prompt every time ZUZZ says "as you mentioned", which trains people to
    #: click prompts through. That is the failure mode that makes prompts worse
    #: than none, and it is why the memory page exists instead: every stored
    #: memory is listed, readable and erasable by hand, so the permission the row
    #: withholds is available on demand rather than as a dialog.
    #:
    #: What this row does NOT cover: erasing. ``forget_before`` is the one
    #: irreversible operation in the region and it is reachable at every level,
    #: which is a real trade-off rather than an oversight — the counter-argument
    #: is that a machine which cannot forget is a machine that holds a wrong
    #: statement about its user forever with no recourse. The mitigation is
    #: structural rather than permission-based: :class:`MemoryRefused` makes an
    #: unreadable date an error rather than a silent "forget everything", so the
    #: destructive direction always requires the caller to state a real date.
    MEMORY_ACCESS = "memory_access"
    #: Added by Phase 6 for the Basal Ganglia. Deliberately *not* auto at the low
    #: levels even though listing and reading a routine is harmless, because the
    #: category covers writing them, and a row that is loose for reading has to
    #: be loose for writing too — the matrix has no per-operation axis.
    #:
    #: The shape borrowed is ``mouse_keyboard``'s, which is the right neighbour:
    #: a routine is a stored bundle of desktop acts, and the user approved those
    #: acts once, individually, without them being bundled. So from level 3, where
    #: the user trusts ZUZZ with the desktop, they trust it with a saved sequence
    #: of desktop acts. It asks at 1 and 2, where evicting or rewriting a routine
    #: is the assistant reorganising its own authority rather than the user's own
    #: housekeeping.
    #:
    #: A routine is never run without its steps being re-checked against the
    #: gate at the current trust level — the stored decision that built it is not
    #: reused. A routine created at level 5 and executed at level 1 runs nothing
    #: that level 1 would refuse, which is why storing one does not store a
    #: permission.
    ROUTINE_MANAGEMENT = "routine_management"
    #: Added by Phase 6 for the Cerebellum, and the third row in this session
    #: that is ``auto`` at every level.
    #:
    #: What it covers is narrow on purpose: reading the register, moving one
    #: editable knob, and detecting the tone of a single message. The knob is the
    #: part worth being careful about, and it is bounded structurally rather than
    #: by permission — only the keys the Cerebellum itself lists as editable can be
    #: set, and the identity of the owner is not among them. That is what keeps
    #: ``personality_update_setting("name", ...)`` from being a thing the model can
    #: do by accident, and it is why this row needs no separate stricter row for
    #: the write half the way ``routine_management`` does.
    #:
    #: A prompt here would be worse than nothing. ZUZZ choosing a terser register
    #: because the user is frustrated is the feature; ask first and it becomes
    #: "ZUZZ would like to adjust its tone, approve?" every time someone is under
    #: pressure, which is precisely when the answer will be clicked through without
    #: being read.
    PERSONALITY_ACCESS = "personality_access"
    #: Added by Phase 4 for ``body/mouth/kilo``, the coding agents. Five rows, and
    #: the shape of each is argued rather than inherited — a permission row that is
    #: merely a neighbour's shape stops being re-examined when the neighbour changes.
    #:
    #: * ``generate_code`` — source text produced from a description and a failure.
    #:   It reads the user's problem and acts on nothing else, so the exposure is the
    #:   model's reasoning leaving the machine. It borrows ``mouse_keyboard``'s shape
    #:   (ask at 1 and 2, auto from 3) rather than ``read_files``'s, because at level 2
    #:   a read is trusted with a file the user pointed at and this is not that.
    #:
    #: * ``execute_code`` — running a test suite or a script in a workspace. It is
    #:   deliberately identical to ``run_python``, because a test file an agent wrote
    #:   a moment ago is not a gentler thing to run than one the user wrote.
    #:
    #: * ``modify_project`` — writing generated code into a user-designated workspace.
    #:   Its own row rather than borrowing ``write_files`` because it is *stricter*:
    #:   ``write_files`` auto-approves from level 2 and this asks until 3. That gap is
    #:   the whole point — from level 3 the user trusts ZUZZ with the desktop, and
    #:   only then with a directory of files it rewrote in one go.
    #:
    #: * ``git_operations`` — commits, branches, and history. Reads (``status``,
    #:   ``log``, ``diff``) are not this category; they are ``read_files``, and
    #:   ``body.mouth.kilo.kilo_git`` maps them there rather than borrowing a wider
    #:   row for a read. Force-push and ``reset --hard`` are refused outright at every
    #:   level, in that module, because a row cannot say "except the destructive ones".
    #:
    #: * ``install_packages`` — ``pip install``. See the note in
    #:   ``config/trust_levels.json``: it asks at all five levels, because immutable
    #:   rule R7 does not have a trust-level escape hatch and a wheel executes its
    #:   author's build hooks.
    GENERATE_CODE = "generate_code"
    EXECUTE_CODE = "execute_code"
    MODIFY_PROJECT = "modify_project"
    GIT_OPERATIONS = "git_operations"
    INSTALL_PACKAGES = "install_packages"
    DEPLOY_AGENTS = "deploy_agents"
    #: Added by Phase 7 for Mirror Neurons. Observation records what the user
    #: does on their desktop. It asks at levels 1 and 2 because the stream of
    #: actions can include private workflows, and auto-approves from level 3
    #: where the user has explicitly raised trust for desktop awareness.
    OBSERVE_USER = "observe_user"
    #: Added by Phase 7 for Mirror Neurons. Creating a macro stores a reusable
    #: routine. It asks at levels 1-3 because a macro writes a persistent
    #: automation, and auto-approves only from level 4 where the user trusts
    #: ZUZZ with routine management.
    CREATE_MACRO = "create_macro"
    #: Added by Phase 7 for Multi-Agent Orchestrator. Deploying a team of
    #: specialist agents runs multiple Cerebrum calls sequentially. It asks at
    #: levels 1 and 2 because each agent acts through the Cerebrum, and
    #: auto-approves from level 3 where the user trusts ZUZZ with complex tasks.
    DEPLOY_AGENT_TEAM = "deploy_agent_team"

    def __str__(self) -> str:
        """Return the config key for this category."""
        return str(self.value)


@dataclass(frozen=True)
class Action:
    """A proposed action awaiting a permission decision.

    Attributes:
        category: What kind of action this is.
        target: What it acts on — a path, a command, a URL, an app name.
        detail: Extra context for logging and for the Amygdala's scoring.
        dry_run: When True the Body layer must describe the action instead of
            performing it.
    """

    category: ActionCategory
    target: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False

    @property
    def is_file_write(self) -> bool:
        """Return True for actions that create or modify a file."""
        return self.category in (ActionCategory.WRITE_FILES, ActionCategory.RUN_PYTHON)

    @property
    def is_destructive(self) -> bool:
        """Return True for actions that can destroy data."""
        return self.category in (
            ActionCategory.DELETE_FILES,
            ActionCategory.DELETE_OS_FILES,
            ActionCategory.CLOSE_APPS,
        )

    def describe(self) -> str:
        """Return a one-line human description for the UI and the log.

        Returns:
            For example ``delete files at C:\\Users\\me\\notes.txt``.
        """
        verb = str(self.category).replace("_", " ")
        suffix = f" {self.target}" if self.target else ""
        prefix = "would " if self.dry_run else ""
        return f"{prefix}{verb}{suffix}"