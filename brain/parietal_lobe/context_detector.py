"""Parietal Lobe — activity inference.

Answers "what is the user doing right now", which is what the Thalamus wants in order
to prefer a coding-focused model while an editor is focused, and what the Dashboard
shows so the user can check the inference against their own behaviour.

**This is inference, and it is labelled as inference.** Every result carries the
:class:`~brain.parietal_lobe.context_detector.Context` that produced it — the window
title and process it matched, the rule that fired, and a confidence — so a caller can
see *why* and disagree. A bare ``"coding"`` with nothing behind it is indistinguishable
from a guess, and a guess about what a person is doing is exactly the kind of answer
ZUZZ must not deliver dressed as a fact.

Three rules keep it honest, and each one exists because of a specific way this could
otherwise lie:

* **Absence of a match is ``unknown``, never ``idle``.** A window ZUZZ cannot classify
  — a PDF viewer, a game it has never heard of, a title-less window — is not evidence
  that the user is doing nothing. ``idle`` has exactly one source: the OS's own
  input-idle timer. Conflating the two would make ZUZZ declare a person idle while they
  are reading a document, which is a claim about a human being and the worst kind of
  wrong for an assistant to make.
* **The focused window wins, and background windows do not vote.** A user with an IDE
  and a browser open is *in* one of them. Scoring every window would let a minimised
  browser outweigh the editor in front of it.
* **Input idleness outranks the window.** If the pointer and keyboard have been still
  for longer than the idle threshold, the user is idle whatever has focus — someone
  reading an article for six minutes is not "working in Chrome".

Title matching is keyword-based rather than model-based on purpose. A 3B model asked
"what is this person doing" from one window title will produce a confident sentence, and
a rule produces either a match with its evidence or nothing at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

#: The contexts this detector will report. A closed set, because the Thalamus routes on
#: it and an open-ended label would need a fallback for every consumer.
CODING = "coding"
BROWSING = "browsing"
WRITING = "writing"
READING = "reading"
GAMING = "gaming"
IDLE = "idle"
UNKNOWN = "unknown"

CONTEXTS = (CODING, BROWSING, WRITING, READING, GAMING, IDLE, UNKNOWN)

#: How long the keyboard and mouse must be still before the user counts as idle.
#: Two minutes: long enough that a person reading a paragraph on the same page is not
#: declared idle, short enough that walking away from the desk is noticed.
IDLE_SECONDS = 120

#: Per-context match rules. Ordered by specificity within each group, and the whole
#: table is checked against the focused window only.
#:
#: ``process`` entries match the executable name, which is the stronger signal — a
#: process called ``Code.exe`` is an editor whatever the document is called. ``title``
#: entries match the window title, which catches editors and viewers ZUZZ does not know
#: by name.
RULES: Dict[str, Dict[str, Sequence[str]]] = {
    CODING: {
        "process": (
            "code", "code.exe", "devenv", "pycharm", "pycharm64", "idea64", "idea",
            "rider64", "rider", "webstorm64", "webstorm", "sublime_text", "notepad++",
            "vim", "nvim", "emacs", "zed", "cursor", "antigravity ide",
            "antigravity ide.exe", "studio64", "eclipse", "intellij", "fleet",
        ),
        "title": (
            r"\.py\b", r"\.js\b", r"\.ts\b", r"\.tsx\b", r"\.jsx\b", r"\.java\b",
            r"\.go\b", r"\.rs\b", r"\.c\b", r"\.cpp\b", r"\.h\b", r"\.cs\b",
            r"\.rb\b", r"\.php\b", r"\.sql\b", r"\.sh\b", r"\.yaml\b", r"\.yml\b",
            r"\.json\b", r"\.toml\b",
            r"\bdef \w+\s*\(", r"\bclass \w+", r"\bfunction \w+\s*\(",
            r"\bimport \w+", r"\breturn\b.*[;{]",
        ),
    },
    BROWSING: {
        "process": (
            "chrome", "chrome.exe", "msedge", "msedge.exe", "firefox", "firefox.exe",
            "brave", "brave.exe", "opera", "opera.exe", "vivaldi", "arc.exe",
            "librewolf", "waterfox", "safari",
        ),
        "title": (
            r"\bhttps?://", r"\bwww\.", r"\bgithub\.com\b", r"\bstackoverflow\b",
            r"\bwikipedia\b", r"\breddit\b", r"\byoutube\b",
        ),
    },
    WRITING: {
        "process": (
            "winword", "winword.exe", "wordpad", "notepad", "notepad.exe",
            "libreoffice", "soffice", "libreoffice-writer", "pages",
        ),
        "title": (
            r"\.docx?\b", r"\.odt\b", r"\.rtf\b", r"\.pages\b", r"\.md\b",
            r"\.txt\b",
        ),
    },
    READING: {
        "process": (
            "acrord32", "acrord32.exe", "zathura", "evince", "okular", "sumatra",
            "fbreader", "calibre",
        ),
        "title": (
            r"\.pdf\b", r"\.epub\b", r"\.mobi\b", r"\breader\b",
        ),
    },
    GAMING: {
        "process": (
            "steam.exe", "steam", "steamwebhelper", "epicgameslauncher",
            "battle.net", "riotclientservices", "gamelaunchhelper",
            "minecraft", "javaw.exe",
        ),
        "title": (r"\bsteam\b", r"\bgame\b"),
    },
}

#: Process names, kept as a plain set and deliberately **not** compiled. These are
#: compared for equality against the executable name, not searched for inside a title,
#: so a regex here would only add a way to get the escaping wrong. The two kinds of
#: rule are held separately because they are two different kinds of match: one is
#: identity, the other is a pattern.
_PROCESS_NAMES: Dict[str, set] = {
    context: {name.lower().removesuffix(".exe") for name in groups.get("process", ())}
    for context, groups in RULES.items()
}

#: Title patterns, compiled once at import. A pattern that fails to compile is dropped
#: rather than allowed to raise at request time — a broken regex must not take the
#: region down, and the remaining patterns still apply.
_COMPILED_TITLES: Dict[str, List[Any]] = {
    context: [
        pattern
        for raw in groups.get("title", ())
        for pattern in (re.compile(raw, re.IGNORECASE),)
        if pattern is not None
    ]
    for context, groups in RULES.items()
}


@dataclass(frozen=True)
class Context:
    """One inference, with the evidence behind it.

    Attributes:
        name: The context, one of :data:`CONTEXTS`.
        confidence: 0.0–1.0. Derived from the kind of evidence, never invented:
            a process-name match is stronger than a title match, and a title match is
            stronger than idle-time alone.
        reason: One line naming the rule that fired.
        evidence: The window title and process it came from, already truncated by
            :meth:`~brain.parietal_lobe.window_tracker.WindowTracker.redacted_title`.
        idle_seconds: The OS input-idle reading, when it was consulted.
        window: The window the inference is about, or None for a pure idle reading.
    """

    name: str
    confidence: float
    reason: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    idle_seconds: Optional[int] = None
    window: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return the inference as plain data for the API and the model."""
        return {
            "context": self.name,
            "confidence": self.confidence,
            "reason": self.reason,
            "evidence": self.evidence,
            "idle_seconds": self.idle_seconds,
        }

    def summary(self) -> str:
        """Return one line naming the context and what it was based on.

        Returns:
            A string safe to put in a routing hint or a chat answer, because it is
            built from the context name and the *application*, never from the
            document title.
        """
        app = str(self.evidence.get("app") or "").strip()
        where = f" in {app}" if app else ""
        return f"{self.name}{where} ({self.reason})"


