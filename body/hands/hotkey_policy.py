"""Body/Hands — hotkey policy.

Which combinations ZUZZ may synthesise, and what happens when it is asked for
one that is on a list. The lists live in ``config/dangerous_hotkeys.json`` so
blocking a combination is a config edit rather than a code change.

Four lists, and the difference between them is the whole point:

``always_blocked``
    Refused outright. ``Ctrl+Alt+Del`` and ``Ctrl+Shift+Esc`` are the
    secure-attention and task-manager sequences. An agent reaching for them is
    trying to get past a boundary the user can see, so there is no trust level
    at which this becomes a question rather than a refusal.

``reserved_by_zuzz``
    ``Ctrl+Shift+Q``, ZUZZ's own emergency stop. Refused for a reason that has
    nothing to do with danger: an agent that can synthesise the stop can stop
    itself, and a stop it can cause is not a stop. The human presses it.

``requires_confirmation``
    Allowed, but only after an explicit answer, and the confirmation says what
    will happen. ``Win+L`` locks the machine with no warning; ``Win+R`` opens a
    box that executes whatever is typed into it.

``always_blocked_by_zuzz``
    Combinations this process refuses even if a config edit removed them from
    ``always_blocked``. Read before the other lists, so the stop cannot be
    un-blocked by editing a file.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from core.config import get_dangerous_hotkeys

from body.hands.exceptions import (
    DangerousHotkeyRefused,
    ReservedHotkeyRefused,
)

#: Keys compared without meaning. ``del`` and ``delete`` name the same key, and
#: so do ``ctrl`` and ``control``.
_KEY_ALIASES = {
    "del": "delete",
    "control": "ctrl",
    "cmd": "command",
    "command": "command",
    "windows": "win",
    "meta": "win",
    "esc": "escape",
    "return": "enter",
    "pgup": "pageup",
    "pgdn": "pagedown",
}

#: What each confirmable combination does, in the user's terms. Stored as
#: combinations rather than joined strings because the comparison is
#: order-independent — see :func:`_matches`.
_CONSEQUENCES = (
    (["win", "l"], "locks the machine immediately"),
    (["win", "r"], "opens a run box, which executes whatever is typed into it"),
    (["alt", "f4"], "closes whatever window has focus"),
    (["win", "d"], "minimises everything to the desktop"),
    (["ctrl", "alt", "delete"], "opens the secure attention sequence"),
)


def normalise_key(key: str) -> str:
    """Return a key name in the form the hotkey lists use.

    Args:
        key: A key name as PyAutoGUI spells it.

    Returns:
        The canonical name, lowercased.
    """
    lowered = str(key or "").strip().lower()
    return _KEY_ALIASES.get(lowered, lowered)


def normalise_combo(keys: Sequence[str]) -> List[str]:
    """Return a combination in the order-independent form the lists use.

    Sorting is what makes the lists safe to write by hand: a combination written
    ``['alt','ctrl','delete']`` matches a caller passing ``('ctrl','alt','delete')``.

    Args:
        keys: The key names, in any order.

    Returns:
        The sorted canonical names.
    """
    return sorted(normalise_key(key) for key in keys if str(key or "").strip())


def guard_hotkey(keys: Sequence[str]) -> None:
    """Refuse a key combination ZUZZ must not synthesise.

    Args:
        keys: The combination, in any order.

    Raises:
        ReservedHotkeyRefused: The combination is ZUZZ's own emergency stop.
        DangerousHotkeyRefused: The combination is on the always-blocked list.
    """
    combo = normalise_combo(keys)
    if not combo:
        return
    config = get_dangerous_hotkeys()
    if _matches(combo, config.get("always_blocked_by_zuzz", [])):
        raise ReservedHotkeyRefused(
            f"{'+'.join(combo)} is ZUZZ's own emergency stop. ZUZZ never types "
            f"its own stop: the stop must always be something a human presses."
        )
    if _matches(combo, config.get("always_blocked", [])):
        raise DangerousHotkeyRefused(
            f"{'+'.join(combo)} is blocked at every trust level. It is the "
            f"secure-attention or task-manager sequence, and reaching for it "
            f"means getting past a boundary the user can see."
        )
    if _matches(combo, config.get("reserved_by_zuzz", [])):
        raise ReservedHotkeyRefused(
            f"{'+'.join(combo)} is reserved for ZUZZ's emergency stop"
        )


def confirmable_hotkey(keys: Sequence[str]) -> Optional[str]:
    """Return why a combination needs confirmation, or None.

    Args:
        keys: The combination, in any order.

    Returns:
        A human-readable consequence, e.g. ``"locks the machine"``, when the
        combination is on the requires-confirmation list. The caller passes this
        into the confirmation prompt rather than showing a bare key name, so the
        user is asked about the consequence and not about the mechanism.
    """
    combo = normalise_combo(keys)
    if not _matches(combo, get_dangerous_hotkeys().get("requires_confirmation", [])):
        return None
    for entry, consequence in _CONSEQUENCES:
        if normalise_combo(entry) == combo:
            return consequence
    return f"sends {'+'.join(combo)}"


def is_blocked(keys: Sequence[str]) -> bool:
    """Return True when a combination ZUZZ may not synthesise.

    Args:
        keys: The combination, in any order.

    Returns:
        True when :func:`guard_hotkey` would refuse it.
    """
    try:
        guard_hotkey(keys)
    except (DangerousHotkeyRefused, ReservedHotkeyRefused):
        return True
    return False


def describe() -> dict:
    """Return the policy for the Desktop tab."""
    config = get_dangerous_hotkeys()
    return {
        "always_blocked": [
            "+".join(normalise_combo(entry)) for entry in config.get("always_blocked", [])
        ],
        "requires_confirmation": [
            "+".join(normalise_combo(entry))
            for entry in config.get("requires_confirmation", [])
        ],
        "reserved_by_zuzz": [
            "+".join(normalise_combo(entry))
            for entry in config.get("reserved_by_zuzz", [])
        ],
    }


def _matches(combo: List[str], table: object) -> bool:
    """Return True when a combination appears in a list of combinations.

    Args:
        combo: The normalised, sorted combination.
        table: A list of combinations in any order.

    Returns:
        True on a match.
    """
    for entry in table or []:
        if normalise_combo(entry) == combo:
            return True
    return False