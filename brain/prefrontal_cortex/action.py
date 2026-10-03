"""Prefrontal Cortex — Action Model.

One action, as the brain proposes it, before any permission decision. The gate
reads these fields; the Body layer executes them. Nothing here decides anything
— deciding is :mod:`brain.prefrontal_cortex.permission_gate`'s job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict


class ActionCategory(str, Enum):
    """The kinds of action the gate knows about.

    The values match the keys in ``config/trust_levels.json`` so the matrix can
    be edited without touching code. Adding a category here without adding a row
    to that file makes the gate refuse the action, which is the safe direction
    to fail.
    """

    CHAT = "chat"
    READ_FILES = "read_files"
    OPEN_APPS = "open_apps"
    CLOSE_APPS = "close_apps"
    FULLSCREEN_CONTROL = "fullscreen_control"
    MOUSE_KEYBOARD = "mouse_keyboard"
    RUN_SAFE_COMMANDS = "run_safe_commands"
    WRITE_FILES = "write_files"
    BROWSE = "browse"
    RUN_PYTHON = "run_python"
    MODIFY_PLUGINS = "modify_plugins"
    DELETE_FILES = "delete_files"
    INSTALL_SOFTWARE = "install_software"
    SEND_MESSAGES = "send_messages"
    RUN_ADMIN = "run_admin"
    ACCESS_CREDENTIALS = "access_credentials"
    DELETE_OS_FILES = "delete_os_files"
    SHARE_DATA = "share_data"
    DISABLE_SAFETY = "disable_safety"

    def __str__(self) -> str:
        """Return the config key for this category."""
        return str(self.value)


@dataclass(frozen=True)
class Action:
    """A proposed action awaiting a permission decision.

    Attributes:
        category: What kind of action this is.
        target: What it acts on — a path, a command, a URL, an app name.
        detail: Extra context for logging and for the Amygdala's scoring.
        dry_run: When True the Body layer must describe the action instead of
            performing it.
    """

    category: ActionCategory
    target: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False

    @property
    def is_file_write(self) -> bool:
        """Return True for actions that create or modify a file."""
        return self.category in (ActionCategory.WRITE_FILES, ActionCategory.RUN_PYTHON)

    @property
    def is_destructive(self) -> bool:
        """Return True for actions that can destroy data."""
        return self.category in (
            ActionCategory.DELETE_FILES,
            ActionCategory.DELETE_OS_FILES,
            ActionCategory.CLOSE_APPS,
        )

    def describe(self) -> str:
        """Return a one-line human description for the UI and the log.

        Returns:
            For example ``delete files at C:\\Users\\me\\notes.txt``.
        """
        verb = str(self.category).replace("_", " ")
        suffix = f" {self.target}" if self.target else ""
        prefix = "would " if self.dry_run else ""
        return f"{prefix}{verb}{suffix}"