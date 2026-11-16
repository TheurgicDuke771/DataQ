"""The frontend's re-baseline gate lists the kinds the backend's route accepts.

`frontend/.../expectationCatalog.ts` keeps its own `STATEFUL_MONITOR_KINDS`; a kind missing
there has no Re-baseline action in the UI, and an extra one offers an action the route refuses.
"""

import re
from pathlib import Path

from backend.app.datasources.monitors import STATEFUL_MONITOR_KINDS

_CATALOG = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "src"
    / "components"
    / "checks"
    / "expectationCatalog.ts"
)


def test_the_frontend_set_equals_the_backends() -> None:
    source = _CATALOG.read_text(encoding="utf-8")
    block = re.search(
        r"export const STATEFUL_MONITOR_KINDS\b[^=]*=\s*new Set\(\[(.*?)\]\)", source, re.S
    )
    assert block is not None, "STATEFUL_MONITOR_KINDS not found in expectationCatalog.ts"
    frontend = set(re.findall(r"'([a-z_]+)'", block.group(1)))
    assert frontend == set(STATEFUL_MONITOR_KINDS)
