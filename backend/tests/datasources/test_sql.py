"""Tests for the shared SQL-datasource primitives (#428) — the single-source
identifier allowlist and the deduped monitor-over-engine execution loop."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from backend.app.datasources.base import MonitorSpec
from backend.app.datasources.sql import is_sql_identifier, run_monitors_over_engine

# ───────────────────────── identifier allowlist ─────────────────────────


@pytest.mark.parametrize("name", ["orders", "_private", "COL$1", "a1_b2", "T"])
def test_valid_identifiers_pass(name: str) -> None:
    assert is_sql_identifier(name)


@pytest.mark.parametrize(
    "name",
    ["", "1abc", "a b", 'a"b', "a;drop table t", "a.b", "col-name", None, 42, ["orders"]],
)
def test_invalid_identifiers_and_non_strings_fail(name: object) -> None:
    assert not is_sql_identifier(name)


def test_allowlist_is_shared_by_monitors_and_profiler() -> None:
    # The point of #428: one source of truth. Both consumers must reject through
    # the same decision — pin that they actually route through it.
    from backend.app.datasources.monitors import MonitorConfigError, _ident
    from backend.app.services.profile_service import (
        ProfileIdentifierInvalidError,
        validate_identifier,
    )

    with pytest.raises(MonitorConfigError):
        _ident("a;drop", what="column")
    with pytest.raises(ProfileIdentifierInvalidError):
        validate_identifier("a;drop")
    assert _ident("fine_col", what="column") == "fine_col"
    assert validate_identifier("fine_col") == "fine_col"


# ───────────────────────── run_monitors_over_engine ─────────────────────────


@pytest.fixture
def engine():  # type: ignore[no-untyped-def]
    eng = create_engine("sqlite://")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE orders (id INTEGER)"))
        conn.execute(text("INSERT INTO orders (id) VALUES (1), (2), (3)"))
    yield eng
    eng.dispose()


def test_volume_monitor_runs_over_one_engine_connection(engine) -> None:  # type: ignore[no-untyped-def]
    outcomes = run_monitors_over_engine(
        engine,
        table="orders",
        schema=None,
        catalog=None,
        monitors=[MonitorSpec(kind="volume", config={"min_rows": 1, "max_rows": 10})],
    )
    assert len(outcomes) == 1
    assert outcomes[0].success
    assert outcomes[0].metric_value == 0.0
    assert outcomes[0].observed_value == {"row_count": 3, "deviation_pct": 0.0}


def test_bad_monitor_errors_only_itself(engine) -> None:  # type: ignore[no-untyped-def]
    # First monitor queries a nonexistent column (SQL error at fetch time); the
    # sibling volume monitor on the same connection must still produce a result.
    outcomes = run_monitors_over_engine(
        engine,
        table="orders",
        schema=None,
        catalog=None,
        monitors=[
            MonitorSpec(kind="freshness", config={"column": "no_such_col"}),
            MonitorSpec(kind="volume", config={"min_rows": 1, "max_rows": 10}),
        ],
    )
    assert [o.errored for o in outcomes] == [True, False]
    assert outcomes[1].success


def test_connection_failure_propagates(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # A datasource-establishment failure must fail the whole run (raise), never
    # degrade into per-monitor errors — the connect happens before the loop.
    eng = create_engine(f"sqlite:///{tmp_path}/no_such_dir/db.sqlite")
    with pytest.raises(Exception, match="unable to open database file"):
        run_monitors_over_engine(
            eng,
            table="orders",
            schema=None,
            catalog=None,
            monitors=[MonitorSpec(kind="volume", config={"min_rows": 0, "max_rows": 1})],
        )
