"""Tests for the shared SQL-identifier allowlist (#428) and the deduped
monitor-over-engine execution loop it enabled (now in `monitors.py`)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

from backend.app.datasources import monitors as monitors_module
from backend.app.datasources.base import MonitorSpec
from backend.app.datasources.monitors import run_monitors_over_engine
from backend.app.datasources.sql import is_sql_identifier
from backend.app.services import profile_service

# ───────────────────────── identifier allowlist ─────────────────────────


@pytest.mark.parametrize("name", ["orders", "_private", "COL$1", "a1_b2", "T"])
def test_valid_identifiers_pass(name: str) -> None:
    assert is_sql_identifier(name)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "1abc",
        "a b",
        'a"b',
        "a;drop table t",
        "a.b",
        "col-name",
        "col\n",  # fullmatch: the `$`-anchor loophole (one trailing \n) is closed
        None,
        42,
        ["orders"],
    ],
)
def test_invalid_identifiers_and_non_strings_fail(name: object) -> None:
    assert not is_sql_identifier(name)


def test_monitors_ident_routes_through_shared_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pin the ROUTING, not just behavioral agreement (#428's whole point): with
    # the shared predicate forced to False, the consumer must reject a name it
    # would otherwise accept — proving it has no private regex copy.
    from backend.app.datasources.monitors import MonitorConfigError, _ident

    assert _ident("fine_col", what="column") == "fine_col"
    monkeypatch.setattr(monitors_module, "is_sql_identifier", lambda name: False)
    with pytest.raises(MonitorConfigError):
        _ident("fine_col", what="column")


def test_profiler_validate_identifier_routes_through_shared_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.services.profile_service import (
        ProfileIdentifierInvalidError,
        validate_identifier,
    )

    assert validate_identifier("fine_col") == "fine_col"
    monkeypatch.setattr(profile_service, "is_sql_identifier", lambda name: False)
    with pytest.raises(ProfileIdentifierInvalidError):
        validate_identifier("fine_col")


# ───────────────────────── folding_identifier (#476) ─────────────────────────


@pytest.mark.parametrize(
    ("name", "quoted"),
    [
        ("order_ts", False),  # lower-case → bare, so the warehouse folds it
        ("load_ts_2", False),
        ("copy", False),  # reserved in SQLAlchemy's dialect, NOT in Snowflake
        ("select", False),  # genuinely reserved: broken either way, but consistent
        ("Amount", True),  # mixed → quoted; the #476 fix
        ("ORDER_TS", True),  # upper → quoted (resolves identically after folding)
        ("A", True),
    ],
)
def test_folding_identifier_decides_on_case_alone(name: str, quoted: bool) -> None:
    """The quote decision must depend on CASE ONLY — never on the dialect's
    reserved-word set, which is not the set the warehouse reserves (SQLAlchemy
    reserves `copy`, Snowflake doesn't). Delegating to the compiler's default
    would silently unresolve a column stored COPY."""
    from backend.app.datasources.sql import folding_identifier

    assert folding_identifier(name).quote is quoted


def test_folding_identifier_preserves_the_name_itself() -> None:
    """It changes the quoting flag, never the spelling — a fold applied to the
    TEXT would resolve a different object."""
    from backend.app.datasources.sql import folding_identifier

    assert str(folding_identifier("Amount")) == "Amount"
    assert str(folding_identifier("order_ts")) == "order_ts"


# ───────────────────────── run_monitors_over_engine ─────────────────────────


def _seeded_engine(tmp_path: Path) -> Engine:
    # A file-backed DB (not sqlite:// in-memory, whose SingletonThreadPool hands
    # every connect() the same DBAPI connection and would mask an extra open).
    eng = create_engine(f"sqlite:///{tmp_path}/monitors.sqlite")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE orders (id INTEGER)"))
        conn.execute(text("INSERT INTO orders (id) VALUES (1), (2), (3)"))
    eng.dispose()  # drop the seeding connection so the test counts from zero
    return eng


def test_monitors_share_exactly_one_connection(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # The helper's contract (and #427's cost story): ONE DBAPI connect per call,
    # however many monitors run. Counted via the pool's connect event — outcomes
    # alone can't detect a regression to connect-per-monitor.
    eng = _seeded_engine(tmp_path)
    connects: list[object] = []
    event.listen(eng, "connect", lambda dbapi_conn, rec: connects.append(dbapi_conn))
    try:
        outcomes = run_monitors_over_engine(
            eng,
            table="orders",
            schema=None,
            catalog=None,
            monitors=[
                MonitorSpec(kind="volume", config={"min_rows": 1, "max_rows": 10}),
                MonitorSpec(kind="volume", config={"min_rows": 5, "max_rows": 10}),
            ],
        )
    finally:
        eng.dispose()
    assert len(connects) == 1
    assert len(outcomes) == 2
    assert outcomes[0].success
    assert outcomes[0].metric_value == 0.0
    assert outcomes[0].observed_value == {"row_count": 3, "deviation_pct": 0.0}
    assert not outcomes[1].success  # 3 rows < floor 5 → volume deviation


def test_bad_monitor_errors_only_itself(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # First monitor queries a nonexistent column (SQL error at fetch time); the
    # sibling volume monitor on the same connection must still produce a result.
    eng = _seeded_engine(tmp_path)
    try:
        outcomes = run_monitors_over_engine(
            eng,
            table="orders",
            schema=None,
            catalog=None,
            monitors=[
                MonitorSpec(kind="freshness", config={"column": "no_such_col"}),
                MonitorSpec(kind="volume", config={"min_rows": 1, "max_rows": 10}),
            ],
        )
    finally:
        eng.dispose()
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


# ───────────────────────── LazyEngine (#427) ─────────────────────────


def test_lazy_engine_builds_once_and_rebuilds_after_close() -> None:
    from backend.app.datasources.sql import LazyEngine

    built: list[object] = []

    class _FakeEngine:
        def __init__(self) -> None:
            self.disposed = 0

        def dispose(self) -> None:
            self.disposed += 1

    def factory() -> _FakeEngine:
        eng = _FakeEngine()
        built.append(eng)
        return eng

    lazy = LazyEngine(factory)
    first = lazy.get()
    assert lazy.get() is first  # one build, shared
    assert len(built) == 1

    lazy.close()
    lazy.close()  # idempotent — dispose exactly once
    assert first.disposed == 1

    second = lazy.get()  # a closed holder lazily rebuilds
    assert second is not first
    assert len(built) == 2
    lazy.close()


def test_lazy_engine_close_before_use_never_builds() -> None:
    from backend.app.datasources.sql import LazyEngine

    built: list[object] = []

    def factory() -> object:
        built.append(object())
        return built[-1]

    lazy = LazyEngine(factory)
    lazy.close()
    assert built == []
