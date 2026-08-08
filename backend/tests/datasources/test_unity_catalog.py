"""Unity Catalog connection adapter tests — config validation + the SELECT 1 probe.

No live Databricks: ``databricks.sql.connect`` is monkeypatched so the
warehouse probe runs against a fake. The adapter is DB-free, so these are pure
unit tests (no db_session).
"""

from typing import Any

import pytest
from databricks import sql
from pydantic import ValidationError

from backend.app.datasources.unity_catalog import (
    UnityCatalogConfig,
    UnityCatalogConnectionAdapter,
)

_UC_CONFIG = {
    "workspace_url": "https://adb-1234.5.azuredatabricks.net",
    "warehouse_id": "abc123def456",
}


# ───────────────────────── validate_config ─────────────────────────


def test_validate_config_accepts_config() -> None:
    cfg = UnityCatalogConnectionAdapter().validate_config(dict(_UC_CONFIG))
    assert isinstance(cfg, UnityCatalogConfig)
    assert cfg.warehouse_id == "abc123def456"


def test_config_derives_hostname_and_http_path() -> None:
    cfg = UnityCatalogConfig.model_validate(_UC_CONFIG)
    assert cfg.server_hostname == "adb-1234.5.azuredatabricks.net"
    assert cfg.http_path == "/sql/1.0/warehouses/abc123def456"


def test_validate_config_rejects_non_http_workspace_url() -> None:
    with pytest.raises(ValidationError, match="http"):
        UnityCatalogConnectionAdapter().validate_config(
            {"workspace_url": "adb-1234.azuredatabricks.net", "warehouse_id": "w"}
        )


def test_validate_config_strips_trailing_slash() -> None:
    cfg = UnityCatalogConnectionAdapter().validate_config(
        {"workspace_url": "https://adb-1.azuredatabricks.net/", "warehouse_id": "w"}
    )
    assert cfg.workspace_url == "https://adb-1.azuredatabricks.net"


def test_validate_config_rejects_missing_warehouse_id() -> None:
    with pytest.raises(ValidationError):
        UnityCatalogConnectionAdapter().validate_config(
            {"workspace_url": "https://adb-1.azuredatabricks.net"}
        )


def test_validate_config_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        UnityCatalogConnectionAdapter().validate_config({**_UC_CONFIG, "catalog": "main"})


# ───────────────────────── test() connectivity ─────────────────────


