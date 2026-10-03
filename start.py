"""ZUZZ — main entry point.

Starts the Brainstem: the FastAPI web server, the background scheduler and the
model reaper. Run with::

    python start.py
    python start.py --reload      # development auto-reload
    python start.py --port 9000
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def parse_args() -> argparse.Namespace:
    """Parse command line arguments.

    Returns:
        Parsed arguments with ``host``, ``port`` and ``reload``.
    """
    parser = argparse.ArgumentParser(description="Start the ZUZZ digital brain")
    parser.add_argument("--host", default=None, help="Bind address (default from settings)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default from settings)")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    return parser.parse_args()


def main() -> int:
    """Start the server.

    Returns:
        Process exit code.
    """
    args = parse_args()
    try:
        from brain.brainstem.server import run
    except ImportError as exc:
        print(f"Missing dependency: {exc}. Run: pip install -r requirements.txt")
        return 1

    run(host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
