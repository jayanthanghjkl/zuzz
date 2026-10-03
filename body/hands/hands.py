"""Body/Hands — the facade.

One object that owns the mouse, the keyboard, the clipboard and the app manager,
and the place where the four of them are wired to a single actuator, a single
kill switch and a single activity log. It exists so that a caller builds one
thing rather than four, and so that "which desktop is this pointing at" has one
answer.

**Every public method takes a decision first.** The list of methods that do not
is closed, and ``test_public_methods_all_require_a_decision`` in the Hands test
module pins it — a new decision-free method is a test failure, not a quiet
widening of the surface that skips the gate. There are exactly three exceptions,
all of them system-level rather than agent actions:

* :meth:`Hands.describe_layout` — the display layout, which the Desktop tab
  shows without asking, exactly as it shows the trust level without asking;
* :meth:`Hands.describe_policy` — the whitelist and hotkey policy, which are
  configuration the user reads rather than actions ZUZZ takes;
* :meth:`Hands.release_held_keys` — the cleanup path for a failed operation,
  which must be callable precisely because the operation that would otherwise
  authorise it has already failed.

``release_held_keys`` is the reason the rule has an exception at all. A stuck
modifier key is a safety problem rather than a convenience one, and requiring a
fresh decision to undo damage ZUZZ itself caused would be the wrong way round.

**Dry runs never reach this layer.** The gate decides ``dry_run``, the Body
refuses it, and :meth:`~body.hands.hands_guards.verify_decision` raises before
any method body runs. The Desktop tab previews with a recording actuator rather
than by passing a dry-run decision here.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from body.hands import app_registry, hotkey_policy, window_guards
from body.hands.actuator import PyAutoGUIActuator
from body.hands.app_manager import AppManager
from body.hands.clipboard import Clipboard
from body.hands.keyboard import Keyboard
from body.hands.mouse import Mouse
from body.hands.screens import Screens
from body.hands.window_control import WindowControl
from body.hands.window_ops import foreground_probe, is_elevated


class Hands:
    """The desktop, behind the gate.

    Attributes:
        actuator: Where input actually goes.
        screens: The display layout.
        mouse: The pointer.
        keyboard: The keyboard.
        clipboard: The clipboard.
        apps: Applications and processes.
    """

    def __init__(
        self,
        actuator: Optional[Any] = None,
        kill_switch: Optional[Any] = None,
        logger: Optional[Any] = None,
        clipboard_backend: Optional[Any] = None,
        launcher: Optional[Any] = None,
        foreground: Optional[Callable[[], Dict[str, Any]]] = None,
        elevated: Optional[bool] = None,
    ) -> None:
        """Build the Hands layer and wire it together.

        Args:
            actuator: The actuator that sends input. Defaults to the real
                PyAutoGUI actuator; the test suite always passes a
                :class:`~body.hands.recorder.RecordingActuator` instead, so no
                automated test can reach the hardware.
            kill_switch: The switch re-checked on every call. None means the
                process-wide one, resolved per call so a stop engaged after
                construction is still seen.
            logger: Activity logger; None means the process-wide one.
            clipboard_backend: Clipboard implementation; None resolves
                PyAutoGUI's on first use.
            launcher: Callable that starts an application; None uses
                :func:`subprocess.Popen`.
            foreground: Callable returning the focused window's facts. None
                builds one from the real window manager.
            elevated: True when ZUZZ is elevated. None asks the OS, once.
        """
        self.actuator = actuator if actuator is not None else PyAutoGUIActuator()
        self.screens = Screens(self.actuator)
        self.kill_switch = kill_switch
        self.logger = logger
        if elevated is None:
            elevated = is_elevated()
        self.elevated = elevated

        probe = foreground_probe(foreground)
        self.mouse = Mouse(
            self.actuator,
            self.screens,
            kill_switch=kill_switch,
            logger=logger,
            foreground=probe,
            elevated=elevated,
        )
        self.keyboard = Keyboard(
            self.actuator,
            kill_switch=kill_switch,
            logger=logger,
            foreground=probe,
            elevated=elevated,
        )
        self.clipboard = Clipboard(
            backend=clipboard_backend,
            kill_switch=kill_switch,
            logger=logger,
        )
        self.apps = AppManager(
            kill_switch=kill_switch,
            logger=logger,
            launcher=launcher,
            elevated=elevated,
        )
        self.windows = WindowControl(
            kill_switch=kill_switch,
            logger=logger,
            elevated=elevated,
        )

    # -- the four input surfaces -------------------------------------------

    def get_position(self, decision: Any):
        """Return the cursor position."""
        return self.mouse.get_position(decision)

    def move(self, decision: Any, x: Any, y: Any, duration: float = 0.5):
        """Move the pointer."""
        return self.mouse.move(decision, x, y, duration=duration)

    def click(self, decision: Any, x: Any, y: Any, **options: Any):
        """Click at a point."""
        return self.mouse.click(decision, x, y, **options)

    def double_click(self, decision: Any, x: Any, y: Any):
        """Double-click at a point."""
        return self.mouse.double_click(decision, x, y)

    def right_click(self, decision: Any, x: Any, y: Any):
        """Right-click at a point."""
        return self.mouse.right_click(decision, x, y)

    def drag(self, decision: Any, start: Any, end: Any, **options: Any):
        """Drag from one point to another."""
        return self.mouse.drag(decision, start, end, **options)

    def scroll(self, decision: Any, clicks: int, x: Any = None, y: Any = None):
        """Scroll the wheel."""
        return self.mouse.scroll(decision, clicks, x=x, y=y)

    def type_text(self, decision: Any, text: str, interval: float = 0.01):
        """Type a string."""
        return self.keyboard.type_text(decision, text, interval=interval)

    def press(self, decision: Any, key: str, presses: int = 1):
        """Press a key."""
        return self.keyboard.press(decision, key, presses=presses)

    def press_keys(self, decision: Any, keys: Any):
        """Press a sequence of keys."""
        return self.keyboard.press_keys(decision, keys)

    def hotkey(self, decision: Any, keys: Any):
        """Press a key combination."""
        return self.keyboard.hotkey(decision, keys)

    def hold_key(self, decision: Any, key: str):
        """Press and hold a key."""
        return self.keyboard.hold_key(decision, key)

    def release_key(self, decision: Any, key: str):
        """Release a held key."""
        return self.keyboard.release_key(decision, key)

    def clipboard_read(self, decision: Any):
        """Read the clipboard."""
        return self.clipboard.read(decision)

    def clipboard_write(self, decision: Any, text: str):
        """Replace the clipboard contents."""
        return self.clipboard.write(decision, text)

    def clipboard_clear(self, decision: Any):
        """Empty the clipboard."""
        return self.clipboard.clear(decision)

    def open_app(self, decision: Any, name: str):
        """Launch an application."""
        return self.apps.open_app(decision, name)

    def close_app(self, decision: Any, name: str):
        """Ask an application to close."""
        return self.apps.close_app(decision, name)

    def kill_process(self, decision: Any, pid: int):
        """Terminate a process immediately."""
        return self.apps.kill_process(decision, pid)

    def list_running_apps(self, decision: Any):
        """Return the processes currently running."""
        return self.apps.list_running(decision)

    # -- windows -----------------------------------------------------------

    def list_windows(self, decision: Any) -> List[Dict[str, Any]]:
        """Return every visible top-level window."""
        return self.windows.list_windows(decision)

    def get_active_window(self, decision: Any) -> Any:
        """Return the window that has focus."""
        return self.windows.get_active_window(decision)

    def focus_window(self, decision: Any, window: Any) -> Dict[str, Any]:
        """Bring a window to the foreground."""
        return self.windows.focus_window(decision, window)

    def set_window_state(self, decision: Any, window: Any, state: str) -> Dict[str, Any]:
        """Minimise, maximise or restore a window."""
        return self.windows.set_state(decision, window, state)

    def resize_window(
        self,
        decision: Any,
        window: Any,
        left: Any,
        top: Any,
        width: Any,
        height: Any,
    ) -> Dict[str, Any]:
        """Move and resize a window."""
        return self.windows.resize(decision, window, left, top, width, height)

    # -- the documented exceptions -----------------------------------------

    def release_held_keys(self):
        """Release every key ZUZZ is holding.

        The one input method that takes no decision, because it undoes damage
        ZUZZ itself caused. The operation that would authorise it has already
        failed, and requiring a fresh decision to release a stuck modifier would
        leave the user's keyboard in a state they never chose.
        """
        return self.keyboard.release_all()

    def describe_layout(self):
        """Return the display layout for the Desktop tab.

        Configuration the user reads rather than an action ZUZZ takes, so it
        asks nothing — exactly like showing the current trust level.
        """
        return self.screens.describe()

    def describe_policy(self):
        """Return the application and hotkey policy for the Desktop tab.

        Read-only configuration. Showing the user exactly what ZUZZ will refuse
        is the opposite of an action.
        """
        return {
            "apps": app_registry.describe(),
            "hotkeys": hotkey_policy.describe(),
            "windows": window_guards.describe(),
        }