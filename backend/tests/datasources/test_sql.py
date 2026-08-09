"""Tests for the shared SQL-identifier allowlist (#428) and the deduped
monitor-over-engine execution loop it enabled (now in `monitors.py`)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import StatementError

from backend.app.datasources import monitors as monitors_module
from backend.app.datasources.base import MonitorSpec
from backend.app.datasources.monitors import run_monitors_over_engine
from backend.app.datasources.sql import is_sql_identifier, strip_statement_echo
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


def test_uc_custom_sql_target_routes_through_shared_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The third consumer (#1179): the UC custom-SQL path interpolates
    table/schema/catalog into GX's connection URL, so it validates them here.

    Pinned as ROUTING for the same reason as the two above — a parametrized
    battery of hostile names is only behavioral agreement, and would stay green
    if this consumer re-grew a private regex copy, which is the exact drift #428
    exists to prevent.
    """
    from backend.app.datasources import unity_catalog as unity_catalog_module
    from backend.app.datasources.unity_catalog import UnityCatalogCheckRunner, UnityCatalogConfig

    runner = UnityCatalogCheckRunner(
        config=UnityCatalogConfig.model_validate(
            {"workspace_url": "https://adb-1.azuredatabricks.net", "warehouse_id": "w"}
        ),
        token="t",
        catalog="main",
    )
    assert runner._sql_target_problem(table="fine_table", schema="gold") is None
    monkeypatch.setattr(unity_catalog_module, "is_sql_identifier", lambda name: False)
    assert runner._sql_target_problem(table="fine_table", schema="gold") is not None


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


# ───────────────── statement-echo strip (#1203) ─────────────────


def _statement_error(*, message: str, statement: str, params: dict[str, object]) -> StatementError:
    """A real SQLAlchemy `StatementError`, so the tests read its ACTUAL rendering.

    Hand-writing the expected string would test our idea of the format rather than
    SQLAlchemy's — precisely the fixture-encodes-our-model trap that hid #953. Every
    SQL datasource reaches DataQ through this wrapper: the Snowflake and Databricks
    dialects both raise driver errors wrapped in it, which is why one strip covers
    both without a per-datasource branch.
    """
    return StatementError(message, statement, params, Exception("orig"))


def test_strip_statement_echo_removes_the_statement_and_its_bound_parameters() -> None:
    exc = _statement_error(
        message=(
            "(snowflake.connector.errors.ProgrammingError) 100038 (22018): "
            "Numeric value 'ORD-9' is not recognized"
        ),
        statement="SELECT * FROM RETAIL.ORDERS WHERE CUSTOMER_REF = %(ref)s",
        params={"ref": "alice@example.com"},
    )
    rendered = str(exc)
    # Guard the premise: SQLAlchemy really does echo both, so a green assertion
    # below cannot be an artefact of nothing having been there.
    assert "[SQL:" in rendered and "[parameters:" in rendered
    assert "alice@example.com" in rendered

    stripped = strip_statement_echo(rendered)

    assert stripped is not None
    assert "[SQL:" not in stripped
    assert "[parameters:" not in stripped
    assert "alice@example.com" not in stripped
    assert "RETAIL.ORDERS" not in stripped
    # …and the driver's own diagnostic, the reason we don't blanket-classify, stays.
    assert stripped == (
        "(snowflake.connector.errors.ProgrammingError) 100038 (22018): "
        "Numeric value 'ORD-9' is not recognized"
    )


def test_strip_statement_echo_keeps_a_multi_line_driver_message() -> None:
    # `_message()` is joined into the same string as the echo, so the cut must be at
    # the marker, not "the first line" — a driver that wraps its message would
    # otherwise lose half its diagnostic.
    exc = _statement_error(
        message="(databricks.sql.exc.ServerOperationError) [CAST_INVALID_INPUT]\ncannot cast 'x'",
        statement="SELECT 1",
        params={"p": "secret-cell"},
    )
    stripped = strip_statement_echo(str(exc))
    assert (
        stripped
        == "(databricks.sql.exc.ServerOperationError) [CAST_INVALID_INPUT]\ncannot cast 'x'"
    )
    assert "secret-cell" not in stripped


def test_strip_statement_echo_is_idempotent_and_passes_other_messages_through() -> None:
    exc = _statement_error(message="(x.Error) boom", statement="SELECT 1", params={"p": "cell"})
    once = strip_statement_echo(str(exc))
    assert strip_statement_echo(once) == once
    # A non-SQL runner's message, a DataQ-authored one, and the empty cases are
    # untouched — the strip must never edit a message it does not recognise.
    assert strip_statement_echo("unknown freshness column 'nope'") == (
        "unknown freshness column 'nope'"
    )
    assert strip_statement_echo("[SQL: not a marker without the newline]") == (
        "[SQL: not a marker without the newline]"
    )
    assert strip_statement_echo(None) is None
    assert strip_statement_echo("") == ""
