"""Tests for the Cerebrum's offline logic: prompts, history and confidence.

No network calls — the model transports are exercised in test_integration.py
with stubs.
"""

from __future__ import annotations

from brain.cerebrum.prompts import build_messages, build_system_prompt, trim_history
from brain.cerebrum.reasoning import _parse_ollama_line, estimate_confidence


def test_system_prompt_contains_identity_and_rules() -> None:
    """The system prompt must carry identity and safety rules."""
    prompt = build_system_prompt()
    assert "ZUZZ" in prompt
    assert "Jayanthan" in prompt
    assert "fabricate" in prompt


def test_system_prompt_includes_context() -> None:
    """Optional context is appended to the prompt."""
    assert "VS Code is open" in build_system_prompt("VS Code is open")


def test_trim_history_aligns_on_user_turn() -> None:
    """Trimming must not leave a reply without its question."""
    history = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
        {"role": "assistant", "content": "four"},
    ]
    trimmed = trim_history(history, limit=3)
    assert trimmed[0]["role"] == "user"
    assert len(trimmed) <= 3


def test_trim_history_keeps_short_history() -> None:
    """History under the limit is returned unchanged."""
    history = [{"role": "user", "content": "hi"}]
    assert trim_history(history, limit=20) == history


def test_build_messages_starts_with_system() -> None:
    """The assembled message list begins with a system prompt."""
    messages = build_messages("hello", [{"role": "user", "content": "earlier"}])
    assert messages[0]["role"] == "system"
    assert messages[-1] == {"role": "user", "content": "hello"}
    assert len(messages) == 3


def test_estimate_confidence_penalises_hedging() -> None:
    """Hedged answers score lower than confident ones."""
    assert estimate_confidence("The answer is 42.", 10) > estimate_confidence(
        "I think maybe it is 42, not sure.", 10
    )


def test_estimate_confidence_penalises_short_answer_to_long_question() -> None:
    """A very short reply to a long question is treated as low confidence."""
    question = "Explain in detail how the router picks between five levels " * 3
    assert estimate_confidence("Yes.", len(question)) < 0.7


def test_estimate_confidence_empty_is_zero() -> None:
    """An empty answer has no confidence."""
    assert estimate_confidence("", 10) == 0.0


def test_parse_ollama_line_extracts_content() -> None:
    """Content is pulled out of an Ollama stream line."""
    assert _parse_ollama_line('{"message":{"content":"hi"}}') == "hi"


def test_parse_ollama_line_ignores_keepalive() -> None:
    """Blank keepalive payloads yield no content."""
    assert _parse_ollama_line("{}") == ""
    assert _parse_ollama_line("not json") == ""
