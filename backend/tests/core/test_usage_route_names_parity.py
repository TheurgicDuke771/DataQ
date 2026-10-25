"""The frontend's page names and the backend's closed `ROUTE_NAMES` must be the same set.

The browser posts `page_viewed` with a name from `frontend/src/api/telemetry.ts`; the server drops
any value outside `ROUTE_NAMES`. A page added on one side only was silently never counted — which
is how `run_compare` went unrecorded.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.app.core.usage_telemetry import ROUTE_NAMES

_TELEMETRY_TS = Path(__file__).resolve().parents[3] / "frontend" / "src" / "api" / "telemetry.ts"
# The fallbacks the server keeps for pages the browser cannot name.
_SERVER_ONLY = {"not_found", "other"}


def test_every_frontend_page_name_is_accepted_by_the_server() -> None:
    names = set(re.findall(r"\['/[^']*', '([a-z_]+)'\]", _TELEMETRY_TS.read_text()))
    assert names, "the route table in telemetry.ts was not found"
    assert names - set(ROUTE_NAMES) == set()
    assert set(ROUTE_NAMES) - _SERVER_ONLY - names == set()
