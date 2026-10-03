"""Spinal Cord — the four desktop watchdog signals.

Clicks, keystrokes, application launches and refused hotkeys. Like the deletion
signal, all four are counted from the activity log rather than by watching the
machine, so the watchdog sees what *ZUZZ* did and not what the user did. A person
clicking quickly must not stop the agent.

The action-name sets are mutually exclusive, and that is the point of
``test_the_signal_sets_do_not_overlap``: the counts are matched by substring, so
a naive name like ``hotkey_blocked`` would also contain ``"key"`` and a refused
hotkey would be counted as both a refusal *and* a burst of keystrokes. That
would let an agent trip the keystroke signal by having its keystrokes refused —
tripping the watchdog precisely when ZUZZ did the safe thing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from brain.brainstem.activity_logger import ActivityLogger
from brain.spinal_cord.watchdog import (
    APP_OPEN_ACTIONS,
    APP_OPEN_WINDOW_SECONDS,
    BLOCKED_HOTKEY_ACTIONS,
    BLOCKED_HOTKEY_LIMIT,
    CLICK_ACTIONS,
    CLICK_WINDOW_SECONDS,
    HOTKEY_WINDOW_SECONDS,
    KEYSTROKE_ACTIONS,
    KEYSTROKE_WINDOW_SECONDS,
    MASS_APP_OPEN_LIMIT,
    MASS_CLICK_LIMIT,
    MASS_KEYSTROKE_LIMIT,
    Watchdog,
)
from tests.watchdog_fakes import FakeSwitch

ALL_ACTIONS = (
    CLICK_ACTIONS,
    KEYSTROKE_ACTIONS,
    APP_OPEN_ACTIONS,
    BLOCKED_HOTKEY_ACTIONS,
)


def watching(logger: ActivityLogger, **kwargs) -> Watchdog:
    """Return a watchdog with an idle CPU reading and a real logger."""
    watchdog = Watchdog(FakeSwitch(), logger=logger, **kwargs)
    watchdog.max_cpu_percent = lambda: 1.0  # type: ignore[method-assign]
    return watchdog


def logger_for(tmp_path: Path) -> ActivityLogger:
    """Return an isolated activity logger."""
    return ActivityLogger(tmp_path / "activity.jsonl")


def stamped(logger: ActivityLogger, action: str, count: int, seconds_ago: float = 0.0) -> None:
    """Write ``count`` records with a controlled timestamp."""
    moment = datetime.fromtimestamp(
        __import__("time").time() - seconds_ago, tz=timezone.utc
    ).isoformat()
    for index in range(count):
        logger.log("hands", action, index=index, timestamp=moment)


# ---------------------------------------------------------------------
# Clicks
# ---------------------------------------------------------------------


def test_a_click_burst_trips(tmp_path: Path) -> None:
    """Fifty clicks in ten seconds is a runaway loop, not a person."""
    logger = logger_for(tmp_path)
    stamped(logger, "mouse_click", MASS_CLICK_LIMIT + 1)
    report = watching(logger).poll()
    assert report["tripped"] is True
    assert "clicks" in report["reason"]


def test_clicks_just_under_the_limit_do_not_trip(tmp_path: Path) -> None:
    """The boundary sits in the safe direction."""
    logger = logger_for(tmp_path)
    stamped(logger, "mouse_click", MASS_CLICK_LIMIT - 1)
    assert watching(logger).poll()["tripped"] is False


def test_clicks_outside_the_window_are_not_counted(tmp_path: Path) -> None:
    """A slow afternoon of clicking is not a loop."""
    logger = logger_for(tmp_path)
    stamped(
        logger,
        "mouse_click",
        MASS_CLICK_LIMIT + 1,
        seconds_ago=CLICK_WINDOW_SECONDS + 30,
    )
    assert watching(logger).poll()["tripped"] is False


def test_the_click_limit_is_configurable(tmp_path: Path) -> None:
    """A caller may tighten a limit without editing the module."""
    logger = logger_for(tmp_path)
    stamped(logger, "mouse_click", 4)
    watchdog = watching(logger, click_limit=3)
    report = watchdog.poll()
    assert report["tripped"] is True
    assert report["clicks_in_window"] == 4


# ---------------------------------------------------------------------
# Keystrokes
# ---------------------------------------------------------------------


def test_a_keystroke_burst_trips(tmp_path: Path) -> None:
    """Five hundred keys in ten seconds is not typing."""
    logger = logger_for(tmp_path)
    stamped(logger, "type_text", MASS_KEYSTROKE_LIMIT + 1)
    report = watching(logger).poll()
    assert report["tripped"] is True
    assert "keystrokes" in report["reason"]


def test_typing_just_under_the_limit_does_not_trip(tmp_path: Path) -> None:
    """A long paste is still allowed."""
    logger = logger_for(tmp_path)
    stamped(logger, "type_text", MASS_KEYSTROKE_LIMIT - 1)
    assert watching(logger).poll()["tripped"] is False


def test_keystrokes_outside_the_window_are_not_counted(tmp_path: Path) -> None:
    """Slow typing is typing."""
    logger = logger_for(tmp_path)
    stamped(
        logger,
        "key_press",
        MASS_KEYSTROKE_LIMIT + 1,
        seconds_ago=KEYSTROKE_WINDOW_SECONDS + 30,
    )
    assert watching(logger).poll()["tripped"] is False


def test_the_keystroke_limit_is_configurable(tmp_path: Path) -> None:
    """A caller may tighten a limit without editing the module."""
    logger = logger_for(tmp_path)
    stamped(logger, "key_press", 11)
    assert watching(logger, keystroke_limit=10).poll()["tripped"] is True


# ---------------------------------------------------------------------
# Application launches
# ---------------------------------------------------------------------


def test_a_spawn_loop_trips(tmp_path: Path) -> None:
    """Eleven launches in a minute is a loop."""
    logger = logger_for(tmp_path)
    stamped(logger, "app_open", MASS_APP_OPEN_LIMIT + 1)
    report = watching(logger).poll()
    assert report["tripped"] is True
    assert "app launches" in report["reason"]


def test_launches_just_under_the_limit_do_not_trip(tmp_path: Path) -> None:
    """Opening a handful of apps is normal work."""
    logger = logger_for(tmp_path)
    stamped(logger, "app_open", MASS_APP_OPEN_LIMIT - 1)
    assert watching(logger).poll()["tripped"] is False


def test_launches_outside_the_window_are_not_counted(tmp_path: Path) -> None:
    """Two launches an hour apart are two launches."""
    logger = logger_for(tmp_path)
    stamped(
        logger,
        "app_open",
        MASS_APP_OPEN_LIMIT + 1,
        seconds_ago=APP_OPEN_WINDOW_SECONDS + 30,
    )
    assert watching(logger).poll()["tripped"] is False


def test_the_app_open_limit_is_configurable(tmp_path: Path) -> None:
    """A caller may tighten a limit without editing the module."""
    logger = logger_for(tmp_path)
    stamped(logger, "app_open", 3)
    assert watching(logger, app_open_limit=2).poll()["tripped"] is True


# ---------------------------------------------------------------------
# Refused hotkeys
# ---------------------------------------------------------------------


def test_a_run_of_refused_hotkeys_trips(tmp_path: Path) -> None:
    """One refusal is the blocklist working; four is something probing it."""
    logger = logger_for(tmp_path)
    stamped(logger, "combo_refused", BLOCKED_HOTKEY_LIMIT + 1)
    report = watching(logger).poll()
    assert report["tripped"] is True
    assert "refused hotkeys" in report["reason"]


def test_a_single_refused_hotkey_does_not_trip(tmp_path: Path) -> None:
    """The whole point of the limit being above one."""
    logger = logger_for(tmp_path)
    stamped(logger, "combo_refused", 1)
    assert watching(logger).poll()["tripped"] is False


def test_refused_hotkeys_outside_the_window_are_not_counted(tmp_path: Path) -> None:
    """An occasional blocked key over a long session is the blocklist working."""
    logger = logger_for(tmp_path)
    stamped(
        logger,
        "combo_refused",
        BLOCKED_HOTKEY_LIMIT + 1,
        seconds_ago=HOTKEY_WINDOW_SECONDS + 30,
    )
    assert watching(logger).poll()["tripped"] is False


def test_the_blocked_hotkey_limit_is_configurable(tmp_path: Path) -> None:
    """A caller may tighten a limit without editing the module."""
    logger = logger_for(tmp_path)
    stamped(logger, "combo_refused", 3)
    assert watching(logger, blocked_hotkey_limit=2).poll()["tripped"] is True


# ---------------------------------------------------------------------
# The counts must not interfere with one another
# ---------------------------------------------------------------------


def test_the_signal_sets_do_not_overlap() -> None:
    """No action name may match two signals at once.

    Substring matching is generous, and these four sets are checked against the
    same log. A name like ``hotkey_blocked`` contains ``"key"``, so a refused
    hotkey would be counted as a keystroke burst — which would trip the
    keystroke signal precisely when ZUZZ refused to send the keys.
    """
    names = [
        "mouse_click",
        "mouse_double_click",
        "mouse_scroll",
        "type_text",
        "key_press",
        "key_down",
        "key_up",
        "key_hotkey",
        "app_open",
        "app_list",
        "combo_refused",
    ]
    for name in names:
        matched = [
            index
            for index, fragments in enumerate(ALL_ACTIONS)
            if any(fragment in name for fragment in fragments)
        ]
        assert len(matched) <= 1, f"{name} matches {len(matched)} signals: {matched}"


def test_a_refused_hotkey_is_not_counted_as_keystrokes(tmp_path: Path) -> None:
    """The specific collision this module exists to prevent."""
    logger = logger_for(tmp_path)
    stamped(logger, "combo_refused", 50)
    report = watching(logger).poll()
    assert report["keystrokes_in_window"] == 0
    assert report["blocked_hotkeys_in_window"] == 50


def test_the_cpu_and_deletion_signals_still_win_first(tmp_path: Path) -> None:
    """The original two signals keep their place in the reason string."""
    logger = logger_for(tmp_path)
    for index in range(30):
        logger.log("body", f"delete_file_{index}")
    report = watching(logger).poll()
    assert report["tripped"] is True
    assert "deletions" in report["reason"]


def test_an_idle_desktop_never_trips(tmp_path: Path) -> None:
    """No signal fires on an empty log."""
    assert watching(logger_for(tmp_path)).poll()["tripped"] is False


def test_every_signal_appears_in_the_report(tmp_path: Path) -> None:
    """The UI reads the report, so every count must be present even at zero."""
    report = watching(logger_for(tmp_path)).poll()
    for field in (
        "clicks_in_window",
        "keystrokes_in_window",
        "app_opens_in_window",
        "blocked_hotkeys_in_window",
    ):
        assert report[field] == 0