"""Tests for the Thalamus: complexity classification and fallback ordering."""

from __future__ import annotations

from brain.thalamus.complexity_analyzer import ComplexityAnalyzer
from brain.thalamus.load_balancer import LoadBalancer, ProviderState
from brain.thalamus.router import Router


def test_greeting_is_level_one() -> None:
    """A greeting routes to the fast local model."""
    assert ComplexityAnalyzer().analyze("hello").level == 1


def test_file_organisation_is_level_two() -> None:
    """A medium task routes to the local 3B model."""
    assert ComplexityAnalyzer().analyze("organize my downloads folder").level == 2


def test_debugging_is_level_three() -> None:
    """Debugging needs cloud reasoning."""
    decision = ComplexityAnalyzer().analyze("debug why this function returns None")
    assert decision.level == 3
    assert "cloud" in decision.reason


def test_full_app_build_is_level_four() -> None:
    """Building a whole app escalates to a cloud agent team."""
    assert ComplexityAnalyzer().analyze("build a full app for tracking habits").level == 4


def test_recurring_task_is_level_five() -> None:
    """Continuous work routes to a background agent."""
    assert ComplexityAnalyzer().analyze("monitor prices daily and alert me").level == 5


def test_conceptual_topic_escalates_to_cloud() -> None:
    """A hard concept needs cloud reasoning even with a simple verb."""
    assert ComplexityAnalyzer().analyze("Explain quantum computing").level == 3
    assert ComplexityAnalyzer().analyze("Explain how transformers compute attention").level == 3


def test_bare_explain_stays_local() -> None:
    """A short 'explain this' is a medium task, not a cloud task."""
    assert ComplexityAnalyzer().analyze("explain this").level == 2


def test_code_writing_is_medium_by_default() -> None:
    """Writing a short script matches the spec's medium band."""
    assert ComplexityAnalyzer().analyze("Write a Python web scraper").level == 2


def test_offline_caps_level_at_two() -> None:
    """Without internet the router must not pick a cloud level."""
    decision = ComplexityAnalyzer().analyze("build a full app for tracking habits", internet_available=False)
    assert decision.level == 2
    assert decision.offline_fallback is True


def test_long_input_escalates() -> None:
    """A very long prompt is treated as complex reasoning."""
    assert ComplexityAnalyzer().analyze("please " * 200).level >= 3


def test_fallback_order_for_cloud_level(monkeypatch) -> None:
    """Cloud levels degrade to local models when a provider is configured."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    router = Router()
    order = router._fallback_order(level=3, internet_available=True)
    assert order[0] == 3
    assert 1 in order and 2 in order


def test_fallback_order_skips_cloud_without_providers() -> None:
    """With no cloud credentials the router stays local from the start."""
    router = Router()
    assert router._fallback_order(level=3, internet_available=True) == [2, 1]


def test_fallback_order_offline_stays_local() -> None:
    """Offline fallback never reaches a cloud level."""
    order = Router()._fallback_order(level=5, internet_available=False)
    assert all(level <= 2 for level in order)


def test_fallback_order_escalates_local_levels() -> None:
    """A failed local model escalates rather than giving up."""
    order = Router()._fallback_order(level=1, internet_available=True)
    assert order[0] == 1
    assert len(order) > 1


def test_load_balancer_benches_failed_provider() -> None:
    """A failing provider is skipped until its cooldown expires."""
    balancer = LoadBalancer()
    balancer._states["groq"] = ProviderState(name="groq")
    balancer._states["openrouter"] = ProviderState(name="openrouter")

    assert balancer.select().provider == "groq"
    balancer.report_failure("groq")

    selection = balancer.select()
    assert selection.provider == "openrouter"
    assert selection.skipped == ["groq"]
    assert balancer.status()["groq"]["benched"] is True


def test_load_balancer_returns_none_when_all_benched() -> None:
    """With no healthy provider the router must fall back to local."""
    balancer = LoadBalancer()
    balancer._states["groq"] = ProviderState(name="groq")
    balancer.report_failure("groq")
    assert balancer.select().provider is None


def test_router_rejects_empty_message() -> None:
    """Empty input is a programming error, not a brain query."""
    import asyncio

    router = Router()
    try:
        asyncio.run(router.route("   "))
    except ValueError as exc:
        assert "empty" in str(exc)
    else:  # pragma: no cover - guard against silent acceptance
        raise AssertionError("Empty message should have been rejected")
