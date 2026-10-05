"""Parietal Lobe — active window tracking.

Answers "which window is the user actually in", which is the question the other five
readings all depend on: a screen capture of the wrong monitor, a context guess based
on a background editor, and a routing hint pointing at tools for an application that
was last focused twenty minutes ago.

**This module does not enumerate windows.** :mod:`body.hands.window_manager` already
does it, through ``ctypes`` against ``EnumWindows``, with the try/except discipline the
Phase 2 Step 5 review established, and it is what ``click`` resolves a window handle
through. A second enumeration path would mean two answers to "what windows exist" and
no way to tell which one the safety checks saw — the same defect that made
``Capture.timestamp`` an ``AttributeError`` and put a working screenshot behind a
500. So this module consumes that one and adds only what it lacks.

What it adds:

* **Foreground vs merely visible.** ``window_manager`` reports every visible
  top-level window; ``GetForegroundWindow`` reports the one with focus. Collapsing
  those two is how an agent decides the user is working in a browser that is sitting
  minimised behind three other windows.
* **Minimised and maximised state.** ``window_manager``'s record carries a rectangle
  and a visibility flag, neither of which distinguishes "minimised to the taskbar" from
  "restored". A minimised window has a ``rect`` that is meaningless for anything the
  agent might do with it, so this module reads the state through ``pygetwindow`` and,
  where that is unavailable, reports ``None`` rather than guessing.
* **Ownership.** The pid is mapped to a process name so a caller can say "Chrome"
  instead of a number. When the foreground window cannot be matched in the enumeration
  — a window with no title, or one that closed between the two calls — a handle-only
  record is returned rather than ``None``, for the reason
  :func:`body.hands.window_manager.active_window` gives.

The title is the sensitive part and is treated as such: it is returned to the caller
under the gate, it never appears in a decision target (see
:mod:`brain.parietal_lobe.parietal_guards`), and
:meth:`WindowTracker.redacted_title` is what a log line or a routing hint should use.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from body.hands import window_manager

from brain.parietal_lobe.exceptions import SystemAwarenessUnavailable

#: How many characters of a window title are safe to put in a log line, a routing
#: hint, or an answer to "what am I working on". Long enough to be useful, short
#: enough that a title which is a whole document's first line does not travel.
TITLE_BUDGET = 80


class WindowTracker:
    """Which window the user is in.

    Attributes:
        windows: Callable returning the visible top-level windows. Injected so the
            region is testable with no display attached — the only way the "the OS
            refused to answer" paths can be exercised at all.
        foreground: Callable returning the focused window handle, or 0.
        states: Callable returning per-hwnd ``{"minimized", "maximized"}``, or None
            when ``pygetwindow`` is not installed.
    """

    def __init__(
        self,
        windows: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        foreground: Optional[Callable[[], int]] = None,
        states: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> None:
        """Wire the tracker.

        Args:
            windows: Window enumerator, defaulting to
                :func:`body.hands.window_manager.list_windows`.
            foreground: Focused-window handle reader, defaulting to ``GetForegroundWindow``.
            states: Per-window state reader, defaulting to ``pygetwindow``.
        """
        self.windows = windows if windows is not None else window_manager.list_windows
        self.foreground = foreground if foreground is not None else _foreground_handle
        self.states = states if states is not None else _pygetwindow_states

    def list(self) -> List[Dict[str, Any]]:
        """Return every visible top-level window, with its state filled in.

        Returns:
            One record per window carrying ``hwnd``, ``title``, ``pid``, ``process``,
            ``rect``, ``visible``, ``minimized`` and ``maximized``.

        Raises:
            SystemAwarenessUnavailable: The platform would not enumerate windows.
                Not raised for an empty list: "no windows exist" is a legitimate
                answer, and reporting it as a failure would make a headless or
                locked-desktop machine look like a broken agent.
        """
        try:
            found = self.windows()
        except Exception as exc:  # pragma: no cover - platform boundary
            raise SystemAwarenessUnavailable(
                f"the window list could not be read: {exc}",
                detail="windows",
            ) from exc
        return [self._describe(window) for window in found or []]

    def active(self) -> Optional[Dict[str, Any]]:
        """Return the window that currently has focus.

        Returns:
            The window record, or None when nothing has focus — which is the normal
            state of a locked screen or a machine sitting at the UAC prompt, and is
            reported as ``None`` rather than as an error or as a guess about the
            last window that had focus.

        Raises:
            SystemAwarenessUnavailable: The platform would not say which window has
                focus, as opposed to saying "none".
        """
        try:
            handle = self.foreground()
        except Exception as exc:  # pragma: no cover - platform boundary
            raise SystemAwarenessUnavailable(
                f"the focused window could not be read: {exc}",
                detail="active-window",
            ) from exc
        if not handle:
            return None
        for window in self.list():
            if window["hwnd"] == int(handle):
                return window
        # Focus is on something the enumeration skipped — a window with no title, or
        # one that closed between the two calls. A handle-only record is more use to a
        # caller than None: it can still be focused by handle, and "ZUZZ knows which
        # window you are in but cannot read its title" is honest.
        return self._describe(
            {
                "hwnd": int(handle),
                "title": "",
                "pid": 0,
                "process": "",
                "rect": {},
                "visible": True,
            }
        )

    def app_of(self, window: Optional[Dict[str, Any]]) -> str:
        """Return the application's name for a window record.

        Prefers the process name over anything guessed from the title. An application
        name is what a routing hint needs ("prefer the browser tools"), and a title
        like ``Invoices — LibreOffice Calc`` gives it away only by a rule that would
        also fire on a document called ``browser.docx``.

        Args:
            window: A window record, or None.

        Returns:
            The bare process name in lower case, e.g. ``chrome``; empty when the
            record carries none.
        """
        if not window:
            return ""
        name = str(window.get("process") or "").strip().lower()
        return name.removesuffix(".exe") if name.endswith(".exe") else name

    def redacted_title(self, window: Optional[Dict[str, Any]], budget: int = TITLE_BUDGET) -> str:
        """Return a window title truncated to a length safe to log or echo.

        Not a redaction in the privacy sense — it does not hide the title, because a
        caller that asked for the window is entitled to it. It bounds it: window titles
        are frequently the first line of whatever the user has open, so a title of
        unbounded length is a document fragment travelling through log files, routing
        hints and chat answers.

        Args:
            window: A window record, or None.
            budget: Maximum characters to keep.

        Returns:
            The truncated title, with an ellipsis when it was cut.
        """
        if not window:
            return ""
        title = str(window.get("title") or "").strip()
        if len(title) <= budget:
            return title
        return title[: max(0, budget - 1)].rstrip() + "…"

    def _describe(self, window: Dict[str, Any]) -> Dict[str, Any]:
        """Return one window record with its minimised state filled in.

        Args:
            window: A raw record from the enumerator.

        Returns:
            The record with ``minimized`` and ``maximized`` added. Both are ``None``
            when the state could not be read, which is different from ``False`` and is
            preserved rather than defaulted: "this window is not minimised" and "ZUZZ
            cannot tell" lead to different decisions about whether to act on it.
        """
        record = dict(window)
        state = {}
        try:
            state = self.states(record) or {}
        except Exception:
            state = {}
        record["minimized"] = state.get("minimized")
        record["maximized"] = state.get("maximized")
        return record


def _foreground_handle() -> int:
    """Return the focused window handle, or 0 when there is none.

    Returns:
        The ``HWND``, as an integer. 0 is Windows' own answer for "no foreground
        window", so it is passed through rather than converted to None here — the
        caller's check is on falsiness either way.
    """
    api = window_manager.user32()
    if api is None:
        raise SystemAwarenessUnavailable(
            "window state needs the Win32 API and this is not Windows",
            detail="active-window",
        )
    return int(api.GetForegroundWindow() or 0)


def _pygetwindow_states(window: Dict[str, Any]) -> Dict[str, Any]:
    """Return a window's minimised and maximised state through ``pygetwindow``.

    ``pygetwindow`` is used for exactly one thing here, and it is the thing
    :mod:`body.hands.window_manager` cannot do: it wraps ``IsIconic`` and
    ``IsZoomed``, so a minimised window can be told from a restored one. The Occipital
    Lobe's capture code already leans on the same library, so this is not a new
    dependency in practice — it is the one already pinned at ``pygetwindow==0.0.9``.

    Args:
        window: A raw window record, carrying ``hwnd``.

    Returns:
        ``{"minimized": bool, "maximized": bool}``.

    Raises:
        SystemAwarenessUnavailable: ``pygetwindow`` is not installed, or refused the
            handle. Callers treat this as "state unknown" rather than propagating it,
            because a window whose state cannot be read is still a real window.
    """
    try:
        import pygetwindow
    except ImportError as exc:
        raise SystemAwarenessUnavailable(
            "pygetwindow is not installed, so minimised state is unavailable "
            "(pip install pygetwindow==0.0.9)",
            detail="window-state",
        ) from exc
    for handle in pygetwindow.getAllWindows():
        if int(handle._hWnd) == int(window.get("hwnd", -1)):  # noqa: SLF001
            return {
                "minimized": bool(handle.isMinimized),
                "maximized": bool(handle.isMaximized),
            }
    raise SystemAwarenessUnavailable(
        f"window {window.get('hwnd')} is not visible to pygetwindow",
        detail="window-state",
    )
