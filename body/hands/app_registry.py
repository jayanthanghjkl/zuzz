"""Body/Hands — the application registry.

Turns "open chrome" into ``chrome.exe``, and decides whether that is an
auto-approval or a question.

The resolution happens *before* classification, which is the whole point. The
gate is asked about the concrete file, not about the string the user typed, so an
alias cannot smuggle in something different, and two names for the same
executable get the same answer.

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

**Where the executable lives.** :func:`locate` resolves a name to a launchable
path: ``PATH`` first, then :data:`KNOWN_APP_PATHS`, which records where Chrome,
Firefox, Edge, VS Code and the rest actually install. The second step is not
belt-and-braces — the Chrome, Firefox and Edge installers do not put themselves on
``PATH``, so without it "Open Chrome" resolved to a bare ``chrome.exe`` that
could not be launched at all. See the ``_comment`` in
``config/apps_whitelist.json`` for the resolution order and how to extend it.
"""

from __future__ import annotations

import os
import shutil
from typing import Any, Dict, List, Optional

from body.hands.exceptions import ProtectedRefused
from core.config import get_apps_whitelist

#: Known install locations for applications the whitelist names but Windows does
#: not put on ``PATH``.
#:
#: Found in Session 13 live verification: "Open Chrome" resolved to the bare
#: string ``chrome.exe`` and then failed, because the Chrome, Firefox and Edge
#: installers deliberately do **not** add their application directory to
#: ``PATH``. Only ``System32`` entries like ``notepad.exe`` resolve by name.
#: The whitelist's job is to say *what* the user meant, and it was right;
#: nothing downstream knew *where* it lived.
#:
#: Keyed by the lower-cased executable name, so it matches whatever the
#: whitelist calls it (``code`` for both ``vscode`` and ``code``). Each entry
#: lists candidates in preference order, per-machine before per-user, because a
#: system install is the one the user is asking about on a shared machine.
#:
#: Checked only after ``PATH``. A bare name that resolves on ``PATH`` stays a
#: bare name, which keeps the target the gate judged — and the launch command —
#: exactly as short as it was before.
KNOWN_APP_PATHS: Dict[str, List[str]] = {
    "chrome.exe": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ],
    "firefox.exe": [
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
    ],
    "msedge.exe": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    "code.exe": [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
        r"C:\Program Files\Microsoft VS Code\Code.exe",
        r"C:\Program Files (x86)\Microsoft VS Code\Code.exe",
    ],
    "brave.exe": [
        os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    ],
    "opera.exe": [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Opera\opera.exe"),
        r"C:\Program Files\Opera\opera.exe",
    ],
    "spotify.exe": [
        os.path.expandvars(r"%APPDATA%\Spotify\Spotify.exe"),
    ],
    "slack.exe": [
        os.path.expandvars(r"%LOCALAPPDATA%\slack\slack.exe"),
    ],
    "discord.exe": [
        os.path.expandvars(r"%LOCALAPPDATA%\Discord\Update.exe"),
    ],
    "notepad.exe": [
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\notepad.exe"),
    ],
    "outlook.exe": [
        r"C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE",
        r"C:\Program Files (x86)\Microsoft Office\root\Office16\OUTLOOK.EXE",
    ],
    "winword.exe": [
        r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE",
        r"C:\Program Files (x86)\Microsoft Office\root\Office16\WINWORD.EXE",
    ],
    "excel.exe": [
        r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE",
        r"C:\Program Files (x86)\Microsoft Office\root\Office16\EXCEL.EXE",
    ],
    "powershell.exe": [
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        r"C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe",
    ],
}


def locate(executable: str) -> str:
    """Return something launchable for an executable name.

    **PATH first, then the known install locations.** The order is the point: a
    name that resolves on ``PATH`` is left exactly as it is, so the string the
    gate judged is the string that is launched and nothing about the existing
    targets changes. Only when ``PATH`` has nothing does the whitelist's table of
    where applications actually live come into play.

    An unresolvable name is returned unchanged rather than refused. Refusing here
    would be a precheck refusal, and it would be a lie: ZUZZ does not know that
    the application is absent, only that it did not find it. Letting the launch
    fail produces the accurate error — "no such file or directory" naming the
    executable — instead of ZUZZ asserting an application does not exist.

    Args:
        executable: A bare executable name, or an absolute path.

    Returns:
        The absolute path when one could be found, otherwise ``executable``
        unchanged. Always deterministic for a given machine, because resolve and
        execute both call this and the gate compares their two results exactly.
    """
    text = str(executable or "").strip()
    if not text:
        return text
    if os.path.isabs(text) or os.sep in text or "/" in text:
        return text
    if shutil.which(text):
        return text
    for candidate in KNOWN_APP_PATHS.get(text.lower(), []):
        if candidate and os.path.isfile(candidate):
            return candidate
    return text


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
    # Located *here*, before classification, so the executable the gate judges is
    # the concrete file that will be launched. A decision for ``chrome.exe`` must
    # cover the real ``C:\Program Files\...\chrome.exe``, not a name that
    # happened to be on PATH when the approval was taken and is not now.
    executable = locate(executable)
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