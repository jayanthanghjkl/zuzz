"""Corpus Callosum — Stream Chunk Parsing.

Server-sent-event parsing shared by the connectors. Kept as module functions
so each connector can reuse or override the format it speaks.
"""

from __future__ import annotations

import json


def parse_sse_line(line: str) -> str:
    """Parse an OpenAI-style ``data:`` server-sent-event line.

    Args:
        line: Raw line from the stream.

    Returns:
        The content fragment, or an empty string for keepalives and terminators.
    """
    if not line.startswith("data:"):
        return ""
    raw = line[5:].strip()
    if not raw or raw == "[DONE]":
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    choices = data.get("choices") or []
    if not choices:
        return ""
    return str((choices[0].get("delta") or {}).get("content") or "")


def parse_hf_line(line: str) -> str:
    """Parse a HuggingFace event-stream line, which arrives as a JSON string.

    Args:
        line: Raw ``data:`` line from the stream.

    Returns:
        The generated fragment with its surrounding quotes removed, or an empty
        string for terminators.
    """
    if not line.startswith("data:"):
        return ""
    raw = line[5:].strip()
    if not raw or raw == "[DONE]":
        return ""
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    return raw.replace("\\n", "\n")