def test_test_runs_select_1_with_pat(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    class _FakeCursor:
        def execute(self, query: str) -> None:
            calls["query"] = query

        def fetchone(self) -> tuple[int]:
            calls["fetched"] = True
            return (1,)

        def close(self) -> None:
            calls["cursor_closed"] = True

    class _FakeConnection:
        def cursor(self) -> _FakeCursor:
            return _FakeCursor()

        def close(self) -> None:
            calls["conn_closed"] = True

    def fake_connect(**kwargs: Any) -> _FakeConnection:
        calls["connect_kwargs"] = kwargs
        return _FakeConnection()

    monkeypatch.setattr(sql, "connect", fake_connect)
    UnityCatalogConnectionAdapter().test(dict(_UC_CONFIG), "dapi-pat-token")  # no raise

    assert calls["connect_kwargs"]["server_hostname"] == "adb-1234.5.azuredatabricks.net"
    assert calls["connect_kwargs"]["http_path"] == "/sql/1.0/warehouses/abc123def456"
    assert calls["connect_kwargs"]["access_token"] == "dapi-pat-token"
    assert calls["query"] == "SELECT 1"
    assert calls["fetched"] is True
    assert calls["cursor_closed"] is True
    assert calls["conn_closed"] is True


def test_test_raises_and_closes_when_query_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: dict[str, bool] = {}

    class _FakeCursor:
        def execute(self, query: str) -> None:
            raise RuntimeError("warehouse stopped")

        def close(self) -> None:
            closed["cursor"] = True

    class _FakeConnection:
        def cursor(self) -> _FakeCursor:
            return _FakeCursor()

        def close(self) -> None:
            closed["conn"] = True

    monkeypatch.setattr(sql, "connect", lambda **kw: _FakeConnection())
    with pytest.raises(RuntimeError, match="warehouse stopped"):
        UnityCatalogConnectionAdapter().test(dict(_UC_CONFIG), "dapi-pat-token")
    assert closed["cursor"] is True  # finally-closes the cursor
    assert closed["conn"] is True  # …and the connection


# ───────────────────────── GX runner (build_databricks_url, runner) ─

import pandas as pd  # noqa: E402

from backend.app.datasources.base import CheckSpec  # noqa: E402
from backend.app.datasources.unity_catalog import (  # noqa: E402
    UnityCatalogCheckRunner,
    build_databricks_url,
    build_unity_catalog_runner,
)


class _FakeStore:
    def get(self, name: str) -> str:
        return "pat-token"

    def set(self, name: str, value: str) -> None:  # read-only test double
        raise NotImplementedError

    def delete(self, name: str) -> None:
        raise NotImplementedError


def test_build_databricks_url_encodes_parts() -> None:
    cfg = UnityCatalogConfig.model_validate(_UC_CONFIG)
    url = build_databricks_url(cfg, "a b/c")
    assert url.startswith("databricks://token:a+b%2Fc@adb-1234.5.azuredatabricks.net")
    # http_path is URL-encoded; no catalog pinned by default
    assert "http_path=%2Fsql%2F1.0%2Fwarehouses%2Fabc123def456" in url
    assert "catalog=" not in url


def test_build_databricks_url_pins_catalog() -> None:
    cfg = UnityCatalogConfig.model_validate(_UC_CONFIG)
    assert "&catalog=main" in build_databricks_url(cfg, "t", catalog="main")


def test_build_databricks_url_pins_schema() -> None:
    """GX's `DatabricksDsn` refuses a URL without `schema` (#1179), so the SQL
    batch needs it on the URL as well as on the asset."""
    cfg = UnityCatalogConfig.model_validate(_UC_CONFIG)
    url = build_databricks_url(cfg, "t", catalog="main", schema="go ld")
    assert "&catalog=main" in url
    assert "&schema=go+ld" in url  # URL-encoded like every other part
    # Unchanged default: callers that qualify the namespace themselves get none.
    assert "schema=" not in build_databricks_url(cfg, "t", catalog="main")


def test_build_unity_catalog_runner_resolves_pat() -> None:
    runner = build_unity_catalog_runner(
        config=dict(_UC_CONFIG), secret_ref="kv-ref", secret_store=_FakeStore(), catalog="main"
    )
    assert isinstance(runner, UnityCatalogCheckRunner)


def test_build_unity_catalog_runner_requires_secret_ref() -> None:
    with pytest.raises(ValueError, match="secret_ref"):
        build_unity_catalog_runner(
            config=dict(_UC_CONFIG), secret_ref=None, secret_store=_FakeStore(), catalog="main"
        )


def _runner_over(df: pd.DataFrame, monkeypatch: pytest.MonkeyPatch) -> UnityCatalogCheckRunner:
    runner = UnityCatalogCheckRunner(
        config=UnityCatalogConfig.model_validate(_UC_CONFIG), token="t", catalog="main"
    )
    # Replace the live reflect+read seam with a canned frame; GX still runs for real.
    monkeypatch.setattr(runner, "_read_table", lambda **kwargs: df)
    return runner


def test_run_checks_runs_gx_on_dataframe(monkeypatch: pytest.MonkeyPatch) -> None:
    df = pd.DataFrame({"id": [1, 2, None], "amt": [10, 20, 30]})
    runner = _runner_over(df, monkeypatch)
    outcome = runner.run_checks(
        table="orders",
        schema="sales",
        checks=[
            CheckSpec("expect_column_values_to_not_be_null", {"column": "id"}),
            CheckSpec("expect_table_row_count_to_be_between", {"min_value": 1, "max_value": 10}),
        ],
    )
    assert outcome.success is False
    by_type = {c.expectation_type: c for c in outcome.checks}
    assert by_type["expect_column_values_to_not_be_null"].success is False
    assert by_type["expect_table_row_count_to_be_between"].success is True
    assert by_type["expect_table_row_count_to_be_between"].observed_value == {"observed_value": 3}


def test_run_checks_all_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    df = pd.DataFrame({"id": [1, 2, 3]})
    runner = _runner_over(df, monkeypatch)
    outcome = runner.run_checks(
        table="t",
        schema="s",
        checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "id"})],
    )
    assert outcome.success is True
    assert outcome.checks[0].success is True


