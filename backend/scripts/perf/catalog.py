"""The case catalog — the one place that imports every case module.

Kept separate from `harness` so the import graph stays one-directional: case
modules import the harness, the catalog imports both, and nothing imports back.
"""

from __future__ import annotations

from collections.abc import Iterable

from backend.scripts.perf import cases_db, cases_flatfile, cases_warehouse
from backend.scripts.perf.harness import Case, registered

#: Importing a case module is what registers its cases; naming them here keeps the
#: imports load-bearing rather than incidental.
CASE_MODULES = (cases_db, cases_flatfile, cases_warehouse)


def registry() -> dict[str, Case]:
    """Every registered case. Importing this module is what registers them, so a
    partial registry (one case module imported directly) cannot be observed.
    """
    return registered()


def select(
    *,
    families: Iterable[str] | None = None,
    tags: Iterable[str] | None = None,
    ids: Iterable[str] | None = None,
) -> list[Case]:
    cases = list(registry().values())
    if ids:
        wanted = set(ids)
        cases = [c for c in cases if c.id in wanted]
    if families:
        fams = set(families)
        cases = [c for c in cases if c.family in fams]
    if tags:
        tagset = set(tags)
        cases = [c for c in cases if tagset & set(c.tags)]
    return cases
