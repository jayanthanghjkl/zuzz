"""Face API — browser routes.

The HTTP surface of :mod:`body.legs`, assembled from three modules and nothing else.
This file holds no endpoints, no policy and no helpers: every route, every docstring and
every decision belongs to one of the three below.

* :mod:`face.api.routes_browser_nav` — where the browser is and where it goes:
  ``open``, ``close``, ``navigate``, and the four tab routes.
* :mod:`face.api.routes_browser_interact` — what happens on the page: ``click``,
  ``fill-form``, ``extract``, ``search``.
* :mod:`face.api.routes_browser_manage` — what it costs: ``session-status``,
  ``screenshot``, ``download``, ``downloads``, and the two allowlist edits.

The split is by user intent rather than by line count, so a reader looking for "why was
this clicked" and one looking for "why was this downloaded" are not reading the same
file. The permission flow, the confirmation prompts, the refusal-to-status mapping and
the two decisions a download needs all live in :mod:`face.api.browser_support`; the
reasoning behind them is in the :mod:`body.legs` modules.

The three gauges — ``tabs``, ``session-status`` and ``downloads`` — are not gated,
because they report ZUZZ's own state so a user can decide whether to grant a permission.
A gauge behind a confirmation is a gauge nobody looked at. The allowlist edits are the
opposite case: they record the *user's* answer to a question ZUZZ asked, so they are
audited rather than gated, and the user can take it back with
``/api/browser/forget-domain``.
"""

from __future__ import annotations

from fastapi import APIRouter

from face.api import routes_browser_interact, routes_browser_manage, routes_browser_nav

#: One router, assembled from three. ``server.create_app`` includes this object, so
#: the composition stays a single line there no matter how the routes are grouped here.
router = APIRouter()

router.include_router(routes_browser_nav.router)
router.include_router(routes_browser_interact.router)
router.include_router(routes_browser_manage.router)