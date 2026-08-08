"""Unity Catalog connection adapter tests — config validation + the SELECT 1 probe.

No live Databricks: ``databricks.sql.connect`` is monkeypatched so the
warehouse probe runs against a fake. The adapter is DB-free, so these are pure
unit tests (no db_session).
"""

from typing import Any

import pytest
from databricks import sql
from pydantic import ValidationError
from structlog.testing import capture_logs

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

import great_expectations as gx_module  # noqa: E402
import pandas as pd  # noqa: E402

from backend.app.datasources.base import CheckSpec  # noqa: E402
from backend.app.datasources.unity_catalog import (  # noqa: E402
    SQL_BATCH_EXPECTATION_TYPES,
    UnityCatalogCheckRunner,
    build_databricks_url,
    build_unity_catalog_runner,
)
from backend.app.services.custom_sql import is_custom_sql  # noqa: E402
from backend.app.services.failure_classifier import classify_failure_reason  # noqa: E402


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


def test_mixed_suite_keeps_order_when_a_dataframe_check_errors(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same merge, under the condition that produced #767.

    GX 1.17 `graph_validate` returns results in submission order only while
    nothing errors; once any expectation errors it appends the errored ones
    FIRST. `gx_runner` re-keys with the `dataq_index` marker, but that marker is
    stamped per `run_expectations` call — so with two calls it is group-LOCAL,
    and the outer positional merge is what has to carry group→global. The
    happy-path mixed test above cannot exercise any of that, because no check
    errors in it.

    The errored DataFrame check is submitted **last** on purpose. Its group's
    submission order is [healthy, errored], GX returns [errored, healthy], so the
    re-key genuinely has work to do — put the errored one first instead and GX's
    raw order would already be correct, making the test pass without exercising
    anything. Two identical expectation *types* on different columns make a
    cross-wire visible rather than a coin flip.
    """
    runner = _uc_runner()
    _sqlite_batch_seam(runner, tmp_path, rows=[1, 4, 5], monkeypatch=monkeypatch)
    monkeypatch.setattr(runner, "_read_table", lambda **_kw: pd.DataFrame({"id": [1, 2, 3]}))
    outcome = runner.run_checks(
        table="feedback",
        schema="gold",
        checks=[
            CheckSpec("expect_column_values_to_not_be_null", {"column": "id"}),
            _custom_sql("SELECT * FROM {batch} WHERE rating >= 4"),
            CheckSpec("expect_column_values_to_not_be_null", {"column": "absent_column"}),
        ],
    )
    healthy, custom, errored = outcome.checks
    assert [c.expectation_type for c in outcome.checks] == [
        "expect_column_values_to_not_be_null",
        "unexpected_rows_expectation",
        "expect_column_values_to_not_be_null",
    ]
    assert healthy.errored is False
    assert healthy.success is True
    assert healthy.expected_value == {"column": "id"}
    assert custom.observed_value == {"observed_value": 2}
    assert errored.errored is True
    assert errored.expected_value == {"column": "absent_column"}


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


def test_an_unreachable_sql_batch_errors_only_its_own_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Building the SQL batch can fail on its own (auto-stopped warehouse, expired
    PAT, missing grant) — GX tests the connection and validates the table there.

    That must NOT propagate: `run_checks` evaluates the DataFrame group FIRST, so
    an exception out of the SQL group would throw away outcomes that already
    succeeded and fail the whole run — a blast radius the single-batch runner
    never had.
    """
    runner = _uc_runner()

    def _warehouse_down(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("Could not connect: dapi-SECRET-in-the-dsn")

    monkeypatch.setattr(runner, "_sql_batch_definition", _warehouse_down)
    monkeypatch.setattr(runner, "_read_table", lambda **_kw: pd.DataFrame({"id": [1, 2, 3]}))
    outcome = runner.run_checks(
        table="feedback",
        schema="gold",
        checks=[
            _custom_sql("SELECT * FROM {batch} WHERE rating >= 4"),
            CheckSpec("expect_column_values_to_not_be_null", {"column": "id"}),
        ],
    )
    custom, sibling = outcome.checks
    assert custom.errored is True
    # The sibling still evaluated and is still reported — the whole point.
    assert sibling.errored is False
    assert sibling.success is True
    assert outcome.success is False


def test_an_unreachable_sql_batch_reports_a_classified_reason_not_driver_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`error_message` is persisted verbatim into `results.observed_value` and
    rendered in the UI — a sink the logger-level scrubber never sees. A driver
    error can echo the PAT-bearing DSN (#849/#900), so the reason must be
    `classify_failure_reason` output, never `str(exc)`."""
    runner = _uc_runner()

    def _warehouse_down(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("Could not connect to databricks://token:dapi-SECRET@host")

    monkeypatch.setattr(runner, "_sql_batch_definition", _warehouse_down)
    _forbid_dataframe_read(runner, monkeypatch)
    outcome = runner.run_checks(
        table="feedback", schema="gold", checks=[_custom_sql("SELECT * FROM {batch} WHERE x = 1")]
    )
    message = outcome.checks[0].error_message or ""
    assert "dapi-SECRET" not in message
    assert "databricks://" not in message
    # …and it still says something actionable rather than going silent.
    assert message == classify_failure_reason(RuntimeError("Could not connect"))
    assert message


def test_a_failure_building_the_asset_still_disposes_the_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`add_databricks_sql` builds AND tests the engine before it returns, so a
    later failure inside `_sql_batch_definition` (a table the role can't see)
    strands a live warehouse session: the caller's `finally` can't reach it,
    because the tuple it would have bound never got returned."""
    runner = _uc_runner()
    disposed: list[object] = []

    class _FakeDatasource:
        def add_table_asset(self, **_kwargs: Any) -> Any:
            raise RuntimeError("TABLE_OR_VIEW_NOT_FOUND")

        def get_engine(self) -> Any:
            class _Engine:
                def dispose(self) -> None:
                    disposed.append(True)

            return _Engine()

    class _FakeSources:
        def add_databricks_sql(self, **_kwargs: Any) -> Any:
            return _FakeDatasource()

    class _FakeContext:
        data_sources = _FakeSources()

    monkeypatch.setattr(gx_module, "get_context", lambda **_kw: _FakeContext())
    _forbid_dataframe_read(runner, monkeypatch)
    outcome = runner.run_checks(
        table="feedback", schema="gold", checks=[_custom_sql("SELECT * FROM {batch} WHERE x = 1")]
    )
    assert outcome.checks[0].errored is True
    assert disposed == [True], "the engine GX had already built must be closed"


def test_gx_engine_is_disposed_after_a_sql_run(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GX owns the engine behind its own SQL datasource, so the runner's `close()`
    can't reach it — `_run_sql_checks` must close it itself or a Celery worker
    holds a warehouse session per run.

    Asserts the **SQLAlchemy engine** was disposed, not that our own wrapper was
    called. Spying on the wrapper proves nothing: `_dispose_gx_engine` swallows
    everything, so if GX ever renamed `get_engine()` the disposal would become a
    permanent silent no-op and a wrapper-level spy would stay green — the
    "fixture encodes our model" shape (#823/#520). The log assertion closes the
    same hole from the other side.
    """
    from sqlalchemy.engine import Engine

    runner = _uc_runner()
    _sqlite_batch_seam(runner, tmp_path, rows=[1], monkeypatch=monkeypatch)
    disposed: list[object] = []
    real_dispose = Engine.dispose

    def _counting_dispose(self: Any, close: bool = True) -> None:
        disposed.append(self)
        real_dispose(self, close)

    monkeypatch.setattr(Engine, "dispose", _counting_dispose)
    with capture_logs() as events:
        runner.run_checks(
            table="feedback",
            schema="gold",
            checks=[_custom_sql("SELECT * FROM {batch} WHERE rating > 9")],
        )
    assert disposed, "the engine GX built behind its own datasource was never disposed"
    assert "uc_sql_engine_dispose_failed" not in repr(
        events
    ), "disposal silently failed — `get_engine()` may have been renamed by a GX upgrade"


def test_dispose_gx_engine_never_masks_the_outcome() -> None:
    """Tidy-up runs in a `finally`; if it raised it would replace the result the
    caller is returning (or the exception it is propagating) with a shutdown
    error. It must swallow — and must not log the exception's MESSAGE, which can
    carry the PAT-bearing URL the engine was built from (#849).

    Both halves are asserted. The message half used to be a docstring claim only,
    which is the shape the repo has been bitten by: a later edit to
    `log.warning(..., error=str(exc))` would have sailed through green.
    """

    class _Boom:
        def get_engine(self) -> Any:
            raise RuntimeError("databricks://token:dapi-SECRET@host failed to close")

    # structlog's own capture, not `caplog`: this logger renders straight to
    # stdout rather than through stdlib logging, so caplog sees nothing and the
    # assertions below would pass vacuously against an empty list.
    with capture_logs() as events:
        UnityCatalogCheckRunner._dispose_gx_engine(_Boom())  # no raise

    emitted = repr(events)
    assert "uc_sql_engine_dispose_failed" in emitted, "the failure must still be observable"
    assert "dapi-SECRET" not in emitted
    assert "databricks://" not in emitted
    assert "RuntimeError" in emitted  # the type is what makes it triageable


def test_gx_exposes_the_databricks_sql_datasource() -> None:
    """Dependency contract, in the spirit of the dialect check above: the SQL
    batch is `context.data_sources.add_databricks_sql`. Tests substitute sqlite
    for it, so a GX upgrade that renamed or dropped it would otherwise surface
    only as a failed production run. No network — attribute presence only."""
    import great_expectations as gx

    assert hasattr(gx.get_context(mode="ephemeral").data_sources, "add_databricks_sql")


def test_the_url_we_build_satisfies_gx_own_dsn_validator() -> None:
    """The real gate on `build_databricks_url`, and the reason it grew `schema`.

    `_sql_batch_definition` is 100% substituted by sqlite in these tests — the
    #535 shape, where "CI never saw it because tests mock the runner seam". So
    assert the URL against **GX's own `DatabricksDsn`**, which is what actually
    rejects it, rather than against a substring of our own making. Network-free:
    `validate_parts` only parses the query string.
    """
    from great_expectations.compatibility import pydantic
    from great_expectations.datasource.fluent.databricks_sql_datasource import DatabricksDsn

    cfg = UnityCatalogConfig.model_validate(_UC_CONFIG)
    # What the runner actually builds — must parse.
    pydantic.parse_obj_as(
        DatabricksDsn, build_databricks_url(cfg, "tok", catalog="main", schema="gold")
    )
    # …and each omission GX rejects, so the requirement is pinned, not assumed.
    for missing, url in (
        ("schema", build_databricks_url(cfg, "tok", catalog="main")),
        ("catalog", build_databricks_url(cfg, "tok", schema="gold")),
        ("catalog", build_databricks_url(cfg, "tok")),
    ):
        with pytest.raises(pydantic.ValidationError, match=missing):
            pydantic.parse_obj_as(DatabricksDsn, url)


def test_sql_batch_expectation_types_is_explicit() -> None:
    """Canary, in the shape of `test_supported_monitor_kinds_is_explicit` (#429).

    `run_checks` routes by exclusion — everything that is not custom SQL goes to
    the pandas batch — so a future GX expectation with SqlAlchemy-only metrics
    would silently reproduce #1179 instead of being routed. This pins today's
    answer so widening it is a conscious edit with a failing test attached.
    """
    assert SQL_BATCH_EXPECTATION_TYPES == frozenset({"unexpected_rows_expectation"})
    # …and the routing predicate agrees with the declared set, so the two can't drift.
    assert all(is_custom_sql(t) for t in SQL_BATCH_EXPECTATION_TYPES)


def test_supported_monitor_kinds_is_explicit() -> None:
    # #880 review: NEVER frozenset(MONITOR_KINDS) — that would auto-advertise
    # every future registry kind and self-defeat the per-kind gate. Widening
    # this set is a conscious act, done when the runner actually implements
    # the new kind.
    assert UnityCatalogCheckRunner.supported_monitor_kinds == frozenset({"freshness", "volume"})
