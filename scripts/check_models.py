"""Model liveness check.

Providers retire models without warning, and a retired model fails at request
time with HTTP 404 rather than at configuration time. This script asks every
configured connector for its current catalogue and reports whether the model
ZUZZ would actually call is still served.

Run it directly, or let ``scripts/health_check.py`` call it::

    python scripts/check_models.py
    python scripts/check_models.py --json
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from brain.corpus_callosum.cloud_bridge import CloudBridge  # noqa: E402
from brain.corpus_callosum.connector_state import ConnectorError  # noqa: E402

OK = "ok"
DEAD = "dead"
UNKNOWN = "unknown"
SKIPPED = "skipped"


@dataclass
class ModelReport:
    """Result of checking one provider's configured model.

    Attributes:
        provider: Provider identifier.
        model: The model ZUZZ would call.
        state: One of ``ok``, ``dead``, ``unknown`` or ``skipped``.
        detail: Explanation for the state.
        catalogue_size: How many models the provider reported.
    """

    provider: str
    model: str
    state: str
    detail: str
    catalogue_size: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form."""
        return {
            "provider": self.provider,
            "model": self.model,
            "state": self.state,
            "detail": self.detail,
            "catalogue_size": self.catalogue_size,
        }


def matches(model: str, catalogue: List[str]) -> bool:
    """Return True when a model appears in a provider's catalogue.

    Exact match wins, but many catalogues namespace models
    (``meta-llama/llama-3.1-8b-instruct`` vs ``llama-3.1-8b-instruct``), so a
    suffix match is accepted as evidence the model is still served.

    Args:
        model: The configured model identifier.
        catalogue: Model identifiers the provider reported.

    Returns:
        True when the model appears to be served.
    """
    if model in catalogue:
        return True
    bare = model.split("/")[-1]
    return any(entry == bare or entry.split("/")[-1] == bare for entry in catalogue)


async def check_connector(connector: Any) -> ModelReport:
    """Check one connector's configured model.

    Args:
        connector: The connector to check.

    Returns:
        A ModelReport describing the outcome.
    """
    name = connector.NAME
    if not connector.is_configured():
        return ModelReport(name, connector.model_for(), SKIPPED, "no credential configured")

    if not getattr(connector, "PUBLISHES_CATALOGUE", True):
        healthy = await connector.health_check()
        detail = connector.health_detail() if healthy else connector.last_error
        return ModelReport(
            name,
            connector.model_for(),
            UNKNOWN if healthy else DEAD,
            "no catalogue published; " + detail,
        )

    try:
        catalogue = await connector.list_models()
    except ConnectorError as exc:
        return ModelReport(name, connector.model_for(), UNKNOWN, str(exc))

    model = connector.model_for()
    if matches(model, catalogue):
        return ModelReport(name, model, OK, "model is served", len(catalogue))

    if not catalogue:
        return ModelReport(name, model, DEAD, "provider serves no models at all")

    close = [entry for entry in catalogue if entry.split("/")[-1][:6] == model.split("/")[-1][:6]]
    hint = f" closest available: {', '.join(close[:3])}" if close else ""
    return ModelReport(
        name,
        model,
        DEAD,
        f"model not in catalogue of {len(catalogue)}.{hint}",
        len(catalogue),
    )


async def check_all() -> List[ModelReport]:
    """Check every connector on the bridge.

    Returns:
        One report per connector, in fallback order.
    """
    bridge = CloudBridge()
    reports: List[ModelReport] = []
    for name in bridge.order:
        reports.append(await check_connector(bridge.connectors[name]))
    return reports


def print_report(reports: List[ModelReport]) -> None:
    """Print a human-readable table of model reports.

    Args:
        reports: The reports to display.
    """
    for report in reports:
        print(f"[{report.state.upper():7}] {report.provider:<13} {report.model}")
        print(f"{'':9}{report.detail}")


def main() -> int:
    """Run the check and return an exit code.

    Returns:
        1 when any configured provider's model is dead, otherwise 0. Unknown
        results never fail the check, because an unreachable catalogue is not
        proof of a bad model.
    """
    parser = argparse.ArgumentParser(description="Check ZUZZ cloud models are still served")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args()

    reports = asyncio.run(check_all())

    if args.json:
        import json

        print(json.dumps([report.to_dict() for report in reports], indent=2))
    else:
        print_report(reports)

    dead = [report for report in reports if report.state == DEAD]
    return 1 if dead else 0


if __name__ == "__main__":
    raise SystemExit(main())
