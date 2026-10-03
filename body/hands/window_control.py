"""Body/Hands — window control behind the gate.

The decision-guarded wrappers around :mod:`body.hands.window_ops`: listing
windows, finding the focused one, raising one, and changing its state or size.

They live here rather than in :mod:`body.hands.window_ops` because that module is
about talking to the operating system and this one is about being permitted to.
The split keeps the Win32 calls free of gate imports, so the low-level code can
be read — and tested — without a decision in sight, and so nothing that touches
the OS can acquire the habit of skipping the gate.

Every method verifies the decision against ``mouse_keyboard``, which is the
category window manipulation shares with the pointer. Raising a window and
clicking in it are the same kind of act: putting ZUZZ's intent into what the
user is looking at.
"""

from __future__ import annotations

from typing import Any, Dict, List

from body.hands import window_guards, window_manager, window_ops
from body.hands.action_classifier import category_for
from body.hands.exceptions import TargetNotFoundRefused
from body.hands.hands_guards import verify_decision


class WindowControl:
    """Window operations, behind the gate.

    Attributes:
        elevated: True when ZUZZ itself is elevated, which removes the
            elevated-window obstacle.
    """

    def __init__(
        self,
        kill_switch: Any = None,
        logger: Any = None,
        elevated: bool = False,
    ) -> None:
        """Wire window control to its collaborators.

        Args:
            kill_switch: The switch to re-check on every call.
            logger: Activity logger; None means no logging.
            elevated: True when ZUZZ is elevated.
        """
        self.kill_switch = kill_switch
        self.logger = logger
        self.elevated = elevated

    def list_windows(self, decision: Any) -> List[Dict[str, Any]]:
        """Return every visible top-level window.

        Args:
            decision: A ``read_files`` decision.

        Returns:
            The window records, as :func:`window_manager.list_windows` returns
            them.
        """
        verify_decision(
            decision,
            category_for("list_windows"),
            str(decision.action.target),
            self.kill_switch,
        )
        return window_manager.list_windows()

    def get_active_window(self, decision: Any) -> Any:
        """Return the window that has focus.

        Args:
            decision: A ``read_files`` decision.

        Returns:
            The window record, or None when no window has focus.
        """
        verify_decision(
            decision,
            category_for("get_active_window"),
            str(decision.action.target),
            self.kill_switch,
        )
        return window_manager.active_window()

    def focus_window(self, decision: Any, description: Any) -> Dict[str, Any]:
        """Bring a window to the foreground.

        Args:
            decision: A ``mouse_keyboard`` decision.
            description: A window handle, an exact title, or a fragment.

        Returns:
            The resolved window and whether it was raised.

        Raises:
            TargetNotFoundRefused: Nothing matched, or the match was ambiguous.
            LoginDialogRefused: The window is a credential prompt.
            ElevatedWindowRefused: It is elevated and ZUZZ is not.
        """
        resolved = self.resolve(description)
        verify_decision(
            decision,
            category_for("focus_window"),
            str(resolved["title"]),
            self.kill_switch,
        )
        window_guards.guard_window(resolved, elevated_ok=self.elevated)
        focused = window_ops.focus_window(resolved["hwnd"])
        self._log("window_focus", title=resolved["title"], focused=focused)
        return {"window": resolved, "focused": focused}

    def set_state(self, decision: Any, description: Any, state: str) -> Dict[str, Any]:
        """Minimise, maximise or restore a window.

        Args:
            decision: A ``mouse_keyboard`` decision.
            description: A window handle, an exact title, or a fragment.
            state: ``minimise``, ``maximise`` or ``restore``.

        Returns:
            The resolved window and whether the change was accepted.

        Raises:
            InvalidInputRefused: The state is not one of the three.
        """
        commands = {
            "minimise": window_ops.SW_MINIMIZE,
            "maximize": window_ops.SW_MAXIMIZE,
            "maximise": window_ops.SW_MAXIMIZE,
            "restore": window_ops.SW_RESTORE,
        }
        if state not in commands:
            from body.hands.exceptions import InvalidInputRefused

            raise InvalidInputRefused(
                f"state must be minimise, maximise or restore, got {state!r}"
            )
        resolved = self.resolve(description)
        verify_decision(
            decision,
            category_for("maximize"),
            str(resolved["title"]),
            self.kill_switch,
        )
        window_guards.guard_window(resolved, elevated_ok=self.elevated)
        changed = window_ops.set_state(resolved["hwnd"], commands[state])
        self._log("window_state", title=resolved["title"], state=state, changed=changed)
        return {"window": resolved, "state": state, "changed": changed}

    def resize(
        self,
        decision: Any,
        description: Any,
        left: Any,
        top: Any,
        width: Any,
        height: Any,
    ) -> Dict[str, Any]:
        """Move and resize a window.

        Args:
            decision: A ``mouse_keyboard`` decision.
            description: A window handle, an exact title, or a fragment.
            left: New left edge.
            top: New top edge.
            width: New width, in pixels.
            height: New height, in pixels.

        Returns:
            The resolved window and whether the change was accepted.
        """
        resolved = self.resolve(description)
        verify_decision(
            decision,
            category_for("resize_window"),
            str(resolved["title"]),
            self.kill_switch,
        )
        window_guards.guard_window(resolved, elevated_ok=self.elevated)
        changed = window_ops.set_rect(
            resolved["hwnd"], left, top, width, height
        )
        self._log("window_resize", title=resolved["title"], changed=changed)
        return {"window": resolved, "changed": changed}

    def resolve(self, description: Any) -> Dict[str, Any]:
        """Turn a description into a concrete window record.

        Args:
            description: A window record, a handle, a title, or a fragment.

        Returns:
            The window record.

        Raises:
            TargetNotFoundRefused: Nothing matched, or the match was ambiguous.
        """
        if isinstance(description, dict) and "hwnd" in description:
            return description
        if description is None or not str(description).strip():
            from body.hands.exceptions import InvalidInputRefused

            raise InvalidInputRefused("a window handle or title is required")
        return window_manager.resolve_window(description)

    def _log(self, action: str, **fields: Any) -> None:
        """Record one window action, when a logger is wired."""
        if self.logger is not None:
            self.logger.log("hands", action, **fields)


# Re-exported so callers can catch it from this module without a second import.
__all__ = ["WindowControl", "TargetNotFoundRefused"]