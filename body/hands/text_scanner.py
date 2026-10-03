"""Body/Hands — text inspection.

Two questions are asked about every string on its way to the machine, and both
are asked here so that no caller has to remember them.

**Is this text dangerous to type?** Typing is the one desktop action with no
target: ``type_text("rm -rf /")`` is harmless in a text editor and catastrophic
in a terminal, and ZUZZ cannot tell which window has focus. So the patterns are
checked against the text itself rather than the destination, and a match asks
even when the matrix would auto-approve.

**Does this look like a credential?** The clipboard is a place secrets end up by
accident — a copied password, a token from a dashboard. When the content looks
like a credential the read is escalated to ``access_credentials``, which asks at
every trust level. The detection is a heuristic and is treated as one: a false
positive costs one confirmation, a false negative leaks a secret into a log.

Neither of these is a defence on its own. They raise scrutiny; they never lower
it, and they never block outright — except for text patterns, which the brief
lists as a refusal.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

#: Providers whose key shapes are recognised. A live key matching one of these
#: prefixes is treated as a credential whatever else it looks like.
API_KEY_PREFIXES = (
    "sk-", "sk_live_", "sk_test_", "gsk_", "hf_", "xoxb-", "xoxp-",
    "ghp_", "gho_", "github_pat_", "AKIA", "AIza", "glpat-", "npm_",
    "pypi-AgEIcH", "dop_v1_", "shpat_",
)

#: Text that must not be typed without an explicit answer.
DANGEROUS_TEXT_PATTERNS = (
    ("recursive_delete", r"\brm\s+(-[a-z]*[rf][a-z]*\s+)+", "recursive delete"),
    ("windows_wipe", r"\b(del|rd|rmdir)\b[^\n]{0,40}(?<![\w/])/s(?![\w])",
     "recursive delete"),
    ("format_drive", r"\bformat\s+[a-z]:", "formats a drive"),
    ("disk_utility", r"\b(diskpart|fsutil|cipher)\b", "disk utility"),
    ("registry_write", r"\breg\s+(add|delete|import|edit)\b", "registry write"),
    ("power_state", r"\b(shutdown|restart-computer|halt)\b", "power state"),
    ("dd_to_device", r"\bdd\b[^\n]{0,40}=\s*/dev/", "writes to a raw device"),
    ("pipe_to_shell", r"(curl|wget)[^\n|]{0,120}\|\s*(sudo\s+)?(ba)?sh",
     "pipes a download into a shell"),
    ("privilege_escalation", r"\b(sudo|runas)\b", "escalates privilege"),
    ("force_flag", r"(--force\b|\s-f\s|/f\b)", "forces past a prompt"),
    ("credential_path", r"(\.env\b|id_rsa|id_ed25519|\.aws/credentials|"
     r"\.ssh/|\.pem\b|\.key\b)", "touches credential material"),
    ("exfil_upload", r"\b(scp|rsync|sftp)\b[^\n]{0,100}[A-Za-z0-9._-]+:[/\\]",
     "copies files to another host"),
)

#: Domains a browser should not be driven to without an explicit answer.
SUSPICIOUS_DOMAINS = (
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "is.gd", "shorturl.at",
    "ngrok.io", "requestbin", "webhook.site", "pastebin.com",
    "transfer.sh", "anonfiles.com",
)

#: Card numbers, matched on the digits with separators removed.
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")

#: A URL carrying inline credentials, e.g. ``https://user:pass@host``.
_URL_CREDENTIALS = re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s:@]+@")

#: The header of a PEM private key.
_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")

#: A bare email address.
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def scan_text(text: str) -> List[Dict[str, str]]:
    """Find dangerous patterns in text that is about to be typed.

    Args:
        text: The string ZUZZ wants to type.

    Returns:
        One entry per match, with ``id``, ``label`` and the matched text.
        Empty when the text looks ordinary.
    """
    if not text:
        return []
    found: List[Dict[str, str]] = []
    for identifier, pattern, label in DANGEROUS_TEXT_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            found.append(
                {
                    "id": identifier,
                    "label": label,
                    "match": match.group(0)[:80],
                }
            )
    for domain in SUSPICIOUS_DOMAINS:
        if re.search(rf"\b{re.escape(domain)}\b", text, re.IGNORECASE):
            found.append(
                {
                    "id": "suspicious_domain",
                    "label": f"shortener or exfiltration host ({domain})",
                    "match": domain,
                }
            )
    return found


def scan_bulk_email(text: str, threshold: int = 5) -> Optional[Dict[str, str]]:
    """Return a finding when the text carries a mailing list.

    Args:
        text: The string ZUZZ wants to type.
        threshold: How many distinct addresses count as bulk.

    Returns:
        A finding dict, or None below the threshold.
    """
    addresses = {match.lower() for match in _EMAIL.findall(text or "")}
    if len(addresses) < threshold:
        return None
    return {
        "id": "bulk_email",
        "label": f"{len(addresses)} email addresses in one action",
        "match": ", ".join(sorted(addresses)[:3]) + ", ...",
    }


def looks_like_credential(text: str) -> Optional[Dict[str, str]]:
    """Return a finding when text looks like a secret.

    Used for the clipboard, where the content is whatever happened to be copied.
    The heuristics are deliberately loose, because the cost of a false positive
    is one confirmation and the cost of a false negative is a secret written
    into an activity log.

    Args:
        text: The candidate content.

    Returns:
        A finding with ``id`` and ``label``, or None when it looks ordinary.
    """
    candidate = (text or "").strip()
    if not candidate:
        return None
    if _PEM.search(candidate):
        return {"id": "private_key", "label": "a PEM private key"}
    if _URL_CREDENTIALS.search(candidate):
        return {"id": "url_credentials", "label": "a URL with a username and password"}
    if candidate.startswith("-----BEGIN"):
        return {"id": "pem_block", "label": "a PEM-encoded key block"}
    for prefix in API_KEY_PREFIXES:
        if candidate.lower().startswith(prefix.lower()) and len(candidate) > 12:
            return {"id": "api_key", "label": f"an API key ({prefix}...)"}
    digits = re.sub(r"[ -]", "", candidate)
    if _CARD.fullmatch(digits) and 13 <= len(digits) <= 19:
        return {"id": "card_number", "label": "a card number"}
    if _credential_shape(candidate):
        return {
            "id": "credential_shape",
            "label": "mixed case, digits and symbols of credential length",
        }
    return None


def _credential_shape(text: str) -> bool:
    """Return True when text has the shape of a generated secret.

    Four independent traits are required together, because any one of them on
    its own describes plenty of ordinary text: ordinary sentences are long, and
    ordinary codes are short. Length is bounded at both ends because the brief
    asks for 8-64, and a "password" of three characters is not a password.

    Args:
        text: The candidate.

    Returns:
        True when all four traits are present.
    """
    if not 8 <= len(text) <= 64:
        return False
    has_lower = any(char.islower() for char in text)
    has_upper = any(char.isupper() for char in text)
    has_digit = any(char.isdigit() for char in text)
    has_symbol = any(not char.isalnum() for char in text)
    return has_lower and has_upper and has_digit and has_symbol