class ContextDetector:
    """Infers what the user is doing from the focused window and input idleness.

    Attributes:
        idle: Callable returning whole seconds since the last keyboard or mouse
            input, or None when the OS will not say.
    """

    def __init__(self, idle: Optional[Callable[[], Optional[int]]] = None) -> None:
        """Wire the detector.

        Args:
            idle: Input-idle reader, defaulting to ``GetLastInputInfo``. Injected so
                the region is testable with no display attached, and so the idle path
                can be tested at all — it is the one branch a live machine will rarely
                take.
        """
        self.idle = idle if idle is not None else _idle_seconds

    def detect(self, window: Optional[Dict[str, Any]]) -> Context:
        """Infer the user's activity.

        Args:
            window: The focused window record, or None when nothing has focus.

        Returns:
            A :class:`Context`. Never raises and never returns a bare label: the
            worst case is :attr:`Context.name` of ``unknown`` with a reason saying
            what was missing, which is a usable answer.
        """
        idle_seconds = self._idle()
        window = dict(window) if window else None

        if idle_seconds is not None and idle_seconds >= IDLE_SECONDS:
            return Context(
                name=IDLE,
                confidence=0.9,
                reason=f"no keyboard or mouse input for {idle_seconds}s",
                idle_seconds=idle_seconds,
                evidence={"app": _app_of(window), "title": _title_of(window)},
                window=window,
            )

        match = _match_window(window)
        if match is None:
            return Context(
                name=UNKNOWN,
                confidence=0.0,
                reason=(
                    "nothing has focus"
                    if window is None
                    else "no rule matched the focused window"
                ),
                idle_seconds=idle_seconds,
                evidence={"app": _app_of(window), "title": _title_of(window)},
                window=window,
            )

        context, confidence, reason = match
        return Context(
            name=context,
            confidence=confidence,
            reason=reason,
            idle_seconds=idle_seconds,
            evidence={"app": _app_of(window), "title": _title_of(window)},
            window=window,
        )

    def _idle(self) -> Optional[int]:
        """Return whole seconds since the last input, or None when unknowable."""
        try:
            value = self.idle()
        except Exception:
            return None
        return None if value is None else max(0, int(value))


