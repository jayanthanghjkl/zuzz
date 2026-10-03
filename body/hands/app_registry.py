"""Body/Hands — the application registry.

Turns "open chrome" into ``chrome.exe``, and decides whether that is an
auto-approval or a question.

The resolution happens *before* classification, which is the whole point. The
gate is asked about ``chrome.exe``, not about the string ``chrome``, so an alias
cannot smuggle in something different, and two names for the same executable get
the same answer.

Three lists, and the difference between them matters:

* ``apps`` — auto-approval. Opening one of these is ordinary work.
* ``never_launch`` — refusal at every trust level. Shells, encoded PowerShell
  and registry editors live here: an entry in ``apps`` that launches a shell
  would otherwise be a way to defeat the whitelist, and a whitelist is only a
  convenience, not a security boundary.
* ``protected_processes`` — never killed. These are the machine, not the user.

Removing an entry from ``apps`` does not block that application. It only removes
the auto-approval, so the application becomes an ``ask``. Blocking is a separate
decision, taken by ``never_launch``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from body.hands.exceptions import ProtectedRefused
from core.config import get_apps_whitelist


def apps() -> Dict[str, str]:
    """Return the alias-to-executable map."""
    return dict(get_apps_whitelist().get("apps", {}))


def is_whitelisted(name: str) -> bool:
    """Return True when an application is on the auto-approve list."""
    return str(name or "").strip().lower() in apps()


def resolve_app(name: str) -> Dict[str, Any]:
    """Turn an application name into something launchable.

    Args:
        name: What the caller asked for, e.g. ``"chrome"``.

    Returns:
        ``{"requested", "executable", "whitelisted", "reason"}``. An unknown
        name is kept whole and marked un-whitelisted, so it is launched as
        given but never auto-approved.

    Raises:
        ProtectedRefused: The resolved command is on the never-launch list.
    """
    table = apps()
    key = str(name or "").strip().lower()
    executable = table.get(key)
    whitelisted = executable is not None
    if not whitelisted:
        executable = str(name or "").strip()
    resolved = {
        "requested": str(name or ""),
        "executable": executable,
        "whitelisted": whitelisted,
        "reason": (
            "on the application whitelist"
            if whitelisted
            else "not on the whitelist, so it asks at every trust level"
        ),
    }
    refuse_if_forbidden(executable)
    return resolved


def refuse_if_forbidden(executable: str) -> None:
    """Refuse a command that must never be launched.

    Args:
        executable: The resolved command line.

    Raises:
        ProtectedRefused: The command matches the never-launch list.
    """
    lowered = str(executable or "").strip().lower()
    for forbidden in get_apps_whitelist().get("never_launch", []):
        if lowered.startswith(str(forbidden).lower()):
            raise ProtectedRefused(
                f"{executable!r} is on the never-launch list. ZUZZ will not "
                f"start a shell, an encoded script or a registry editor."
            )


def is_protected_process(name: str, pid: Optional[int] = None) -> bool:
    """Return True when a process must never be killed.

    Both the PID and the name are checked. The name catches the machine's own
    processes wherever they appear; the PID catches PID 0 and PID 4, which
    address the kernel and the system and cannot be signalled at all.

    Args:
        name: The process name, with or without ``.exe``.
        pid: The process id, when known.

    Returns:
        True when the process is off limits.
    """
    if pid in (0, 4):
        return True
    lowered = str(name or "").strip().lower()
    if not lowered:
        return False
    if lowered.endswith(".exe"):
        lowered = lowered[:-4]
    protected = {
        str(entry).lower().removesuffix(".exe")
        for entry in get_apps_whitelist().get("protected_processes", [])
    }
    return lowered in protected


def describe() -> Dict[str, Any]:
    """Return the registry for the Desktop tab."""
    config = get_apps_whitelist()
    return {
        "apps": sorted(apps()),
        "never_launch": list(config.get("never_launch", [])),
        "protected_processes": list(config.get("protected_processes", [])),
    }


def matching(names: List[str]) -> List[str]:
    """Return the whitelist entries whose name contains a fragment.

    Args:
        names: Fragments the user typed, e.g. ``["note"]``.

    Returns:
        Matching alias names, so an unknown app can be offered as a suggestion
        without being silently substituted.
    """
    table = apps()
    hits: List[str] = []
    for fragment in names:
        needle = str(fragment or "").strip().lower()
        if needle and needle in table and needle not in hits:
            hits.append(needle)
    return hits