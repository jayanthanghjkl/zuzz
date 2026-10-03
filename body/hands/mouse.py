"""Body/Hands — the pointer.

Everything that moves or clicks, behind the gate. See
:mod:`body.hands.input_base` for the checks every input layer shares and why they
run in that order.

The mouse adds exactly two things on top: resolving the coordinate against every
attached monitor, and refusing to begin a drag it cannot finish.

Coordinates are never clamped. A point off every attached monitor is refused,
because clamping turns "off the edge of the screen" into a click in the corner,
which is a click the user never asked for and never saw. A point on a secondary
display is legitimate input, and validation against all monitors is what
distinguishes the two cases.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from body.hands import action_classifier
from body.hands.exceptions import InvalidInputRefused
from body.hands.hands_guards import verify_decision
from body.hands.input_base import InputLayer
from body.hands.screens import Screens


def coordinate_target(x: Any, y: Any) -> str:
    """Return the target string for a coordinate pair.

    Args:
        x: Horizontal coordinate.
        y: Vertical coordinate.

    Returns:
        ``"100,200"``. The gate and the Body both use this, so a decision
        issued for ``(100, 200)`` and one issued for ``"100,200"`` name the same
        point rather than differing by a bracket.
    """
    return f"{x},{y}"


class Mouse(InputLayer):
    """The pointer, behind the gate.

    Attributes:
        actuator: Where input actually goes.
        screens: The displays coordinates are validated against.
    """

    def __init__(
        self,
        actuator: Any,
        screens: Screens,
        kill_switch: Optional[Any] = None,
        logger: Optional[Any] = None,
        foreground: Optional[Any] = None,
        elevated: bool = False,
    ) -> None:
        """Wire the pointer to its collaborators.

        Args:
            actuator: The actuator that sends input.
            screens: The display layout used to validate coordinates.
            kill_switch: The switch to re-check on every call.
            logger: Activity logger; None means no logging.
            foreground: Callable returning the focused window's facts.
            elevated: True when ZUZZ itself is elevated.
        """
        super().__init__(
            kill_switch=kill_switch,
            logger=logger,
            foreground=foreground,
            elevated=elevated,
        )
        self.actuator = actuator
        self.screens = screens

    # -- read --------------------------------------------------------------

    def get_position(self, decision: Any) -> Tuple[int, int]:
        """Return the cursor position.

        Args:
            decision: A ``read_files`` decision.

        Returns:
            The ``(x, y)`` position.

        Raises:
            HandsRefused: The decision does not permit a read, or the cursor is
                somehow off every monitor.
        """
        verify_decision(
            decision,
            action_classifier.category_for("get_position"),
            str(decision.action.target),
            self.kill_switch,
        )
        x, y = self.actuator.position()
        self.screens.check(x, y)
        return x, y

    # -- input -------------------------------------------------------------

    def move(
        self, decision: Any, x: Any, y: Any, duration: float = 0.5
    ) -> Dict[str, Any]:
        """Move the pointer to a point.

        Args:
            decision: A ``mouse_keyboard`` decision, or ``fullscreen_control``
                when a full-screen window has focus.
            x: Destination horizontal coordinate.
            y: Destination vertical coordinate.
            duration: Seconds the movement takes, so a human can watch it happen
                and recognise what is about to be clicked.

        Returns:
            The resolved point, the monitor it is on, and the category enforced.
        """
        point, category = self._prepare(decision, x, y)
        self.actuator.move_to(point["x"], point["y"], duration=duration)
        self.log("mouse_move", point=point, category=category)
        return dict(point, category=category)

    def click(
        self,
        decision: Any,
        x: Any,
        y: Any,
        button: str = "left",
        clicks: int = 1,
        interval: float = 0.1,
    ) -> Dict[str, Any]:
        """Click at a point.

        Args:
            decision: A ``mouse_keyboard`` decision, or ``fullscreen_control``
                when a full-screen window has focus.
            x: Horizontal coordinate.
            y: Vertical coordinate.
            button: ``left``, ``right`` or ``middle``.
            clicks: How many clicks; above 1 this is a double or triple click.
            interval: Seconds between clicks.

        Returns:
            The resolved point, the monitor it is on, and the category enforced.

        Raises:
            InvalidInputRefused: The button is not one of the three, or the
                click count is not positive. Both are checked before anything
                moves, so a typo does not become a click.
        """
        if button not in ("left", "right", "middle"):
            raise InvalidInputRefused(
                f"button must be left, right or middle, got {button!r}"
            )
        if clicks < 1:
            raise InvalidInputRefused(f"clicks must be at least 1, got {clicks}")
        point, category = self._prepare(decision, x, y)
        self.actuator.click(
            point["x"], point["y"], button=button, clicks=clicks, interval=interval
        )
        self.log(
            "mouse_click", point=point, button=button, clicks=clicks, category=category
        )
        return dict(point, category=category)

    def double_click(self, decision: Any, x: Any, y: Any) -> Dict[str, Any]:
        """Double-click at a point."""
        return self.click(decision, x, y, clicks=2, interval=0.05)

    def right_click(self, decision: Any, x: Any, y: Any) -> Dict[str, Any]:
        """Right-click at a point, for a context menu."""
        return self.click(decision, x, y, button="right")

    def scroll(
        self,
        decision: Any,
        clicks: int,
        x: Optional[Any] = None,
        y: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Scroll the wheel.

        Args:
            decision: A ``mouse_keyboard`` decision.
            clicks: Wheel notches; negative scrolls down.
            x: Horizontal coordinate to scroll at. The current position when None.
            y: Vertical coordinate to scroll at. The current position when None.

        Returns:
            Where the scroll happened.
        """
        if x is None or y is None:
            current_x, current_y = self.actuator.position()
            x = current_x if x is None else x
            y = current_y if y is None else y
        point, category = self._prepare(decision, x, y)
        self.actuator.scroll(clicks, x=point["x"], y=point["y"])
        self.log("mouse_scroll", point=point, clicks=clicks, category=category)
        return dict(point, clicks=clicks, category=category)

    def drag(
        self,
        decision: Any,
        start: Tuple[Any, Any],
        end: Tuple[Any, Any],
        duration: float = 0.5,
        button: str = "left",
    ) -> Dict[str, Any]:
        """Press at one point, move, and release at another.

        Both ends are resolved *before* the button goes down. A drag whose
        destination is off-screen must not begin: releasing the mouse outside
        every monitor leaves the button down, so the user is left with a stuck
        mouse and ZUZZ with a drag it cannot finish. Checking first is the only
        way to avoid that, since the actuator cannot refuse the release.

        Args:
            decision: A ``mouse_keyboard`` decision.
            start: Where the button goes down.
            end: Where it comes up.
            duration: Seconds the drag takes.
            button: Which button to hold.

        Returns:
            The resolved start and end points, and the category enforced.
        """
        from_start, category = self._prepare(decision, start[0], start[1])
        to_end, _ = self._prepare(decision, end[0], end[1])
        self.actuator.move_to(from_start["x"], from_start["y"])
        self.actuator.click(from_start["x"], from_start["y"], button=button, clicks=1)
        self.actuator.move_to(to_end["x"], to_end["y"], duration=duration)
        self.actuator.click(to_end["x"], to_end["y"], button=button, clicks=1)
        self.log("mouse_drag", start=from_start, end=to_end, category=category)
        return {"start": from_start, "end": to_end, "category": category}

    # -- internals ---------------------------------------------------------

    def _prepare(self, decision: Any, x: Any, y: Any) -> Tuple[Dict[str, Any], str]:
        """Check the decision, then resolve the coordinate.

        The decision comes first so that a stop is reported as a stop even when
        the point would also have been refused.

        Args:
            decision: The decision being executed.
            x: Requested horizontal coordinate.
            y: Requested vertical coordinate.

        Returns:
            The resolved point and the category that was enforced.

        Raises:
            HandsRefused: Any refusal from the shared checks, or
                OffScreenRefused when the point is on no attached monitor.
        """
        _, category = self.prepare(decision, coordinate_target(x, y))
        return self.screens.check(x, y), category