def test_databricks_sqlalchemy_dialect_is_installed() -> None:
    """Dependency contract (#535): `_read_table` does
    `create_engine('databricks://…')`, whose dialect lives in the separate
    `databricks-sqlalchemy` package since databricks-sql-connector 4.x —
    tests mock the runner seam, so without this check a missing dialect only
    surfaces as a failed run in production. No network: dialect load only.
    """
    from sqlalchemy import create_engine

    engine = create_engine(
        "databricks://token:x@example.cloud.databricks.com"
        "?http_path=/sql/1.0/warehouses/x&catalog=c"
    )
    assert engine.dialect.name == "databricks"


# ───────────────────────── shared engine lifecycle (#427) ─────────────────────────


def test_gx_read_and_monitors_share_one_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A mixed suite (expectations + monitors) must pay ONE warehouse session:
    `_read_table` (the GX path) and `run_monitors` share the runner's lazy
    engine (#427). Pinned by counting `create_engine` constructions."""
    import sqlalchemy

    from backend.app.datasources.base import MonitorSpec

    real_create_engine = sqlalchemy.create_engine
    db_url = f"sqlite:///{tmp_path}/uc.sqlite"
    seed = real_create_engine(db_url)
    with seed.begin() as conn:
        conn.execute(sqlalchemy.text("CREATE TABLE orders (id INTEGER)"))
        conn.execute(sqlalchemy.text("INSERT INTO orders (id) VALUES (1), (2)"))
    seed.dispose()

    created: list[str] = []

    def _fake_create_engine(url: str, **_kwargs: Any) -> Any:
        created.append(str(url))
        return real_create_engine(db_url)

    monkeypatch.setattr(sqlalchemy, "create_engine", _fake_create_engine)
    runner = UnityCatalogCheckRunner(
        config=UnityCatalogConfig.model_validate(_UC_CONFIG),
        token="tok",
        catalog="main",
    )
    df = runner._read_table(table="orders", schema=None)
    assert len(df) == 2
    # run_monitors reuses the same engine; the 3-part `main.x.orders` name errors
    # PER-MONITOR on sqlite (which is fine — the connection itself succeeded).
    outcomes = runner.run_monitors(
        table="orders",
        schema="x",
        monitors=[MonitorSpec(kind="volume", config={"min_rows": 1, "max_rows": 10})],
    )
    assert len(outcomes) == 1
    assert len(created) == 1  # ONE engine across the GX read AND the monitor path
    assert created[0].endswith("uc.sqlite") or created[0].startswith("databricks")  # url recorded
    runner.close()
    runner.close()  # idempotent
    # After close a later use lazily rebuilds — never a bricked runner.
    runner._read_table(table="orders", schema=None)
    assert len(created) == 2
    runner.close()


# ───────────────────── custom SQL on Unity Catalog (#1179) ─────────────────────
#
# The bug: `UnexpectedRowsExpectation`'s metrics (`unexpected_rows_query.table` /
# `.row_count`) have a SqlAlchemy provider and NO pandas one, so on this runner's
# DataFrame batch GX raised "No provider found for unexpected_rows_query.table
# using PandasExecutionEngine" — custom SQL had never once worked on UC.
#
# The fix routes those checks to a GX **SQL** batch. These tests substitute
# **sqlite for the warehouse** at `_sql_batch_definition` — the live seam — and
# leave everything else real: GX genuinely executes the query through a
# SqlAlchemy execution engine and `gx_runner` genuinely maps the result. What
# sqlite cannot prove is the Databricks half (the DSN GX accepts, the dialect,
# the driver's own error shape), which is why #1179 also carries a live run
# against the real warehouse — the #953 rule.


def _sqlite_batch_seam(
    runner: UnityCatalogCheckRunner,
    tmp_path: Any,
    rows: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    """Point the runner's SQL-batch seam at a real sqlite `feedback(rating)` table.

    Returns a list that records each seam call's arguments, so a test can assert
    the seam was (or was not) reached and with what target.

    It also **arms the DataFrame seam to fail**, so a custom-SQL check that is
    misrouted back to the pandas batch aborts loudly. That is load-bearing, not
    belt-and-braces: `_read_table` builds a real `databricks://` engine, so the
    unfixed routing makes these tests HANG on a DNS lookup for the fake
    workspace host instead of failing — a regression this suite could then only
    report as a CI timeout. A test that needs the frame (the mixed-suite case)
    overrides it afterwards.
    """
    import sqlite3

    path = tmp_path / "uc.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE feedback (rating INTEGER)")
    conn.executemany("INSERT INTO feedback VALUES (?)", [(r,) for r in rows])
    conn.commit()
    conn.close()

    calls: list[dict[str, Any]] = []

    def _seam(context: Any, *, table: str, schema: str) -> tuple[Any, Any]:
        calls.append({"table": table, "schema": schema})
        datasource = context.data_sources.add_sqlite(
            name="uc-sql", connection_string=f"sqlite:///{path}"
        )
        asset = datasource.add_table_asset(name="feedback", table_name="feedback")
        return datasource, asset.add_batch_definition_whole_table(name="whole_table")

    monkeypatch.setattr(runner, "_sql_batch_definition", _seam)
    _forbid_dataframe_read(runner, monkeypatch)
    return calls


def _forbid_dataframe_read(
    runner: UnityCatalogCheckRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Make the DataFrame seam fail loudly.

    Every custom-SQL test arms this, and it is load-bearing rather than
    belt-and-braces: `_read_table` builds a real `databricks://` engine, so a
    custom-SQL check misrouted back to the pandas batch does not fail — it HANGS
    on a DNS lookup for the fake workspace host. Verified against the pre-fix
    routing: without this the suite reports a CI timeout instead of a defect.
    """

    def _must_not_read(**_kwargs: Any) -> Any:
        raise AssertionError("custom SQL must not trigger the full-table DataFrame read")

    monkeypatch.setattr(runner, "_read_table", _must_not_read)


def _uc_runner() -> UnityCatalogCheckRunner:
    return UnityCatalogCheckRunner(
        config=UnityCatalogConfig.model_validate(_UC_CONFIG), token="t", catalog="main"
    )


def _custom_sql(query: str) -> CheckSpec:
    return CheckSpec("unexpected_rows_expectation", {"unexpected_rows_query": query})


def test_custom_sql_passes_on_sql_batch_without_reading_a_dataframe(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression. A zero-row query passes — and the DataFrame read is never
    even attempted, which is what makes this the SQL batch and not the old one."""
    runner = _uc_runner()
    calls = _sqlite_batch_seam(runner, tmp_path, rows=[1, 4, 5], monkeypatch=monkeypatch)
    outcome = runner.run_checks(
        table="feedback",
        schema="gold",
        checks=[_custom_sql("SELECT * FROM {batch} WHERE rating NOT BETWEEN 1 AND 5")],
    )
    assert outcome.success is True
    check = outcome.checks[0]
    assert check.errored is False, check.error_message
    assert check.success is True
    assert check.observed_value == {"observed_value": 0}
    # The user's query round-trips onto the result row, as on the Snowflake path.
    assert check.expected_value == {
        "unexpected_rows_query": "SELECT * FROM {batch} WHERE rating NOT BETWEEN 1 AND 5"
    }
    assert calls == [{"table": "feedback", "schema": "gold"}]


def test_custom_sql_failing_query_reports_the_unexpected_row_count(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows returned = failures, and the count is the `observed_value` — the exact
    shape `test_custom_sql_gx.py` locks for the SQL path."""
    runner = _uc_runner()
    _sqlite_batch_seam(runner, tmp_path, rows=[1, 4, 5], monkeypatch=monkeypatch)
    outcome = runner.run_checks(
        table="feedback",
        schema="gold",
        checks=[_custom_sql("SELECT * FROM {batch} WHERE rating >= 4")],
    )
    assert outcome.success is False
    check = outcome.checks[0]
    assert check.errored is False, check.error_message
    assert check.success is False
    assert check.observed_value == {"observed_value": 2}


def test_custom_sql_query_error_is_an_operational_error_not_a_crash(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken query must land as `errored` (→ an `error` result, #122), never
    raise out of the runner and fail the whole run."""
    runner = _uc_runner()
    _sqlite_batch_seam(runner, tmp_path, rows=[1], monkeypatch=monkeypatch)
    outcome = runner.run_checks(
        table="feedback",
        schema="gold",
        checks=[_custom_sql("SELECT * FROM {batch} WHERE no_such_column = 1")],
    )
    check = outcome.checks[0]
    assert check.errored is True
    assert check.success is False
    assert "no_such_column" in (check.error_message or "")


def test_mixed_suite_merges_both_batches_in_submission_order(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Custom SQL interleaved with ordinary expectations: every outcome must come
    back at its submitted position, because `run_service` zips outcomes onto its
    own `checks` list — a shuffle would attribute results to the wrong check."""
    runner = _uc_runner()
    _sqlite_batch_seam(runner, tmp_path, rows=[1, 4, 5], monkeypatch=monkeypatch)
    monkeypatch.setattr(
        runner, "_read_table", lambda **_kw: pd.DataFrame({"id": [1, 2, None], "amt": [10, 20, 30]})
    )
    outcome = runner.run_checks(
        table="feedback",
        schema="gold",
        checks=[
            CheckSpec("expect_table_row_count_to_be_between", {"min_value": 1, "max_value": 10}),
            _custom_sql("SELECT * FROM {batch} WHERE rating >= 4"),
            CheckSpec("expect_column_values_to_not_be_null", {"column": "id"}),
        ],
    )
    assert [c.expectation_type for c in outcome.checks] == [
        "expect_table_row_count_to_be_between",
        "unexpected_rows_expectation",
        "expect_column_values_to_not_be_null",
    ]
    assert [c.success for c in outcome.checks] == [True, False, False]
    assert outcome.checks[1].observed_value == {"observed_value": 2}
    assert outcome.success is False


def test_suite_without_custom_sql_never_opens_a_sql_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    """No custom SQL → no second warehouse session. The DataFrame path is byte-
    for-byte what it always was."""
    runner = _uc_runner()

    def _must_not_build(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a suite with no custom SQL must not build a SQL batch")

    monkeypatch.setattr(runner, "_sql_batch_definition", _must_not_build)
    monkeypatch.setattr(runner, "_read_table", lambda **_kw: pd.DataFrame({"id": [1, 2, 3]}))
    outcome = runner.run_checks(
        table="t",
        schema="s",
        checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "id"})],
    )
    assert outcome.success is True


def test_custom_sql_without_a_schema_errors_only_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    """A UC suite target may legally omit the schema, but GX's DSN cannot — and an
    unqualified name would silently resolve against the session default, i.e. read
    the WRONG table rather than fail. So the custom-SQL check errors; its siblings
    on the DataFrame batch still evaluate and persist (#122)."""
    runner = _uc_runner()

    def _must_not_build(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("no schema → the DSN must never be built")

    monkeypatch.setattr(runner, "_sql_batch_definition", _must_not_build)
    monkeypatch.setattr(runner, "_read_table", lambda **_kw: pd.DataFrame({"id": [1, 2, 3]}))
    outcome = runner.run_checks(
        table="feedback",
        schema=None,
        checks=[
            _custom_sql("SELECT * FROM {batch} WHERE rating >= 4"),
            CheckSpec("expect_column_values_to_not_be_null", {"column": "id"}),
        ],
    )
    assert outcome.success is False
    custom, sibling = outcome.checks
    assert custom.errored is True
    assert "schema" in (custom.error_message or "")
    # The user's query still round-trips, so the failing result row stays diagnosable.
    assert custom.expected_value == {
        "unexpected_rows_query": "SELECT * FROM {batch} WHERE rating >= 4"
    }
    assert sibling.errored is False
    assert sibling.success is True


@pytest.mark.parametrize(
    "bad", ["a; DROP TABLE x", 'a"b', "a b", "a.b", "a-b", "1col", "", "a'b", "a\n"]
)
def test_custom_sql_refuses_a_non_identifier_target(
    bad: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The table/schema/catalog are interpolated into the DSN and the asset, so
    they go through the shared #428 allowlist FIRST — a hostile identifier must
    error the check, never reach the URL builder."""
    runner = _uc_runner()

    def _must_not_build(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a rejected identifier must never reach the DSN")

    monkeypatch.setattr(runner, "_sql_batch_definition", _must_not_build)
    _forbid_dataframe_read(runner, monkeypatch)
    outcome = runner.run_checks(
        table=bad, schema="gold", checks=[_custom_sql("SELECT * FROM {batch} WHERE x = 1")]
    )
    assert outcome.checks[0].errored is True
    assert "table" in (outcome.checks[0].error_message or "")


def test_custom_sql_refuses_a_non_identifier_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """The catalog is runner-held rather than per-call, so it needs its own case —
    the loop that validates it would otherwise be provable by neither of the above."""
    runner = UnityCatalogCheckRunner(
        config=UnityCatalogConfig.model_validate(_UC_CONFIG),
        token="t",
        catalog="main; DROP TABLE x",
    )

    def _must_not_build(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a rejected catalog must never reach the DSN")

    monkeypatch.setattr(runner, "_sql_batch_definition", _must_not_build)
    _forbid_dataframe_read(runner, monkeypatch)
    outcome = runner.run_checks(
        table="feedback", schema="gold", checks=[_custom_sql("SELECT * FROM {batch} WHERE x = 1")]
    )
    assert outcome.checks[0].errored is True
    assert "catalog" in (outcome.checks[0].error_message or "")


def test_gx_engine_is_disposed_after_a_sql_run(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GX owns the engine behind its own SQL datasource, so the runner's `close()`
    can't reach it — `_run_sql_checks` must close it itself or a Celery worker
    holds a warehouse session per run."""
    runner = _uc_runner()
    _sqlite_batch_seam(runner, tmp_path, rows=[1], monkeypatch=monkeypatch)
    disposed: list[bool] = []
    real_dispose = UnityCatalogCheckRunner._dispose_gx_engine

    def _spy(datasource: Any) -> None:
        disposed.append(True)
        real_dispose(datasource)

    monkeypatch.setattr(runner, "_dispose_gx_engine", _spy)
    runner.run_checks(
        table="feedback",
        schema="gold",
        checks=[_custom_sql("SELECT * FROM {batch} WHERE rating > 9")],
    )
    assert disposed == [True]


def test_dispose_gx_engine_never_masks_the_outcome() -> None:
    """Tidy-up runs in a `finally`; if it raised it would replace the result the
    caller is returning (or the exception it is propagating) with a shutdown
    error. It must swallow — and must not log the message, which can carry the
    PAT-bearing URL (#849)."""

    class _Boom:
        def get_engine(self) -> Any:
            raise RuntimeError("dapi-secret-in-the-url")

    UnityCatalogCheckRunner._dispose_gx_engine(_Boom())  # no raise


def test_gx_exposes_the_databricks_sql_datasource() -> None:
    """Dependency contract, in the spirit of the dialect check above: the SQL
    batch is `context.data_sources.add_databricks_sql`. Tests substitute sqlite
    for it, so a GX upgrade that renamed or dropped it would otherwise surface
    only as a failed production run. No network — attribute presence only."""
    import great_expectations as gx

    assert hasattr(gx.get_context(mode="ephemeral").data_sources, "add_databricks_sql")


def test_supported_monitor_kinds_is_explicit() -> None:
    # #880 review: NEVER frozenset(MONITOR_KINDS) — that would auto-advertise
    # every future registry kind and self-defeat the per-kind gate. Widening
    # this set is a conscious act, done when the runner actually implements
    # the new kind.
    assert UnityCatalogCheckRunner.supported_monitor_kinds == frozenset({"freshness", "volume"})
