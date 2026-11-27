"""Each retention sweep, as the code builds it, reaches the partial index written for it (#2751).

The statement is compiled by the session's own dialect and sent through the driver, so the
planner sees the text the app sends. A hand-written copy of the query would prove only that the
copy matches the index.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import null, text

from backend.app.services import run_service
from backend.tests.support.driver_guard import assert_client_side_binding
from backend.tests.support.index_plans import drop_rival_indexes

_CUTOFF = datetime(2026, 1, 1, tzinfo=UTC)

_SWEEPS: list[tuple[str, Callable[[], list[Any]], dict[str, Any]]] = [
    (
        "ix_results_unpurged_created",
        run_service.sample_failures_guard,
        {"sample_failures": null(), "sample_failures_purged_at": _CUTOFF},
    ),
    ("ix_results_unpurged_observed", run_service.observed_list_guard, {"observed_value": null()}),
    (
        "ix_results_unpurged_unparsed",
        run_service.observed_unparsed_guard,
        {"observed_value": null()},
    ),
]


@pytest.mark.parametrize(("index", "guard", "values"), _SWEEPS, ids=[s[0] for s in _SWEEPS])
def test_the_sweep_the_code_builds_reaches_its_index(
    db_session: Any, index: str, guard: Callable[[], list[Any]], values: dict[str, Any]
) -> None:
    connection = db_session.connection()
    assert_client_side_binding(connection)
    drop_rival_indexes(connection, index)
    connection.execute(text("SET LOCAL enable_seqscan = off"))
    statement = run_service.purge_statement(
        cutoff=_CUTOFF, extra_where=guard(), values=values, chunk_size=500
    )
    # `render_postcompile` writes the `sql_constant` literals in, as execution does.
    compiled = statement.compile(
        dialect=connection.dialect, compile_kwargs={"render_postcompile": True}
    )
    plan = "\n".join(
        row[0] for row in connection.exec_driver_sql(f"EXPLAIN {compiled}", compiled.params)
    )
    assert index in plan, f"the sweep misses {index}:\n{plan}"
