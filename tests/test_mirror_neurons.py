"""Tests for Mirror Neurons — observation, pattern detection, and macro generation."""

from __future__ import annotations

import time
from typing import Any, Dict, List

import pytest

from brain.mirror_neurons import MirrorNeurons
from brain.mirror_neurons.exceptions import (
    MacroCreationRefused,
    ObservationAlreadyActive,
    ObservationNotActive,
    ObservationRefused,
    PatternNotFound,
)
from brain.mirror_neurons.observation_filter import ObservationFilter, FilterRule, IGNORE


class TestObservationFilter:
    """Test the privacy filter that excludes sensitive actions."""

    def test_default_filter_watches_everything(self):
        filter = ObservationFilter()
        verdict = filter.decide({"tool": "file_write", "target": "test.txt"})
        assert verdict.watched is True

    def test_ignore_password_fields(self):
        rule = FilterRule(
            name="password_field",
            kind="target",
            pattern="password",
            verdict=IGNORE,
            reason="password field detected",
        )
        filter = ObservationFilter(rules=[rule])
        verdict = filter.decide({"tool": "keyboard_type", "target": "password_field"})
        assert verdict.watched is False
        assert verdict.rule is not None
        assert verdict.rule.name == "password_field"

    def test_ignore_api_keys(self):
        rule = FilterRule(
            name="api_key",
            kind="any",
            pattern="api_key",
            verdict=IGNORE,
            reason="API key detected",
        )
        filter = ObservationFilter(rules=[rule])
        verdict = filter.decide({"tool": "file_write", "target": "config.py", "detail": {"content": "api_key = 'secret'"}})
        assert verdict.watched is False

    def test_ignore_banking_windows(self):
        rule = FilterRule(
            name="banking",
            kind="context",
            pattern="bank",
            verdict=IGNORE,
            reason="banking app",
        )
        filter = ObservationFilter(rules=[rule])
        verdict = filter.decide({"tool": "mouse_click", "context": "banking"})
        assert verdict.watched is False

    def test_never_ignore_overrides(self):
        # never_ignore should override a previous ignore rule for specific matches
        ignore = FilterRule(name="ignore_passwords", kind="target", pattern="password", verdict=IGNORE)
        watch = FilterRule(name="watch_specific_write", kind="tool", pattern="file_write", verdict="watch")
        filter = ObservationFilter(rules=[ignore, watch])
        # This has "password" in target, so ignore rule matches first
        verdict = filter.decide({"tool": "file_write", "target": "password_field"})
        assert verdict.watched is False
        # This doesn't have "password", so ignore doesn't match, watch matches
        verdict = filter.decide({"tool": "file_write", "target": "normal.txt"})
        assert verdict.watched is True


class TestMirrorNeuronsObservation:
    """Test starting/stopping observation sessions."""

    def test_start_observation_returns_session_id(self):
        mn = MirrorNeurons()
        session_id = mn.start_observation()
        assert session_id.startswith("obs_")
        assert mn.active is True

    def test_start_observation_twice_raises(self):
        mn = MirrorNeurons()
        mn.start_observation()
        with pytest.raises(ObservationAlreadyActive):
            mn.start_observation()

    def test_stop_observation_returns_session(self):
        mn = MirrorNeurons()
        session_id = mn.start_observation()
        session = mn.stop_observation()
        assert session.session_id == session_id
        assert session.record_count == 0
        assert mn.active is False

    def test_stop_without_start_raises(self):
        mn = MirrorNeurons()
        with pytest.raises(ObservationNotActive):
            mn.stop_observation()

    def test_records_are_filtered(self):
        mn = MirrorNeurons()
        mn.start_observation()
        # This should be filtered out by default filter (empty filter watches all)
        # But we can add a filter that ignores everything
        from brain.mirror_neurons.observation_filter import FilterRule, IGNORE
        mn._recorder.filter = ObservationFilter(rules=[
            FilterRule(name="ignore_all", kind="any", pattern="", verdict=IGNORE)
        ])
        mn.record_act("file_write", "secret.txt")
        session = mn.stop_observation()
        assert session.record_count == 0


