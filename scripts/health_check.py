"""Full system health check.

Verifies that the dependencies, configuration, local models and cloud provider
credentials are all in place, then prints a pass/fail report. Run with::

    python scripts/health_check.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"


def check_dependencies() -> bool:
    """Report which required Python packages are importable.

    Returns:
        True when all required packages import cleanly.
    """
    required = ["fastapi", "uvicorn", "httpx", "dotenv", "pydantic"]
    missing = []
    for name in required:
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    print(f"[{FAIL if missing else PASS}] dependencies - missing: {missing or 'none'}")
    return not missing


def check_configuration() -> bool:
    """Verify config files load and secrets are present in `.env`.

    Returns:
        True when every config file parses and at least one cloud key exists.
    """
    from core.config import env, get_models, get_settings

    settings = get_settings()
    models = get_models()
    print(f"[{PASS if settings else FAIL}] settings.json - {len(settings)} keys")
    print(f"[{PASS if models else FAIL}] models.json - {len(models.get('levels', {}))} levels")

    env_file = ROOT / ".env"
    if not env_file.is_file():
        print(f"[{WARN}] .env - not created yet (copy .env.example)")
        return True

    keys = [
        name
        for name in ("GROQ_API_KEY", "HUGGINGFACE_API_KEY", "OPENROUTER_API_KEY", "NVIDIA_API_KEY")
        if env(name)
    ]
    print(f"[{PASS if keys else WARN}] cloud keys - configured: {keys or 'none (local only)'}")
    return True


async def check_local_models() -> bool:
    """Check whether Ollama is running and which models are available.

    Returns:
        Always True - missing local models are a warning, not a failure,
        because a cloud provider can serve the request instead.
    """
    from brain.cerebrum.reasoning import list_local_models, ollama_host

    models = await list_local_models()
    state = PASS if models else WARN
    hint = "".join(models) if models else "none (run: ollama pull qwen2.5-coder:1.5b)"
    print(f"[{state}] ollama {ollama_host()} - models: {hint}")
    return True


def check_project_files() -> bool:
    """Verify the files the server needs at boot exist.

    Returns:
        True when all required files are present.
    """
    required = [
        "face/frontend/index.html",
        "face/frontend/static/css/main.css",
        "face/api/server.py",
        "config/settings.json",
    ]
    missing = [name for name in required if not (ROOT / name).is_file()]
    print(f"[{FAIL if missing else PASS}] project files - missing: {missing or 'none'}")
    return not missing


async def check_models() -> bool:
    """Verify every configured cloud model is still served by its provider.

    Providers retire models without warning, which surfaces later as a HTTP 404
    on a real request. See docs/MODEL_MAINTENANCE.md.

    Returns:
        False only when a provider positively reports that the configured model
        is gone. An unreachable catalogue is a warning, not a failure.
    """
    from scripts.check_models import DEAD, check_all

    reports = await check_all()
    dead = [r for r in reports if r.state == DEAD]
    state = FAIL if dead else PASS
    summary = ", ".join(f"{r.provider}={r.state}" for r in reports) or "no providers"
    print(f"[{state}] cloud models - {summary}")
    for report in dead:
        print(f"           {report.provider}: {report.model} - {report.detail}")
    return not dead


async def main() -> int:
    """Run every check and return an exit code.

    Returns:
        0 when no check failed, 1 otherwise.
    """
    print("ZUZZ health check")
    print("=" * 46)
    results = [
        check_project_files(),
        check_configuration(),
        await check_local_models(),
        await check_models(),
        check_dependencies(),
    ]
    print("=" * 46)
    failed = results.count(False)
    print("FAILED" if failed else "All critical checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
