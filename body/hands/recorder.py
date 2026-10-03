"""Body/Hands — the recording actuator.

:class:`~body.hands.actuator.RecordingActuator` lives here rather than beside
the real one so that :mod:`body.hands.actuator` stays about the hardware
boundary and nothing else.

The recording actuator is not a test double that pretends to be convenient. It
is the mechanism that makes the attack tests meaningful: the suite asserts on an
empty ``calls`` list to prove a refused action never reached the hardware, and
that assertion is only possible because the hardware is behind an object the
test owns.

It also backs the Desktop tab's dry-run preview, where the recording *is* the
preview: the user is shown the exact sequence that would be sent.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from body.hands.exceptions import FailSafeTriggered


class RecordingActuator:
    """Records actions instead of performing them.

    Attributes:
        calls: Every call made, in order, as ``{"op": ..., ...}`` dicts. An
            empty list means nothing was sent to the desktop.
        cursor: The simulated cursor position, so a sequence of moves and
            clicks lands where it would on a real screen.
        screen: The simulated primary display size.
        fail_on_corner: When True, moving into a corner raises the fail-safe,
            so the suite can prove the third line of defence fires.
    """

    def __init__(
        self,
        size: Tuple[int, int] = (1920, 1080),
        cursor: Tuple[int, int] = (960, 540),
        fail_on_corner: bool = False,
    ) -> None:
        """Configure the simulated desktop.

        Args:
            size: The simulated display size.
            cursor: Where the simulated cursor starts.
            fail_on_corner: Raise the fail-safe when the cursor enters a
                screen corner.
        """
        self.calls: List[Dict[str, Any]] = []
        self._size = size
        self._cursor = cursor
        self.fail_on_corner = fail_on_corner

    # -- Actuator ---------------------------------------------------------

    def position(self) -> Tuple[int, int]:
        """Return the simulated cursor position."""
        self.calls.append({"op": "position"})
        return self._cursor

    def size(self) -> Tuple[int, int]:
        """Return the simulated display size."""
        self.calls.append({"op": "size"})
        return self._size

    def move_to(self, x: int, y: int, duration: float = 0.5) -> None:
        """Record a move and update the simulated cursor."""
        self.calls.append(
            {"op": "move_to", "x": x, "y": y, "duration": duration}
        )
        self._cursor = (x, y)
        self._check_corner(x, y)

    def click(
        self,
        x: int,
        y: int,
        button: str = "left",
        clicks: int = 1,
        interval: float = 0.1,
    ) -> None:
        """Record a click."""
        self.calls.append(
            {
                "op": "click",
                "x": x,
                "y": y,
                "button": button,
                "clicks": clicks,
                "interval": interval,
            }
        )

    def scroll(
        self, clicks: int, x: Optional[int] = None, y: Optional[int] = None
    ) -> None:
        """Record a scroll."""
        self.calls.append(
            {"op": "scroll", "clicks": clicks, "x": x, "y": y}
        )

    def write(self, text: str, interval: float = 0.01) -> None:
        """Record typed text."""
        self.calls.append(
            {"op": "write", "text": text, "interval": interval}
        )

    def press(self, key: str) -> None:
        """Record a key press."""
        self.calls.append({"op": "press", "key": key})

    def key_down(self, key: str) -> None:
        """Record a key going down."""
        self.calls.append({"op": "key_down", "key": key})

    def key_up(self, key: str) -> None:
        """Record a key coming up."""
        self.calls.append({"op": "key_up", "key": key})

    def hotkey(self, *keys: str) -> None:
        """Record a hotkey combination."""
        self.calls.append({"op": "hotkey", "keys": list(keys)})

    # -- helpers ----------------------------------------------------------

    def ops(self) -> List[str]:
        """Return the operation names called, in order.

        Returns:
            Just the names, which is what most assertions want.
        """
        return [call["op"] for call in self.calls]

    def _check_corner(self, x: int, y: int) -> None:
        """Raise the fail-safe when the cursor enters a corner.

        Real PyAutoGUI checks the corner before every call, not just on
        moves, so a drag that ends in a corner is caught too. The recording
        actuator checks on move because that is where a corner is reached;
        a caller that wants the stricter behaviour drives it through
        :class:`~body.hands.actuator.PyAutoGUIActuator`.

        Args:
            x: Target x.
            y: Target y.

        Raises:
            FailSafeTriggered: The cursor entered a corner.
        """
        if not self.fail_on_corner:
            return
        width, height = self._size
        if (x <= 0 or x >= width - 1) and (y <= 0 or y >= height - 1):
            raise FailSafeTriggered(
                "stopped: the cursor reached a screen corner."
            )