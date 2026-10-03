"""Body/Hands — the shared base for everything that sends input.

The mouse and the keyboard have to ask the same questions in the same order, and
they must not be able to drift apart. An input path that checked for a login
window while its neighbour did not would be a hole shaped exactly like "the user
told it to".

So the checks live here once, and both layers call them:

1. **The decision**, verified for category *and* target, with the kill switch
   re-checked first so a stop is reported as a stop rather than as an action
   that merely happened to need confirming.
2. **The foreground window.** Not a credential prompt, not elevated above ZUZZ.

Which category applies is read from the screen *before* the decision is checked.
That ordering is the whole trick: a decision made while an ordinary window had
focus cannot be spent on a full-screen window that appeared afterwards, because
by then the operation needs ``fullscreen_control`` and the decision says
``mouse_keyboard``. The result is a replay refusal naming both categories, which
is a far more useful error than "not permitted".

Coordinates are validated after the decision, in the mouse layer, because
resolving them needs the display layout. A point off every monitor is refused
rather than clamped: clamping turns "off the edge of the screen" into a click in
the corner, which is a click the user never asked for and never saw.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

from body.hands import action_classifier, window_guards
from body.hands.hands_guards import verify_decision


class InputLayer:
    """Base for the mouse and the keyboard.

    Attributes:
        kill_switch: The switch re-checked on every call.
        logger: Activity logger, which is also what the watchdog reads.
        elevated: True when ZUZZ itself runs as administrator.
    """

    def __init__(
        self,
        kill_switch: Optional[Any] = None,
        logger: Optional[Any] = None,
        foreground: Optional[Callable[[], Dict[str, Any]]] = None,
        elevated: bool = False,
    ) -> None:
        """Wire the layer to its collaborators.

        Args:
            kill_switch: The switch to re-check on every call.
            logger: Activity logger; None means no logging, which tests use to
                keep their assertions about the actuator alone.
            foreground: Callable returning ``{"fullscreen", "title", "window"}``,
                from :func:`body.hands.window_ops.foreground_probe`. There is
                deliberately no "assume nothing has focus" default: not knowing
                is not the same as knowing, and a default would make the guard
                untestable rather than absent.
            elevated: True when ZUZZ is itself elevated.
        """
        self.kill_switch = kill_switch
        self.logger = logger
        self.elevated = elevated
        self.foreground = foreground or (
            lambda: {"fullscreen": False, "title": "", "window": {}}
        )

    def prepare(self, decision: Any, target: str) -> Tuple[Dict[str, Any], str]:
        """Run the checks every input operation shares.

        Args:
            decision: The decision being executed.
            target: The resolved target the operation will act on, in the form
                the decision was issued for.

        Returns:
            The focused window's facts, and the category that was enforced.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            DecisionReplayRefused: The decision names another category or target
                — including a decision made before a full-screen window appeared.
            DryRunRefused: A dry-run decision reached the input layer.
            KillSwitchRefused: The switch was engaged after the decision.
            LoginDialogRefused: The foreground window is a credential prompt.
            ElevatedWindowRefused: It is elevated and ZUZZ is not.
        """
        focus = self.foreground()
        category = (
            action_classifier.fullscreen_category()
            if focus.get("fullscreen")
            else action_classifier.mouse_category()
        )
        verify_decision(decision, category, target, self.kill_switch)
        window_guards.guard_window(focus.get("window") or {}, elevated_ok=self.elevated)
        return focus, category

    def log(self, action: str, **fields: Any) -> None:
        """Record one input action, when a logger is wired.

        The action names are what the watchdog counts, and they are chosen to be
        unambiguous: ``mouse_click`` counts towards the click rate and nothing
        else, and a refused combination is logged as ``combo_refused`` so it is
        not also counted as a burst of keystrokes. See
        ``brain.spinal_cord.watchdog``.

        Args:
            action: The action name.
            **fields: Structured detail recorded verbatim.
        """
        if self.logger is not None:
            self.logger.log("hands", action, **fields)