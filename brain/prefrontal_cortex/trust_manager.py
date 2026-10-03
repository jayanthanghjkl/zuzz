"""Prefrontal Cortex — Trust Manager.

Holds the current trust level and enforces the escalating confirmation approved
on 2026-10-02: a downgrade to level 1 is instant, while level 5 requires the
user to type ``CONFIRM`` and wait out a cooldown.

Rule R9 — the AI cannot change the trust level — is enforced structurally here:
:meth:`request_change` is only ever called from the HTTP route that the UI
calls, and the gate itself never holds a reference to this object.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from core.config import ROOT, get_trust_levels

DEFAULT_LEVEL = 1
MIN_LEVEL = 1
MAX_LEVEL = 5
STATE_PATH = ROOT / "storage" / "trust_level.json"


class TrustChangeRefused(RuntimeError):
    """Raised when a trust level change did not satisfy its confirmation."""


class TrustManager:
    """Owns the current trust level and the rules for changing it.

    Attributes:
        path: Where the level is persisted between runs.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        """Load the persisted level, falling back to the configured default.

        Args:
            path: Override for the state file, used by tests.
        """
        self.path = Path(path) if path else STATE_PATH
        self._changed_at = 0.0

    def current(self) -> int:
        """Return the current trust level.

        Returns:
            The persisted level, or 1 when none is stored. Level 1 is the
            default because it is the only level that cannot surprise the user.
        """
        default = int(get_trust_levels().get("policy", {}).get("default_trust_level", DEFAULT_LEVEL))
        if not self.path.is_file():
            return default
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            level = int(data.get("trust_level", default))
        except (json.JSONDecodeError, ValueError, TypeError):
            return default
        return max(MIN_LEVEL, min(MAX_LEVEL, level))

    def save(self, level: int) -> None:
        """Persist a trust level.

        Args:
            level: The new level; clamped to 1-5.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload: Dict[str, Any] = {"trust_level": max(MIN_LEVEL, min(MAX_LEVEL, level))}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self._changed_at = time.time()

    def requirement_for(self, target_level: int) -> str:
        """Return the confirmation requirement for a change to a level.

        Args:
            target_level: The level being moved to.

        Returns:
            One of ``instant``, ``simple``, ``explain``, ``warn`` or
            ``type_confirm``.
        """
        change = get_trust_levels().get("trust_change", {})
        target = max(MIN_LEVEL, min(MAX_LEVEL, int(target_level)))
        for requirement in ("type_confirm", "warn_reduced_oversight", "explain_new_permissions", "simple_confirm"):
            if target in change.get(requirement, []):
                return {
                    "type_confirm": "type_confirm",
                    "warn_reduced_oversight": "warn",
                    "explain_new_permissions": "explain",
                    "simple_confirm": "simple",
                }[requirement]
        return "instant"

    def confirmation_word(self) -> str:
        """Return the word the user must type for the highest level."""
        return str(get_trust_levels().get("trust_change", {}).get("confirmation_word", "CONFIRM"))

    def cooldown_seconds(self) -> int:
        """Return the cooldown applied to the highest trust level."""
        return int(get_trust_levels().get("trust_change", {}).get("cooldown_seconds", 10))

    def cooldown_remaining(self) -> int:
        """Return seconds left in the cooldown, or 0 when it has elapsed."""
        elapsed = time.time() - self._changed_at
        return max(0, int(round(self.cooldown_seconds() - elapsed)))

    def describe_change(self, target_level: int) -> Dict[str, Any]:
        """Return what the UI needs to render a confirmation dialog.

        Args:
            target_level: The level being moved to.

        Returns:
            Current level, target level, requirement, confirmation word and
            cooldown.
        """
        config = get_trust_levels()
        levels = config.get("levels", {})
        target = str(max(MIN_LEVEL, min(MAX_LEVEL, int(target_level))))
        return {
            "current_level": self.current(),
            "current_description": levels.get(str(self.current()), {}).get("description", ""),
            "target_level": int(target),
            "target_description": levels.get(target, {}).get("description", ""),
            "requirement": self.requirement_for(target_level),
            "confirmation_word": self.confirmation_word(),
            "cooldown_seconds": self.cooldown_seconds(),
            "cooldown_remaining": self.cooldown_remaining(),
        }

    def request_change(
        self,
        target_level: int,
        *,
        confirmed: bool = False,
        typed_word: str = "",
        acknowledged_cooldown: bool = False,
    ) -> int:
        """Change the trust level once its confirmation requirement is met.

        Args:
            target_level: The requested level.
            confirmed: The user clicked confirm.
            typed_word: What the user typed, for ``type_confirm``.
            acknowledged_cooldown: The user waited out the cooldown.

        Returns:
            The new trust level.

        Raises:
            TrustChangeRefused: When the requirement for the change is not met.
        """
        current = self.current()
        target = max(MIN_LEVEL, min(MAX_LEVEL, int(target_level)))
        if target == current:
            return current

        requirement = self.requirement_for(target)
        if requirement == "instant":
            self.save(target)
            return target
        if not confirmed:
            raise TrustChangeRefused(
                f"moving to trust level {target} requires confirmation"
            )
        if requirement == "type_confirm":
            if typed_word.strip().upper() != self.confirmation_word():
                raise TrustChangeRefused(
                    f"moving to trust level {target} requires typing {self.confirmation_word()}"
                )
            if not acknowledged_cooldown:
                raise TrustChangeRefused(
                    f"moving to trust level {target} requires waiting out a "
                    f"{self.cooldown_seconds()}s cooldown"
                )
        self.save(target)
        return target