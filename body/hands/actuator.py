"""Body/Hands — the actuator.

Every call that physically moves the mouse, presses a key or touches the
clipboard goes through one object implementing :class:`Actuator`. Nothing else in
the project imports PyAutoGUI.

That indirection is not architecture for its own sake. It is what makes the
attack tests possible: :class:`RecordingActuator` records what *would* have been
sent instead of sending it, so the full test suite can prove that a refused
action reaches the hardware boundary zero times, on a machine the developer is
sitting at. A test that moved a real cursor would be a test that could not be
run.

It also gives the UI its dry-run preview for free — the preview is a recording,
rendered.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, Tuple

from body.hands.exceptions import ActuatorError, FailSafeTriggered

#: Seconds PyAutoGUI pauses after every call. Not zero: a back-to-back click
#: sequence is how double-clicks and racing widgets get missed.
PAUSE = 0.1


class Actuator(Protocol):
    """The physical boundary: everything the Hands can do to the desktop."""

    def position(self) -> Tuple[int, int]:
        """Return the cursor's current ``(x, y)``."""

    def size(self) -> Tuple[int, int]:
        """Return the primary display's ``(width, height)`` in pixels."""

    def move_to(self, x: int, y: int, duration: float = 0.5) -> None:
        """Move the cursor to an absolute position."""

    def click(
        self,
        x: int,
        y: int,
        button: str = "left",
        clicks: int = 1,
        interval: float = 0.1,
    ) -> None:
        """Click at an absolute position."""

    def scroll(self, clicks: int, x: Optional[int] = None,
               y: Optional[int] = None) -> None:
        """Scroll by a number of notches, optionally at a position."""

    def write(self, text: str, interval: float = 0.01) -> None:
        """Type a string as keystrokes."""

    def press(self, key: str) -> None:
        """Press and release one key."""

    def key_down(self, key: str) -> None:
        """Press a key and leave it held."""

    def key_up(self, key: str) -> None:
        """Release a held key."""

    def hotkey(self, *keys: str) -> None:
        """Press a combination in order and release it in reverse."""


class PyAutoGUIActuator:
    """Drives the real desktop through PyAutoGUI.

    PyAutoGUI is imported inside the constructor, not at module scope, so the
    rest of ZUZZ — and every test — imports cleanly on a machine where it is not
    installed or where there is no desktop session.

    Two settings are forced here and are not configurable:

    * ``FAILSAFE = True`` — PyAutoGUI aborts by itself when the cursor reaches a
      screen corner. It is the third and last independent stop, after the gate
      and the hotkey, and it works even when both of those were satisfied.
    * ``PAUSE`` — a short settle between calls.
    """

    def __init__(self, pause: float = PAUSE, failsafe: bool = True) -> None:
        """Import PyAutoGUI and arm its fail-safe.

        Args:
            pause: Seconds to settle after each call.
            failsafe: Left as ``True`` in every configuration; present so a
                reader can see it is a decision and not an omission.

        Raises:
            ActuatorError: When PyAutoGUI is not installed, or there is no
                desktop session to drive.
        """
        try:
            import pyautogui
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ActuatorError(
                "PyAutoGUI is required for desktop control. Install it with "
                "'pip install pyautogui', or use the Files tab instead."
            ) from exc
        self._gui = pyautogui
        self._gui.FAILSAFE = failsafe
        self._gui.PAUSE = pause

    def position(self) -> Tuple[int, int]:
        """Return the cursor position, honouring the fail-safe."""
        return self._guard(self._gui.position)

    def size(self) -> Tuple[int, int]:
        """Return the primary display size."""
        width, height = self._gui.size()
        return int(width), int(height)

    def move_to(self, x: int, y: int, duration: float = 0.5) -> None:
        """Move the cursor, honouring the fail-safe."""
        self._guard(self._gui.moveTo, x, y, duration=duration)

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1,
              interval: float = 0.1) -> None:
        """Click at a position."""
        self._guard(
            self._gui.click, x, y, clicks=clicks, interval=interval,
            button=button,
        )

    def scroll(self, clicks: int, x: Optional[int] = None,
               y: Optional[int] = None) -> None:
        """Scroll, optionally after moving to a position."""
        if x is not None and y is not None:
            self._guard(self._gui.moveTo, x, y)
        self._guard(self._gui.scroll, clicks)

    def write(self, text: str, interval: float = 0.01) -> None:
        """Type a string."""
        self._guard(self._gui.write, text, interval=interval)

    def press(self, key: str) -> None:
        """Press and release one key."""
        self._guard(self._gui.press, key)

    def key_down(self, key: str) -> None:
        """Press a key and hold it."""
        self._guard(self._gui.keyDown, key)

    def key_up(self, key: str) -> None:
        """Release a held key."""
        self._guard(self._gui.keyUp, key)

    def hotkey(self, *keys: str) -> None:
        """Press a combination and release it in reverse order."""
        self._guard(self._gui.hotkey, *keys)

    def _guard(self, call: Any, *args: Any, **kwargs: Any) -> Any:
        """Run a PyAutoGUI call, translating its fail-safe into our own.

        PyAutoGUI raises ``FailSafeException`` when the cursor hits a corner.
        That exception must not reach the API as a 500: it is a safety stop
        firing, so it becomes :class:`~body.hands.exceptions.FailSafeTriggered`
        and the UI can say so plainly.

        Args:
            call: The PyAutoGUI function to run.
            *args: Positional arguments for it.
            **kwargs: Keyword arguments for it.

        Returns:
            Whatever the call returned.

        Raises:
            FailSafeTriggered: The cursor reached a corner.
        """
        try:
            return call(*args, **kwargs)
        except self._gui.FailSafeException as exc:
            raise FailSafeTriggered(
                "stopped: the cursor reached a screen corner, which is "
                "PyAutoGUI's fail-safe. Nothing further was sent."
            ) from exc