def _match_window(window: Optional[Dict[str, Any]]):
    """Return ``(context, confidence, reason)`` for a window, or None.

    Args:
        window: The focused window record.

    Returns:
        The winning context, a confidence derived from which kind of rule fired, and a
        reason naming it — or None when nothing matched.
    """
    if not window:
        return None
    process = str(window.get("process") or "").strip().lower().removesuffix(".exe")
    title = str(window.get("title") or "")
    for context in RULES:
        # Identity first and per-context, so an editor called "Chrome DevTools"
        # still reads as coding when the process is the IDE. Falling out of the loop
        # on the first process match also keeps the rule order meaningful: the groups
        # are checked in declaration order, which is how a title rule for a browser
        # loses to the process rule for the editor it is embedded in.
        if process and process in _PROCESS_NAMES[context]:
            return context, 0.95, f"the focused application is {process}"
    for context in RULES:
        for pattern in _COMPILED_TITLES[context]:
            if pattern.search(title):
                return (
                    context,
                    0.7,
                    f"the window title matches {pattern.pattern!r}",
                )
    return None


def _app_of(window: Optional[Dict[str, Any]]) -> str:
    """Return the application name for a window record, without its extension."""
    if not window:
        return ""
    return str(window.get("process") or "").strip().lower().removesuffix(".exe")


def _title_of(window: Optional[Dict[str, Any]], budget: int = 80) -> str:
    """Return a window title truncated to a length safe to log."""
    if not window:
        return ""
    title = str(window.get("title") or "").strip()
    return title if len(title) <= budget else title[: budget - 1].rstrip() + "…"


def _idle_seconds() -> Optional[int]:
    """Return whole seconds since the last keyboard or mouse input.

    Returns:
        Seconds, or None when the OS will not say. None rather than 0: zero is a real
        reading — the user touched something a moment ago — and defaulting an unknown
        reading to zero would turn "ZUZZ cannot tell" into "the user is active", which
        would suppress the idle path entirely on any machine where the call fails.
    """
    try:  # pragma: no cover - Windows only
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        elapsed = ctypes.windll.kernel32.GetTickCount() - info.dwTime
        return int(elapsed // 1000)
    except Exception:
        return None