class TestPatternDetection:
    """Test pattern detection with frequency threshold >= 5."""

    def test_no_patterns_below_threshold(self):
        mn = MirrorNeurons()
        records = [
            {"tool": "file_write", "target": "a.txt", "captured_at": time.time()},
            {"tool": "file_write", "target": "b.txt", "captured_at": time.time() + 1},
        ] * 2  # Only 2 repetitions of a 2-step sequence
        patterns = mn.get_detected_patterns(records)
        assert patterns == []

    def test_pattern_detected_at_threshold_5(self):
        mn = MirrorNeurons()
        base_time = time.time()
        # With window=2 (minimum), we need 5 repetitions of a 2-step sequence = 10 records
        records = []
        for i in range(5):
            records.append({"tool": "file_write", "target": f"file_{i}.txt", "captured_at": base_time + i * 2})
            records.append({"tool": "keyboard_type", "target": f"text_{i}", "captured_at": base_time + i * 2 + 0.1})
        from brain.mirror_neurons.workflow_learner import WorkflowLearner
        learner = WorkflowLearner(min_repetitions=5, window=2)
        patterns = learner.learn(records)
        assert len(patterns) == 1
        assert patterns[0].occurrences == 5
        assert len(patterns[0].steps) == 2
        assert patterns[0].steps[0]["tool"] == "file_write"
        assert patterns[0].steps[1]["tool"] == "keyboard_type"

    def test_patterns_sorted_by_occurrences_then_length(self):
        mn = MirrorNeurons()
        base_time = time.time()
        from brain.mirror_neurons.workflow_learner import WorkflowLearner
        # Use window=2 to find 2-step patterns
        learner = WorkflowLearner(min_repetitions=5, window=2)
        # Pattern A: 5 occurrences, 2 steps (file_write -> keyboard_type) - different tools
        records_a = []
        for i in range(5):
            records_a.append({"tool": "file_write", "target": f"a_{i}.txt", "captured_at": base_time + i * 1000})
            records_a.append({"tool": "keyboard_type", "target": f"text_{i}", "captured_at": base_time + i * 1000 + 1})
        # Pattern B: 5 occurrences, 2 steps (mouse_click -> file_write) - different tools, different time range
        records_b = []
        for i in range(5):
            records_b.append({"tool": "mouse_click", "target": f"100,200", "captured_at": base_time + 10000 + i * 1000})
            records_b.append({"tool": "file_write", "target": f"hello_{i}.txt", "captured_at": base_time + 10000 + i * 1000 + 1})
        records = records_a + records_b
        patterns = learner.learn(records)
        # Both have 5 occurrences
        assert len(patterns) == 2
        assert patterns[0].occurrences == 5
        assert patterns[1].occurrences == 5
        assert len(patterns[0].steps) == 2
        assert len(patterns[1].steps) == 2

    def test_get_patterns_without_session_raises(self):
        mn = MirrorNeurons()
        with pytest.raises(ObservationNotActive):
            mn.get_detected_patterns()


