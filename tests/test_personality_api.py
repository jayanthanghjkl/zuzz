"""Cerebellum — personality API tests.

The endpoints, not the region: ``tests/test_cerebellum.py`` pins what the
configuration *means*, and this pins what a client is *told*. The one thing worth
its own file is the reporting of a value the config could not honour, because that
is the difference between a Settings page that explains itself and one that
silently rewrites what the operator typed.

Every test runs against a throwaway config file, so a test can state an invalid
value without changing the voice the developer's own ZUZZ is speaking.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from brain.cerebellum import Cerebellum

CONFIG: Dict[str, Any] = {
    "name": "ZUZZ",
    "owner": "Jayanthan",
    "user_name": "Jayanthan",
    "wake_word": "zuzz",
    "tone": ["direct", "warm"],
    "max_sentences_per_reply": 8,
    "style": "formal_friendly",
    "humor_level": "subtle",
    "verbosity": "concise",
    "greeting": "Hey {user_name}",
    "signoff": "— {name}",
    "quirks": ["likes a precise question"],
}

SETTINGS = "/api/personality/settings"


def write_config(path: Path, **changes: Any) -> Path:
    """Write a throwaway personality file.

    Args:
        path: Where to write it.
        **changes: Keys to override in the base configuration.

    Returns:
        The path written.
    """
    path.write_text(json.dumps(dict(CONFIG, **changes)), encoding="utf-8")
    return path


@pytest.fixture()
def client(kill_switch_app, tmp_path: Path) -> TestClient:
    """Return a client bound to a throwaway personality file.

    Args:
        kill_switch_app: The isolated app from ``conftest``.
        tmp_path: Somewhere to put the config.

    Returns:
        A client over ``tmp_path``'s config.
    """
    app = kill_switch_app
    app.state.cerebellum = Cerebellum(
        path=write_config(tmp_path / "personality.json")
    )
    return TestClient(app)


def test_the_settings_page_reports_nothing_when_the_file_is_valid(client: TestClient) -> None:
    """A correct config must come back clean, or the warning means nothing."""
    body = client.get(SETTINGS).json()

    assert body["invalid_fields"] == {}
    assert body["warnings"] == []


def test_personality_api_returns_invalid_fields_as_warnings(client: TestClient) -> None:
    """A value outside the vocabulary is reported, not quietly replaced.

    The page shows the effective value, so a config saying ``low`` under a field
    that only accepts ``terse``/``concise``/``detailed`` produces a read-back the
    operator did not write. Returning the rejection alongside it is what makes the
    difference explainable — a read that silently disagrees with the file is
    indistinguishable from a bug, and gets debugged as one.
    """
    app = client.app
    write_config(app.state.cerebellum.path, verbosity="low")

    response = client.get(SETTINGS)
    assert response.status_code == 200

    body = response.json()
    assert body["verbosity"] == "concise", "the default is still what is spoken"
    assert body["invalid_fields"] == {"verbosity": "low"}

    assert len(body["warnings"]) == 1, "one bad field, one warning"
    warning = body["warnings"][0]
    assert "personality.verbosity" in warning
    assert "low" in warning and "concise" in warning
    assert "terse, concise, detailed" in warning, "the legal values must be named"


def test_a_write_reports_a_previously_invalid_value_too(
    client: TestClient,
) -> None:
    """Fixing one field must not hide a second one that is still wrong.

    The obvious implementation reports only what the current request touched,
    which means the last known-bad field keeps no trace once anything else is
    saved. Reporting everything the file still gets wrong is what keeps the
    warning honest.
    """
    app = client.app
    path = app.state.cerebellum.path
    write_config(path, verbosity="low", style="sarcastic")

    applied = client.post(SETTINGS, json={"settings": {"verbosity": "detailed"}})

    assert applied.status_code == 200
    written = applied.json()["settings"]
    assert written["verbosity"] == "detailed"
    assert written["invalid_fields"] == {"style": "sarcastic"}, (
        "the untouched bad field is still bad, so it is still reported"
    )


def test_the_update_route_still_refuses_an_invalid_value_rather_than_fixing_it(
    client: TestClient,
) -> None:
    """Reporting is for the file; a request is still held to the vocabulary.

    If a POST could write ``low``, the warning above would be reporting a value
    the API itself produced — and the two paths would disagree about what is legal.
    """
    refused = client.post(SETTINGS, json={"settings": {"verbosity": "low"}})

    assert refused.status_code == 400
    assert "terse" in refused.json()["detail"]