"""Shared runtime configuration loading for ZUZZ.

Reads `.env` for secrets and `config/*.json` for non-secret settings, and
exposes them through small accessor functions so no module needs to know the
on-disk layout. Note: this module is not in the PROJECT.md section 14 tree —
it exists because several brain regions need the same settings and the spec
has no common layer.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

# Jayanthan's `.env` and the spec use these shorter names; the code uses the
# canonical ones. Both are accepted so a hand-written `.env` just works.
ENV_ALIASES: Dict[str, str] = {
    "OLLAMA_HOST": ("OLLAMA_URL",),
    "OLLAMA_URL": ("OLLAMA_HOST",),
    "HUGGINGFACE_API_KEY": ("HF_API_KEY", "HUGGINGFACE_TOKEN"),
    "HF_API_KEY": ("HUGGINGFACE_API_KEY",),
    "COLAB_API_URL": ("COLAB_URL", "NGROK_URL"),
    "COLAB_URL": ("COLAB_API_URL", "NGROK_URL"),
    "ZUZZ_PORT": ("WEB_PORT", "PORT"),
    "WEB_PORT": ("ZUZZ_PORT",),
    "TELEGRAM_BOT_TOKEN": ("TELEGRAM_TOKEN",),
}


# Loaded once at import so provider detection never depends on which component
# happened to read settings.json first. Real environment values win.
load_dotenv(ROOT / ".env", override=False)


def _read_json(name: str) -> Dict[str, Any]:
    """Load a JSON file from the config directory.

    Args:
        name: File name such as ``settings.json``.

    Returns:
        Parsed JSON mapping, or an empty dict when the file is missing.
    """
    path = CONFIG_DIR / name
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc


@lru_cache(maxsize=1)
def get_settings() -> Dict[str, Any]:
    """Return general settings from ``config/settings.json``."""
    return _read_json("settings.json")


@lru_cache(maxsize=1)
def get_models() -> Dict[str, Any]:
    """Return the level-to-model routing table."""
    return _read_json("models.json")


@lru_cache(maxsize=1)
def get_personality() -> Dict[str, Any]:
    """Return ZUZZ personality settings."""
    return _read_json("personality.json")


def get_resource_limits() -> Dict[str, Any]:
    """Return resource budgets from ``config/resource_limits.json``.

    Read fresh on each lookup rather than memoised, so an edit to the budgets
    takes effect without a restart.
    """
    return _read_json("resource_limits.json")


def get_thermal_limits() -> Dict[str, Any]:
    """Return thermal thresholds from ``config/thermal_limits.json``.

    Not memoised: thermal limits can be tuned without a restart.
    """
    return _read_json("thermal_limits.json")


def get_danger_patterns() -> Dict[str, Any]:
    """Return known dangerous commands, paths and keywords."""
    return _read_json("danger_patterns.json")


def get_apps_whitelist() -> Dict[str, Any]:
    """Return the Body/Hands application whitelist.

    Read fresh on each lookup rather than memoised, so an edit to the whitelist
    takes effect without a restart. The Desktop tab says a restart is needed;
    that is a deliberate belt-and-braces choice, not a limitation of this
    function — re-reading costs nothing and a narrower whitelist can never be
    hidden by a stale cache.
    """
    return _read_json("apps_whitelist.json")


def get_dangerous_hotkeys() -> Dict[str, Any]:
    """Return the always-blocked and confirm-required hotkey combinations."""
    return _read_json("dangerous_hotkeys.json")


def get_trust_levels() -> Dict[str, Any]:
    """Return the permission matrix from ``config/trust_levels.json``.

    Not memoised: the safety gate re-reads this on every decision so an edit to
    the matrix takes effect without a restart, and a narrower matrix can never
    be hidden by a stale cache.
    """
    return _read_json("trust_levels.json")


def get_vision_thresholds() -> Dict[str, Any]:
    """Return vision confidence thresholds from ``config/vision_thresholds.json``.

    Not memoised, for the reason every other safety config here is not: the
    Occipital Lobe asks this question on every element lookup and OCR read, and a
    threshold the user tuned on real hardware must not need a server restart to
    take effect.
    """
    return _read_json("vision_thresholds.json")


def env(name: str, default: str = "") -> str:
    """Read an environment variable, falling back to ``default``.

    Aliases in :data:`ENV_ALIASES` are checked when the canonical name is unset,
    so both ``HUGGINGFACE_API_KEY`` and ``HF_API_KEY`` resolve to the same value.

    Args:
        name: Canonical variable name.
        default: Value returned when neither name is set.

    Returns:
        The resolved value, or ``default``.
    """
    for candidate in (name, *ENV_ALIASES.get(name, ())):
        value = os.getenv(candidate)
        if value not in (None, ""):
            return value
    return default


def env_int(name: str, default: int) -> int:
    """Read an environment variable as an integer."""
    try:
        return int(env(name, str(default)))
    except ValueError:
        return default


def model_for_level(level: int) -> Optional[str]:
    """Resolve the model identifier a routing level should use.

    Args:
        level: Routing level 1-5.

    Returns:
        Model string, or None when the level is not configured.
    """
    level_cfg = get_models().get("levels", {}).get(str(level), {})
    env_name = level_cfg.get("model_env")
    if not env_name:
        return None
    defaults = {
        "LOCAL_MODEL_FAST": "qwen2.5-coder:1.5b",
        "LOCAL_MODEL_SMART": "qwen2.5-coder:3b",
        "CLOUD_MODEL": "groq/openai/gpt-oss-120b",
    }
    return env(env_name, defaults.get(env_name, "")) or None


def provider_for_level(level: int) -> str:
    """Resolve whether a routing level runs locally or in the cloud."""
    level_cfg = get_models().get("levels", {}).get(str(level), {})
    return str(level_cfg.get("provider", "local"))


def timeout_for_level(level: int) -> int:
    """Resolve the request timeout, in seconds, for a routing level."""
    level_cfg = get_models().get("levels", {}).get(str(level), {})
    return int(level_cfg.get("timeout", 60))


def reset_cache() -> None:
    """Clear memoised settings — used by tests."""
    get_settings.cache_clear()
    get_models.cache_clear()
    get_personality.cache_clear()