class TestMacroCreation:
    """Test macro creation from patterns."""

    def test_create_macro_from_pattern(self):
        mn = MirrorNeurons()
        base_time = time.time()
        records = []
        for i in range(5):
            records.append({"tool": "keyboard_type", "target": f"text_{i}", "captured_at": base_time + i * 2})
            records.append({"tool": "keyboard_type", "target": f"more_{i}", "captured_at": base_time + i * 2 + 0.1})
        # MirrorNeurons default learner uses window=None (whole session).
        # Use a custom learner with window=2 to detect the 2-step pattern.
        from brain.mirror_neurons.workflow_learner import WorkflowLearner
        learner = WorkflowLearner(min_repetitions=5, window=2)
        patterns = learner.learn(records)
        pattern_id = patterns[0].id
        # Replace learner so create_macro_from_pattern uses the same learner
        mn._learner = learner

        macro = mn.create_macro_from_pattern(pattern_id, records)
        assert macro.name.startswith("mirror_")
        assert macro.source == "mirror"
        assert macro.pattern_id == pattern_id
        assert len(macro.steps) == 2
        assert macro.steps[0].tool == "keyboard_type"
        assert macro.steps[1].tool == "keyboard_type"
        assert macro.steps[0].params == {"text": "text_0"}
        assert macro.steps[1].params == {"text": "more_0"}

    def test_create_macro_unknown_pattern_raises(self):
        mn = MirrorNeurons()
        records = []
        for i in range(5):
            records.append({"tool": "file_write", "target": f"test_{i}.txt", "captured_at": time.time() + i * 2})
            records.append({"tool": "keyboard_type", "target": f"text_{i}", "captured_at": time.time() + i * 2 + 0.1})
        # Use custom learner to populate patterns
        from brain.mirror_neurons.workflow_learner import WorkflowLearner
        learner = WorkflowLearner(min_repetitions=5, window=2)
        mn._learner = learner
        mn.get_detected_patterns(records)  # populate patterns
        with pytest.raises(PatternNotFound):
            mn.create_macro_from_pattern("nonexistent", records)

    def test_suggest_automation_returns_macro(self):
        mn = MirrorNeurons()
        base_time = time.time()
        records = []
        for i in range(5):
            records.append({"tool": "keyboard_type", "target": f"test_{i}", "captured_at": base_time + i * 2})
            records.append({"tool": "keyboard_type", "target": f"more_{i}", "captured_at": base_time + i * 2 + 0.1})
        # Use custom learner with window=2
        from brain.mirror_neurons.workflow_learner import WorkflowLearner
        learner = WorkflowLearner(min_repetitions=5, window=2)
        mn._learner = learner
        macro = mn.suggest_automation(records)
        assert macro is not None
        assert macro.name.startswith("mirror_")

    def test_suggest_automation_returns_none_below_threshold(self):
        mn = MirrorNeurons()
        records = []
        for i in range(2):
            records.append({"tool": "file_write", "target": f"test_{i}.txt", "captured_at": time.time() + i * 2})
            records.append({"tool": "keyboard_type", "target": f"text_{i}", "captured_at": time.time() + i * 2 + 0.1})
        # Use custom learner with window=2
        from brain.mirror_neurons.workflow_learner import WorkflowLearner
        learner = WorkflowLearner(min_repetitions=5, window=2)
        mn._learner = learner
        macro = mn.suggest_automation(records)
        assert macro is None


class TestMacroValidation:
    """Test macro validation rejects invalid patterns."""

    def test_macro_too_many_steps_rejected(self):
        from brain.mirror_neurons.macro_generator import generate_macro, MAX_MACRO_STEPS
        from brain.mirror_neurons.pattern_cloner import Pattern

        # Create a pattern with more than MAX_MACRO_STEPS steps
        steps = [
            {"tool": "file_write", "target": f"file{i}.txt", "captured_at": time.time() + i}
            for i in range(MAX_MACRO_STEPS + 1)
        ]
        pattern = Pattern(
            id="test_long",
            steps=steps,
            occurrences=5,
            first_seen=time.time(),
            last_seen=time.time() + 100,
        )

        with pytest.raises(MacroCreationRefused):
            generate_macro(pattern)

    def test_macro_unknown_tool_rejected(self):
        from brain.mirror_neurons.macro_generator import generate_macro
        from brain.mirror_neurons.pattern_cloner import Pattern

        steps = [
            {"tool": "nonexistent_tool", "target": "test.txt", "captured_at": time.time() + i}
            for i in range(3)
        ]
        pattern = Pattern(
            id="test_bad_tool",
            steps=steps,
            occurrences=5,
            first_seen=time.time(),
            last_seen=time.time() + 100,
        )

        with pytest.raises(MacroCreationRefused):
            generate_macro(pattern)


class TestObservationRefused:
    """Test observation refused when filter ignores everything."""

    def test_all_ignore_filter_refused(self):
        from brain.mirror_neurons.observation_filter import FilterRule, IGNORE
        mn = MirrorNeurons()
        mn._recorder.filter = ObservationFilter(rules=[
            FilterRule(name="ignore_all", kind="any", pattern="", verdict=IGNORE)
        ])
        with pytest.raises(ObservationRefused):
            mn.start_observation()


class TestRecordAct:
    """Test recording acts during observation."""

    def test_record_act_when_active(self):
        mn = MirrorNeurons()
        mn.start_observation()
        verdict = mn.record_act("file_write", "test.txt")
        assert verdict.watched is True
        session = mn.stop_observation()
        assert session.record_count == 1
        assert session.records[0]["tool"] == "file_write"
        assert session.records[0]["target"] == "test.txt"

    def test_record_act_when_inactive_returns_ignored(self):
        mn = MirrorNeurons()
        verdict = mn.record_act("file_write", "test.txt")
        assert verdict.watched is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])