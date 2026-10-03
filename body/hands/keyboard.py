"""Body/Hands — the keyboard.

Typing, key presses and combinations, behind the gate. The checks shared with
the pointer are in :mod:`body.hands.input_base`; this layer adds the ones that are
specific to keys.

**Dangerous text is refused, not confirmed.** ``type_text("rm -rf /")`` is
ordinary text in an editor and catastrophic in a terminal, and ZUZZ cannot see
which window has focus. Every string is judged on its own content, on every
call, and a match is a refusal rather than a question — because a confirmation
dialog cannot name the risk without naming the command, and at that point the
user is reading the dangerous text in a dialog ZUZZ built.

**Combinations have three lists and three treatments.** Always-blocked is
refused outright; ZUZZ's own emergency stop is refused for a different reason;
confirm-required is allowed once the user answers, and the prompt says what will
happen rather than naming the keys. See :mod:`body.hands.hotkey_policy`.

**Held keys are released on the way out.** ``hold_key`` without ``release_key``
leaves a modifier down and the user's keyboard in a state they did not choose.
Every failure path after a key goes down releases what was pressed, because a
raised exception is not a reason to leave the Shift key stuck.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from body.hands.exceptions import InvalidInputRefused
from body.hands.hands_guards import guard_text
from body.hands.hotkey_policy import confirmable_hotkey, guard_hotkey
from body.hands.input_base import InputLayer


class Keyboard(InputLayer):
    """The keyboard, behind the gate.

    Attributes:
        actuator: Where keystrokes actually go.
    """

    def __init__(
        self,
        actuator: Any,
        kill_switch: Optional[Any] = None,
        logger: Optional[Any] = None,
        foreground: Optional[Any] = None,
        elevated: bool = False,
    ) -> None:
        """Wire the keyboard to its collaborators.

        Args:
            actuator: The actuator that sends keystrokes.
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
        self._held: List[str] = []

    # -- typing ------------------------------------------------------------

    def type_text(self, decision: Any, text: str, interval: float = 0.01) -> Dict[str, Any]:
        """Type a string.

        The text is scanned before anything is sent, and again after nothing has
        been sent, because the scan is the only thing standing between a
        command and a terminal.

        Args:
            decision: A ``mouse_keyboard`` decision, or ``fullscreen_control``
                when a full-screen window has focus.
            text: The string to type.
            interval: Seconds between keystrokes.

        Returns:
            What was typed and how long it was.

        Raises:
            InvalidInputRefused: The text is not a string.
            DangerousTextRefused: The text matches a dangerous pattern.
            HandsRefused: Any refusal from the shared checks.
        """
        if not isinstance(text, str):
            raise InvalidInputRefused(f"text must be a string, got {type(text).__name__}")
        target = _text_target(text)
        self.prepare(decision, target)
        guard_text(text)
        self.actuator.write(text, interval=interval)
        self.log("type_text", chars=len(text), target=target)
        return {"typed": len(text), "target": target}

    def press(self, decision: Any, key: str, presses: int = 1) -> Dict[str, Any]:
        """Press a single key.

        Args:
            decision: A ``mouse_keyboard`` decision.
            key: The key name, as PyAutoGUI spells it.
            presses: How many times.

        Returns:
            What was pressed.

        Raises:
            InvalidInputRefused: The key is empty or the count is not positive.
        """
        if not str(key or "").strip():
            raise InvalidInputRefused("a key name is required")
        if presses < 1:
            raise InvalidInputRefused(f"presses must be at least 1, got {presses}")
        self.prepare(decision, str(key))
        for _ in range(presses):
            self.actuator.press(key)
        self.log("key_press", key=key, presses=presses)
        return {"key": key, "presses": presses}

    def press_keys(self, decision: Any, keys: Sequence[str]) -> Dict[str, Any]:
        """Press a sequence of keys in order, like a keyboard macro.

        Args:
            decision: A ``mouse_keyboard`` decision.
            keys: The key names, in the order to press them.

        Returns:
            The keys that were pressed.

        Raises:
            InvalidInputRefused: The sequence is empty.
        """
        sequence = [str(key) for key in (keys or []) if str(key or "").strip()]
        if not sequence:
            raise InvalidInputRefused("at least one key is required")
        self.prepare(decision, _text_target(",".join(sequence)))
        for key in sequence:
            self.actuator.press(key)
        self.log("key_press", keys=sequence)
        return {"keys": sequence}

    def hotkey(self, decision: Any, keys: Sequence[str]) -> Dict[str, Any]:
        """Press a key combination.

        The combination is checked against the blocklists *before* the decision
        is verified, and the refusal is logged as ``combo_refused`` — a run of
        these is the watchdog's signal that something is working through a
        blocklist rather than a person pressing keys.

        Args:
            decision: A ``mouse_keyboard`` decision.
            keys: The combination, in any order.

        Returns:
            The combination, and what it does when it needed confirmation.

        Raises:
            ReservedHotkeyRefused: The combination is ZUZZ's own stop.
            DangerousHotkeyRefused: The combination is always blocked.
        """
        sequence = [str(key) for key in (keys or []) if str(key or "").strip()]
        if not sequence:
            raise InvalidInputRefused("at least one key is required")
        try:
            guard_hotkey(sequence)
        except Exception:
            self.log("combo_refused", keys=sequence)
            raise
        consequence = confirmable_hotkey(sequence)
        self.prepare(decision, _text_target("+".join(sequence)))
        self.actuator.hotkey(*sequence)
        self.log("key_hotkey", keys=sequence, consequence=consequence or "")
        return {"keys": sequence, "consequence": consequence}

    # -- held keys ---------------------------------------------------------

    def hold_key(self, decision: Any, key: str) -> Dict[str, Any]:
        """Press and hold a key, for a drag or a modifier.

        The key is tracked, so :meth:`release_all` can undo it if something
        later goes wrong.

        Args:
            decision: A ``mouse_keyboard`` decision.
            key: The key to hold.

        Returns:
            Which keys are now held.
        """
        self.prepare(decision, str(key))
        self.actuator.key_down(key)
        self._held.append(str(key))
        self.log("key_down", key=key)
        return {"held": list(self._held)}

    def release_key(self, decision: Any, key: str) -> Dict[str, Any]:
        """Release a held key.

        Args:
            decision: A ``mouse_keyboard`` decision.
            key: The key to release.

        Returns:
            Which keys are still held.
        """
        self.prepare(decision, str(key))
        self._release(str(key))
        self.log("key_up", key=key)
        return {"held": list(self._held)}

    def release_all(self) -> List[str]:
        """Release every key ZUZZ is holding.

        Deliberately takes no decision. It is the cleanup path for the failure
        case where keys were pressed and the operation that pressed them could
        not finish — the alternative is leaving the user's keyboard in a state
        they never chose, and a stuck modifier is a safety problem rather than a
        convenience one.

        Returns:
            The keys that were released.
        """
        released: List[str] = []
        while self._held:
            key = self._held.pop()
            try:
                self.actuator.key_up(key)
            except Exception:
                continue
            released.append(key)
        return released

    def _release(self, key: str) -> None:
        """Release one key, tolerating one that is not actually held.

        Args:
            key: The key to release.
        """
        try:
            self.actuator.key_up(key)
        finally:
            if key in self._held:
                self._held.remove(key)


def _text_target(value: str) -> str:
    """Return the target string for text or a key sequence.

    Long strings are truncated: the decision records what was typed, and the
    activity log is not the place to store a page of pasted text twice. The
    guard still saw the whole string, which is the part that matters.

    Args:
        value: The text or key sequence.

    Returns:
        A target string no longer than 80 characters.
    """
    text = str(value)
    return text if len(text) <= 80 else text[:77] + "..."