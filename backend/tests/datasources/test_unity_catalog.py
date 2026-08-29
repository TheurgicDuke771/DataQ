"""Unity Catalog connection adapter tests — config validation + the SELECT 1 probe."""

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

from decimal import Decimal  # noqa: E402

import great_expectations as gx_module  # noqa: E402
import pandas as pd  # noqa: E402

from backend.app.core.config import get_settings  # noqa: E402
from backend.app.datasources import unity_catalog  # noqa: E402
from backend.app.datasources.base import CheckSpec, SampleSpec  # noqa: E402
from backend.app.datasources.sampling import (  # noqa: E402
    SamplingDrawError,
    ScanTooLargeError,
)
from backend.app.datasources.unity_catalog import (  # noqa: E402
    SQL_BATCH_EXPECTATION_TYPES,
    SQL_PUSHDOWN_EXPECTATION_TYPES,
    UnityCatalogCheckRunner,
    _fold_reflection_keyed_columns,
    _reflection_key,
    build_databricks_url,
    build_unity_catalog_runner,
)
from backend.app.services.custom_sql import is_custom_sql  # noqa: E402
from backend.app.services.failure_classifier import classify_failure_reason  # noqa: E402
from backend.app.services.severity import extract_metric  # noqa: E402
from backend.tests.support.fake_secret_store import FakeSecretStore  # noqa: E402

_REAL_SQL_BATCH_DEF = UnityCatalogCheckRunner._sql_batch_definition


@pytest.fixture(autouse=True)
def _no_live_sql_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    # #1532: a check misrouted to the live SQL seam must fail loudly, not hang on DNS for the fake
    # workspace host.
    def _refuse(self: Any, context: Any, *, table: str, schema: str) -> Any:
        pytest.fail(f"unexpected live SQL batch for {table!r} — misrouted check")

    monkeypatch.setattr(UnityCatalogCheckRunner, "_sql_batch_definition", _refuse)


def _pushdown_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UC_SQL_PUSHDOWN", "false")
    get_settings.cache_clear()


def _pushdown_on(monkeypatch: pytest.MonkeyPatch) -> None:
    # conftest defaults the flag off for the legacy frame-lane tests; opt in here.
    monkeypatch.setenv("UC_SQL_PUSHDOWN", "true")
    get_settings.cache_clear()


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
    batch needs it on the URL as well as on the asset.
    """
    cfg = UnityCatalogConfig.model_validate(_UC_CONFIG)
    url = build_databricks_url(cfg, "t", catalog="main", schema="go ld")
    assert "&catalog=main" in url
    assert "&schema=go+ld" in url  # URL-encoded like every other part
    # Unchanged default: callers that qualify the namespace themselves get none.
    assert "schema=" not in build_databricks_url(cfg, "t", catalog="main")


def test_build_unity_catalog_runner_resolves_pat() -> None:
    runner = build_unity_catalog_runner(
        config=dict(_UC_CONFIG),
        secret_ref="kv-ref",
        secret_store=FakeSecretStore(default="pat-token", raise_on_write=True),
        catalog="main",
    )
    assert isinstance(runner, UnityCatalogCheckRunner)


def test_build_unity_catalog_runner_requires_secret_ref() -> None:
    with pytest.raises(ValueError, match="secret_ref"):
        build_unity_catalog_runner(
            config=dict(_UC_CONFIG),
            secret_ref=None,
            secret_store=FakeSecretStore(default="pat-token", raise_on_write=True),
            catalog="main",
        )


def _runner_over(df: pd.DataFrame, monkeypatch: pytest.MonkeyPatch) -> UnityCatalogCheckRunner:
    # Pushdown off (#1532): these tests cover the frame path, now the rollback lane.
    _pushdown_off(monkeypatch)
    runner = UnityCatalogCheckRunner(
        config=UnityCatalogConfig.model_validate(_UC_CONFIG), token="t", catalog="main"
    )
    # Replace the live reflect+read seam with a canned frame; GX still runs for real.
    monkeypatch.setattr(runner, "_read_table", lambda **kwargs: df)
    # The scan guardrail (#595) probes COUNT(*) before the read; stub it to a
    # small table so these tests keep exercising the GX path, not the probe.
    monkeypatch.setattr(runner, "_count_rows", lambda **kwargs: len(df))
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
    """Dependency contract (#535): `_read_table` does `create_engine('databricks://…')`, whose
    dialect lives in the separate `databricks-sqlalchemy` package since databricks-sql-connector
    4.x — tests mock the runner seam, so without this check a missing dialect only surfaces as a
    failed run in production. No network: dialect load only.
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
    engine (#427). Pinned by counting `create_engine` constructions.
    """
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
    """Point the runner's SQL-batch seam at a real sqlite `feedback(rating)` table."""
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
    """Make the DataFrame seam fail loudly."""

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
    even attempted, which is what makes this the SQL batch and not the old one.
    """
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
    shape `test_custom_sql_gx.py` locks for the SQL path.
    """
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


def test_custom_sql_row_count_feeds_severity_metric_value(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Unity Catalog half of #1202: the UC SQL-batch outcome's `observed_value` feeds
    `severity.extract_metric` the same way the Snowflake-shaped path does
    (`test_gx_runner.py::test_to_suite_outcome_reads_custom_sql_row_count_as_observed_value`) —
    proving the metric is populated identically regardless of which datasource ran the check,
    per the issue's "no per-datasource divergence" requirement.
    """
    runner = _uc_runner()
    _sqlite_batch_seam(runner, tmp_path, rows=[1, 4, 5], monkeypatch=monkeypatch)
    outcome = runner.run_checks(
        table="feedback",
        schema="gold",
        checks=[_custom_sql("SELECT * FROM {batch} WHERE rating >= 4")],
    )
    check = outcome.checks[0]
    assert check.observed_value == {"observed_value": 2}
    assert extract_metric(check) == Decimal("2")


def test_custom_sql_query_error_is_an_operational_error_not_a_crash(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken query must land as `errored` (→ an `error` result, #122), never
    raise out of the runner and fail the whole run.
    """
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
    own `checks` list — a shuffle would attribute results to the wrong check.
    """
    runner = _uc_runner()
    _sqlite_batch_seam(runner, tmp_path, rows=[1, 4, 5], monkeypatch=monkeypatch)
    monkeypatch.setattr(
        runner, "_read_table", lambda **_kw: pd.DataFrame({"id": [1, 2, None], "amt": [10, 20, 30]})
    )
    # The #595 size probe runs before the read; stub it too, or it opens a
    # real warehouse connection and hangs on DNS for the fake host.
    monkeypatch.setattr(
        runner,
        "_count_rows",
        lambda **_kw: len(pd.DataFrame({"id": [1, 2, None], "amt": [10, 20, 30]})),
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
    _pushdown_off(monkeypatch)
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
    # The #595 size probe runs before the read; stub it too, or it opens a
    # real warehouse connection and hangs on DNS for the fake host.
    monkeypatch.setattr(runner, "_count_rows", lambda **_kw: len(pd.DataFrame({"id": [1, 2, 3]})))
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
    _pushdown_off(monkeypatch)
    """No custom SQL → no second warehouse session. The DataFrame path is byte-
    for-byte what it always was."""
    runner = _uc_runner()

    def _must_not_build(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a suite with no custom SQL must not build a SQL batch")

    monkeypatch.setattr(runner, "_sql_batch_definition", _must_not_build)
    monkeypatch.setattr(runner, "_read_table", lambda **_kw: pd.DataFrame({"id": [1, 2, 3]}))
    # The #595 size probe runs before the read; stub it too, or it opens a
    # real warehouse connection and hangs on DNS for the fake host.
    monkeypatch.setattr(runner, "_count_rows", lambda **_kw: len(pd.DataFrame({"id": [1, 2, 3]})))
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
    on the DataFrame batch still evaluate and persist (#122).
    """
    runner = _uc_runner()

    def _must_not_build(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("no schema → the DSN must never be built")

    monkeypatch.setattr(runner, "_sql_batch_definition", _must_not_build)
    monkeypatch.setattr(runner, "_read_table", lambda **_kw: pd.DataFrame({"id": [1, 2, 3]}))
    # The #595 size probe runs before the read; stub it too, or it opens a
    # real warehouse connection and hangs on DNS for the fake host.
    monkeypatch.setattr(runner, "_count_rows", lambda **_kw: len(pd.DataFrame({"id": [1, 2, 3]})))
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
    error the check, never reach the URL builder.
    """
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
    the loop that validates it would otherwise be provable by neither of the above.
    """
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
    _pushdown_off(monkeypatch)
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
    # The #595 size probe runs before the read; stub it too, or it opens a
    # real warehouse connection and hangs on DNS for the fake host.
    monkeypatch.setattr(runner, "_count_rows", lambda **_kw: len(pd.DataFrame({"id": [1, 2, 3]})))
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
    `classify_failure_reason` output, never `str(exc)`.
    """
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
    because the tuple it would have bound never got returned.
    """
    runner = _uc_runner()
    # This test exercises the REAL seam (against a fake GX context).
    monkeypatch.setattr(runner, "_sql_batch_definition", _REAL_SQL_BATCH_DEF.__get__(runner))
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
    """

    class _Boom:
        def get_engine(self) -> Any:
            raise RuntimeError("databricks://token:dapi-SECRET@host failed to close")

    # structlog's own capture, not `caplog`: this logger renders straight to stdout rather than
    # through stdlib logging.
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
    only as a failed production run. No network — attribute presence only.
    """
    import great_expectations as gx

    assert hasattr(gx.get_context(mode="ephemeral").data_sources, "add_databricks_sql")


def test_the_url_we_build_satisfies_gx_own_dsn_validator() -> None:
    """The real gate on `build_databricks_url`, and the reason it grew `schema`."""
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
    """Canary, in the shape of `test_supported_monitor_kinds_is_explicit` (#429)."""
    assert SQL_BATCH_EXPECTATION_TYPES == frozenset({"unexpected_rows_expectation"})
    # …and the routing predicate agrees with the declared set, so the two can't drift.
    assert all(is_custom_sql(t) for t in SQL_BATCH_EXPECTATION_TYPES)


def test_supported_monitor_kinds_is_explicit() -> None:
    # #880 review: NEVER frozenset(MONITOR_KINDS) — that would auto-advertise every future registry
    # kind and self-defeat the per-kind gate.
    assert UnityCatalogCheckRunner.supported_monitor_kinds == frozenset({"freshness", "volume"})


# ───────────────── scale-aware execution: sampling + guardrail (#595) ─────────
#
# The UC read is the hungriest full-load path measured (~925 MiB for 1M rows; 2M
# OOM-killed the child — docs/site/perf-baseline.md), so both halves matter here. The
# SQL these tests pin is DataQ's own construction, captured at the
# `pandas.read_sql_query` seam: what a live warehouse does with `TABLESAMPLE` is
# a driver-boundary fact and is verified by a live run, not by a mock (#953).


def _sampling_runner(sample: Any) -> UnityCatalogCheckRunner:
    return UnityCatalogCheckRunner(
        config=UnityCatalogConfig.model_validate(_UC_CONFIG),
        token="tok",
        catalog="main",
        sampling=sample,
    )


def _capture_query(monkeypatch: pytest.MonkeyPatch, frame: pd.DataFrame) -> list[str]:
    """Capture the SQL handed to pandas, returning the canned ``frame`` instead."""
    import pandas

    seen: list[str] = []

    def _read_sql_query(statement: Any, _con: Any, **_kw: Any) -> pd.DataFrame:
        seen.append(str(statement))
        return frame

    monkeypatch.setattr(pandas, "read_sql_query", _read_sql_query)
    return seen


def test_sample_percent_scales_the_draw_to_the_population() -> None:
    # 100 of 1,000 rows is 10%, over-drawn to 12% so a Bernoulli sample that
    # comes back light still fills the LIMIT.
    assert unity_catalog._sample_percent(100, 1_000) == pytest.approx(12.0)


def test_sample_percent_never_rounds_a_tiny_draw_down_to_zero() -> None:
    """`TABLESAMPLE (0 PERCENT)` returns NOTHING — an empty frame that would read
    as "every check passed", on no rows. The floor is what stops a very small
    sample of a very large table becoming a silent all-green run.
    """
    percent = unity_catalog._sample_percent(1, 10**12)
    assert percent > 0


@pytest.mark.parametrize(("rows", "total"), [(10, 0), (10, -5), (500, 100), (100, 100)])
def test_sample_percent_is_one_hundred_when_the_sample_covers_everything(
    rows: int, total: int
) -> None:
    assert unity_catalog._sample_percent(rows, total) == 100.0


def test_a_head_sample_pushes_a_limit_down_and_never_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bound is applied AT the warehouse, so the worker never receives the
    rows it will not look at. A head sample also skips the COUNT probe entirely —
    it does not need the population size, and a needless warehouse round trip on
    every scheduled run is the overhead #854 exists to remove.
    """
    runner = _sampling_runner(SampleSpec(strategy="head", rows=50))
    monkeypatch.setattr(
        runner,
        "_count_rows",
        lambda **_kw: pytest.fail("a head sample must not need a COUNT(*)"),
    )
    seen = _capture_query(monkeypatch, pd.DataFrame({"id": range(51)}))

    frame, record = runner._read_sampled_table(
        table="orders", schema="sales", sample=SampleSpec(strategy="head", rows=50)
    )

    assert len(seen) == 1
    # `rows + 1`: the probe row is what tells "exactly 50 rows" from "more".
    assert "LIMIT 51" in seen[0]
    assert "TABLESAMPLE" not in seen[0]
    assert len(frame) == 50
    assert record["sampled"] is True and record["total_rows"] is None


def test_a_head_sample_that_reaches_the_end_reports_a_complete_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _sampling_runner(SampleSpec(strategy="head", rows=50))
    _capture_query(monkeypatch, pd.DataFrame({"id": range(12)}))
    frame, record = runner._read_sampled_table(
        table="orders", schema="sales", sample=SampleSpec(strategy="head", rows=50)
    )
    assert len(frame) == 12
    assert record["sampled"] is False and record["total_rows"] == 12


def test_a_random_sample_pushes_tablesample_down_sized_from_the_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deliberately not `ORDER BY rand() LIMIT n` (a global sort of the whole
    table) and deliberately not `TABLESAMPLE (n ROWS)`, which Spark implements as
    a plain LIMIT — i.e. it would be a head sample wearing a random label.
    """
    runner = _sampling_runner(SampleSpec(strategy="random", rows=100, seed=1))
    monkeypatch.setattr(runner, "_count_rows", lambda **_kw: 1_000)
    seen = _capture_query(monkeypatch, pd.DataFrame({"id": range(100)}))

    frame, record = runner._read_sampled_table(
        table="orders",
        schema="sales",
        sample=SampleSpec(strategy="random", rows=100, seed=1),
    )

    assert "TABLESAMPLE (12.000000 PERCENT)" in seen[0]
    assert "LIMIT 100" in seen[0]
    assert len(frame) == 100
    assert record["total_rows"] == 1_000 and record["sampled"] is True


def test_a_random_sample_of_a_table_smaller_than_the_sample_is_not_a_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _sampling_runner(SampleSpec(strategy="random", rows=100))
    monkeypatch.setattr(runner, "_count_rows", lambda **_kw: 30)
    _capture_query(monkeypatch, pd.DataFrame({"id": range(30)}))
    _frame, record = runner._read_sampled_table(
        table="orders", schema="sales", sample=SampleSpec(strategy="random", rows=100)
    )
    assert record["sampled"] is False and record["total_rows"] == 30


def test_the_sampled_query_uses_the_dialects_own_quoting_not_a_hardcoded_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Databricks reads `"..."` as a STRING LITERAL, so a hand-rolled double
    quote would not merely be ugly — it would silently select a constant. The
    quote character has to come from the connection's dialect (#476).
    """
    runner = _sampling_runner(SampleSpec(strategy="head", rows=5))
    seen = _capture_query(monkeypatch, pd.DataFrame({"id": [1]}))
    runner._read_sampled_table(
        table="Orders", schema="Sales", sample=SampleSpec(strategy="head", rows=5)
    )
    # `main` is all lower-case, so it stays BARE and folds; only the mixed-case
    # parts are quoted — `folding_identifier`'s rule applied per namespace part.
    assert "main.`Sales`.`Orders`" in seen[0]
    assert '"Orders"' not in seen[0]


def test_a_lower_case_target_stays_unquoted_so_the_warehouse_folds_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`folding_identifier`'s rule, inherited: an all-lower-case name is emitted
    bare so it folds exactly as it did before, and only a mixed-case one is
    quoted. Quoting everything would break names created unquoted.
    """
    runner = _sampling_runner(SampleSpec(strategy="head", rows=5))
    seen = _capture_query(monkeypatch, pd.DataFrame({"id": [1]}))
    runner._read_sampled_table(
        table="orders", schema="sales", sample=SampleSpec(strategy="head", rows=5)
    )
    assert "main.sales.orders" in seen[0]
    assert "`" not in seen[0]


def test_a_schema_less_target_drops_the_catalog_rather_than_misresolving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 2-part `catalog.table` is resolved by Unity Catalog as `schema.table` —
    a DIFFERENT OBJECT, not an error. So a schema-less target falls back to the
    session defaults the URL already pins, exactly as `read_sql_table` does.
    """
    runner = _sampling_runner(SampleSpec(strategy="head", rows=5))
    seen = _capture_query(monkeypatch, pd.DataFrame({"id": [1]}))
    runner._read_sampled_table(
        table="orders", schema=None, sample=SampleSpec(strategy="head", rows=5)
    )
    assert "FROM orders" in seen[0]
    assert "main." not in seen[0]


def test_an_oversized_table_is_refused_before_the_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pushdown_off(monkeypatch)
    """#755, inverted: instead of the child being SIGKILLed and the run sitting
    `running` for an hour, the run ends with a sentence naming the knob."""
    runner = UnityCatalogCheckRunner(
        config=UnityCatalogConfig.model_validate(_UC_CONFIG), token="t", catalog="main"
    )
    monkeypatch.setattr(runner, "_count_rows", lambda **_kw: 9_000_000)
    monkeypatch.setattr(
        runner, "_read_table", lambda **_kw: pytest.fail("the table must not be read")
    )
    with pytest.raises(ScanTooLargeError, match="RUN_MAX_SCAN_ROWS"):
        runner.run_checks(
            table="orders",
            schema="sales",
            checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "id"})],
        )


def test_a_disabled_row_cap_skips_the_count_probe_entirely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pushdown_off(monkeypatch)
    """The off-switch has to be genuinely off: an operator who disables the cap
    should not keep paying a warehouse round trip for a number nobody reads."""
    monkeypatch.setenv("RUN_MAX_SCAN_ROWS", "0")
    get_settings.cache_clear()
    runner = UnityCatalogCheckRunner(
        config=UnityCatalogConfig.model_validate(_UC_CONFIG), token="t", catalog="main"
    )
    monkeypatch.setattr(
        runner,
        "_count_rows",
        lambda **_kw: pytest.fail("no COUNT(*) when the cap is off"),
    )
    monkeypatch.setattr(runner, "_read_table", lambda **_kw: pd.DataFrame({"id": [1, 2]}))
    outcome = runner.run_checks(
        table="orders",
        schema="sales",
        checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "id"})],
    )
    assert outcome.checks[0].success is True


def test_a_sampled_uc_run_is_allowed_past_the_row_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pushdown_off(monkeypatch)
    """Sampling replaces the guardrail rather than stacking with it — the read is
    bounded at the warehouse, so the table's own size stops being a memory fact.
    If the cap still applied, the feature would be unreachable where it is needed."""
    runner = _sampling_runner(SampleSpec(strategy="head", rows=10))
    monkeypatch.setattr(
        runner,
        "_count_rows",
        lambda **_kw: pytest.fail("a sampled read must not count"),
    )
    _capture_query(monkeypatch, pd.DataFrame({"id": range(11)}))
    outcome = runner.run_checks(
        table="orders",
        schema="sales",
        checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "id"})],
    )
    assert outcome.checks[0].sampling is not None
    assert outcome.checks[0].sampling["rows"] == 10


def test_a_uc_sample_over_the_row_cap_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pushdown_off(monkeypatch)
    runner = _sampling_runner(SampleSpec(strategy="head", rows=9_000_000))
    monkeypatch.setattr(
        runner,
        "_read_sampled_table",
        lambda **_kw: pytest.fail("the sample must be refused before the read"),
    )
    with pytest.raises(ScanTooLargeError, match="sample of 9,000,000"):
        runner.run_checks(
            table="orders",
            schema="sales",
            checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "id"})],
        )


def test_only_the_sampled_group_is_labelled_sampled_not_the_custom_sql_beside_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    _pushdown_off(monkeypatch)
    """The reason sampled-ness is per-CHECK and not per-run (#1179 + #595): a
    custom-SQL check evaluates against a SQL batch over the WHOLE table while the
    expectations beside it ran on the sample. One run-level flag would have to lie
    about one of them."""
    runner = _sampling_runner(SampleSpec(strategy="head", rows=2))
    # `_sqlite_batch_seam` also arms `_read_table` to fail loudly. That stays
    # armed on purpose: a sampled run must not fall back to the whole-table read.
    _sqlite_batch_seam(runner, tmp_path, [1, 4, 5], monkeypatch)
    _capture_query(monkeypatch, pd.DataFrame({"rating": [1, 4]}))

    outcome = runner.run_checks(
        table="feedback",
        schema="sales",
        checks=[
            CheckSpec("expect_column_values_to_not_be_null", {"column": "rating"}),
            _custom_sql("SELECT * FROM {batch} WHERE rating < 0"),
        ],
    )

    assert outcome.checks[0].sampling is not None, "the DataFrame group ran on a sample"
    assert outcome.checks[1].sampling is None, "the custom-SQL group saw the whole table"


def test_the_count_probe_runs_a_real_aggregate_over_the_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The guardrail refuses on this number, so the query that produces it is
    load-bearing — a probe stubbed in every test would leave the one statement the
    refusal depends on unexecuted. Run against a real (sqlite) engine, like the
    #427 shared-engine test: the statement is Core, so the dialect renders it.
    """
    import sqlalchemy

    real_create_engine = sqlalchemy.create_engine
    db_url = f"sqlite:///{tmp_path}/uc-count.sqlite"
    seed = real_create_engine(db_url)
    with seed.begin() as conn:
        conn.execute(sqlalchemy.text("CREATE TABLE orders (id INTEGER)"))
        conn.execute(sqlalchemy.text("INSERT INTO orders (id) VALUES (1), (2), (3)"))
    seed.dispose()
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda _url, **_kw: real_create_engine(db_url))

    runner = UnityCatalogCheckRunner(
        config=UnityCatalogConfig.model_validate(_UC_CONFIG),
        token="tok",
        catalog="main",
    )
    try:
        assert runner._count_rows(table="orders", schema=None) == 3
    finally:
        runner.close()


def test_an_over_cap_count_refuses_the_run_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The probe and the refusal wired together against a real engine, with the
    cap lowered under the seeded row count — so the guardrail is exercised on a
    number it actually computed rather than one a stub handed it.
    """
    import sqlalchemy

    real_create_engine = sqlalchemy.create_engine
    db_url = f"sqlite:///{tmp_path}/uc-cap.sqlite"
    seed = real_create_engine(db_url)
    with seed.begin() as conn:
        conn.execute(sqlalchemy.text("CREATE TABLE orders (id INTEGER)"))
        conn.execute(sqlalchemy.text("INSERT INTO orders (id) VALUES (1), (2), (3)"))
    seed.dispose()
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda _url, **_kw: real_create_engine(db_url))
    monkeypatch.setenv("RUN_MAX_SCAN_ROWS", "2")
    get_settings.cache_clear()

    runner = UnityCatalogCheckRunner(
        config=UnityCatalogConfig.model_validate(_UC_CONFIG),
        token="tok",
        catalog="main",
    )
    monkeypatch.setattr(
        runner, "_read_table", lambda **_kw: pytest.fail("the table must not be read")
    )
    try:
        with pytest.raises(ScanTooLargeError, match="3 rows, over the scan cap of 2"):
            runner.run_checks(
                table="orders",
                schema=None,
                checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "id"})],
            )
    finally:
        runner.close()


# ── /code-review follow-ups: C1, C2, C3 (#595) ───────────────────────────────


def test_the_percentage_never_renders_in_scientific_notation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C1, the headline defect. Databricks accepts only an INTEGER or DECIMAL literal in
    `TABLESAMPLE`, and Python's float repr flips to scientific below 1e-4 — so a 100-row sample
    of a 200M-row table emitted `TABLESAMPLE (6e-05 PERCENT)` and died with PARSE_SYNTAX_ERROR.
    """
    runner = _sampling_runner(SampleSpec(strategy="random", rows=100))
    monkeypatch.setattr(runner, "_count_rows", lambda **_kw: 200_000_000)
    seen = _capture_query(monkeypatch, pd.DataFrame({"id": range(100)}))

    runner._read_sampled_table(
        table="orders", schema="sales", sample=SampleSpec(strategy="random", rows=100)
    )

    assert "e-" not in seen[0].lower(), f"scientific notation reached the warehouse: {seen[0]}"
    assert "TABLESAMPLE (0.000060 PERCENT)" in seen[0]


@pytest.mark.parametrize(
    ("rows", "total"),
    [(1, 200_000_000), (100, 200_000_000), (10, 10**12), (100_000, 5_000_000), (1, 2)],
)
def test_every_rendered_percentage_is_a_parseable_decimal_literal(rows: int, total: int) -> None:
    """The property, not one example: whatever `_sample_percent` returns must
    survive formatting as a plain decimal. A floor that renders as `1e-06`, or as
    `0.000000`, is no floor at all — the first is a syntax error and the second is
    `TABLESAMPLE (0 PERCENT)`, which returns nothing.
    """
    rendered = unity_catalog.format_sample_percent(unity_catalog._sample_percent(rows, total))
    assert "e" not in rendered.lower()
    assert float(rendered) > 0, f"{rendered} would sample zero rows"


def test_a_tiny_sample_of_a_huge_table_is_still_drawn_reliably() -> None:
    """C3's root cause, fixed in the sizing rather than only caught after the fact. A Bernoulli
    draw sized for its exact target is a coin flip at small numbers — at an expected 1.2 rows,
    P(zero) is ~30%, and an empty frame passes every column expectation vacuously with a green
    run.
    """
    total = 200_000_000
    percent = unity_catalog._sample_percent(1, total)
    expected_rows = percent / 100.0 * total
    assert expected_rows >= unity_catalog._MIN_EXPECTED_DRAW_ROWS


def test_a_seed_is_emitted_as_repeatable_not_merely_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C2. The docs promise "add a seed to make a run reproducible", the parser accepts it and the
    record persists it — but the emitted SQL had no `REPEATABLE`, so consecutive runs drew
    different rows while every result row claimed reproducibility. Recording a property the
    query does not have is worse than not offering seeds at all.
    """
    runner = _sampling_runner(SampleSpec(strategy="random", rows=10, seed=42))
    monkeypatch.setattr(runner, "_count_rows", lambda **_kw: 10_000)
    seen = _capture_query(monkeypatch, pd.DataFrame({"id": range(10)}))

    _frame, record = runner._read_sampled_table(
        table="orders",
        schema="sales",
        sample=SampleSpec(strategy="random", rows=10, seed=42),
    )

    assert "REPEATABLE (42)" in seen[0]
    # The record and the query must agree — that agreement IS the fix.
    assert record["seed"] == 42


def test_an_unseeded_sample_emits_no_repeatable_clause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The complement: without a seed the draw must stay genuinely random, or
    every unseeded monitor would inspect the same rows forever.
    """
    runner = _sampling_runner(SampleSpec(strategy="random", rows=10))
    monkeypatch.setattr(runner, "_count_rows", lambda **_kw: 10_000)
    seen = _capture_query(monkeypatch, pd.DataFrame({"id": range(10)}))
    runner._read_sampled_table(
        table="orders", schema="sales", sample=SampleSpec(strategy="random", rows=10)
    )
    assert "REPEATABLE" not in seen[0]


def test_an_empty_draw_from_a_non_empty_table_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C3. An empty frame passes EVERY column expectation vacuously, so the run
    would print a full green board while asserting nothing about a 10,000-row
    table. `_sample_percent`'s floor makes it astronomically unlikely, but the
    failure mode is silent, so it is checked rather than assumed.
    """
    runner = _sampling_runner(SampleSpec(strategy="random", rows=10))
    monkeypatch.setattr(runner, "_count_rows", lambda **_kw: 10_000)
    _capture_query(monkeypatch, pd.DataFrame({"id": []}))

    with pytest.raises(SamplingDrawError, match="no rows from a table of 10,000"):
        runner._read_sampled_table(
            table="orders",
            schema="sales",
            sample=SampleSpec(strategy="random", rows=10),
        )


def test_an_empty_draw_from_a_GENUINELY_empty_table_is_not_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The complement, and the reason the guard reads `total` rather than just
    the frame: an empty table really does yield an empty frame, and that is the
    truth — the same answer the unsampled path gives. Refusing it would make
    sampling unusable on a table that is legitimately empty today.
    """
    runner = _sampling_runner(SampleSpec(strategy="random", rows=10))
    monkeypatch.setattr(runner, "_count_rows", lambda **_kw: 0)
    _capture_query(monkeypatch, pd.DataFrame({"id": []}))

    frame, record = runner._read_sampled_table(
        table="orders", schema="sales", sample=SampleSpec(strategy="random", rows=10)
    )
    assert len(frame) == 0
    assert record["sampled"] is False


def test_a_SHORT_draw_is_accepted_and_reported_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deliberately NOT refused. The record reports the true row count, so nothing
    is overstated — the author asked for at most N and is told exactly how many
    were read. Only ZERO is a lie, because zero rows cannot support the verdict
    the run would print.
    """
    runner = _sampling_runner(SampleSpec(strategy="random", rows=100))
    monkeypatch.setattr(runner, "_count_rows", lambda **_kw: 10_000)
    _capture_query(monkeypatch, pd.DataFrame({"id": range(63)}))

    _frame, record = runner._read_sampled_table(
        table="orders", schema="sales", sample=SampleSpec(strategy="random", rows=100)
    )
    assert record["rows"] == 63
    assert record["requested_rows"] == 100


# ── C6: row-count expectations vs a sampled read ─────────────────────────────


def test_a_row_count_expectation_is_refused_on_a_sampled_uc_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pushdown_off(monkeypatch)
    """C6. Against a sampled frame this expectation deterministically observes the
    SAMPLE and reports it as the table's size, so a healthy 5M-row table with
    `min_value=4_000_000` fails critically forever. Refused per check (#122), so
    the siblings on the same frame still evaluate and persist."""
    runner = _sampling_runner(SampleSpec(strategy="head", rows=10))
    _capture_query(monkeypatch, pd.DataFrame({"id": range(11)}))

    outcome = runner.run_checks(
        table="orders",
        schema="sales",
        checks=[
            CheckSpec("expect_table_row_count_to_be_between", {"min_value": 4_000_000}),
            CheckSpec("expect_column_values_to_not_be_null", {"column": "id"}),
        ],
    )

    assert outcome.checks[0].errored is True
    assert "row-count expectation cannot run against a sampled dataset" in (
        outcome.checks[0].error_message or ""
    )
    assert outcome.checks[1].success is True, "the sibling must still evaluate"
    # Order is submission order — `run_service` zips outcomes onto its own list.
    assert outcome.checks[0].expectation_type == "expect_table_row_count_to_be_between"


def test_a_row_count_expectation_runs_normally_without_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal must be scoped to sampling: unsampled, the count IS the
    dataset's and the expectation is perfectly valid. A blanket ban would remove
    a working check from every unsampled suite.
    """
    runner = _runner_over(pd.DataFrame({"id": [1, 2, 3]}), monkeypatch)
    outcome = runner.run_checks(
        table="orders",
        schema="sales",
        checks=[
            CheckSpec(
                "expect_table_row_count_to_be_between",
                {"min_value": 1, "max_value": 10},
            )
        ],
    )
    assert outcome.checks[0].success is True


# ───────────────────────── SQL pushdown (#1532) ─────────────────────────


def _orders_sql_seam(
    runner: UnityCatalogCheckRunner,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    rows: list[tuple[Any, Any]],
) -> list[dict[str, Any]]:
    """sqlite-backed SQL batch over `orders(id, amt)`; arms the frame seam to fail."""
    import sqlite3

    path = tmp_path / "uc_pushdown.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE orders (id INTEGER, amt INTEGER)")
    conn.executemany("INSERT INTO orders VALUES (?, ?)", rows)
    conn.commit()
    conn.close()

    calls: list[dict[str, Any]] = []

    def _seam(context: Any, *, table: str, schema: str) -> tuple[Any, Any]:
        calls.append({"table": table, "schema": schema})
        datasource = context.data_sources.add_sqlite(
            name="uc-sql", connection_string=f"sqlite:///{path}"
        )
        asset = datasource.add_table_asset(name="orders", table_name="orders")
        return datasource, asset.add_batch_definition_whole_table(name="whole_table")

    monkeypatch.setattr(runner, "_sql_batch_definition", _seam)
    _forbid_dataframe_read(runner, monkeypatch)
    return calls


def test_pushdown_runs_catalog_types_on_the_sql_batch_without_a_frame(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pushdown_on(monkeypatch)
    runner = _uc_runner()
    calls = _orders_sql_seam(runner, tmp_path, monkeypatch, rows=[(1, 10), (2, 20), (None, 30)])
    outcome = runner.run_checks(
        table="orders",
        schema="sales",
        checks=[
            CheckSpec("expect_column_values_to_not_be_null", {"column": "id"}),
            CheckSpec("expect_table_row_count_to_be_between", {"min_value": 1, "max_value": 10}),
        ],
    )
    assert calls == [{"table": "orders", "schema": "sales"}]
    by_type = {c.expectation_type: c for c in outcome.checks}
    assert by_type["expect_column_values_to_not_be_null"].success is False
    assert by_type["expect_table_row_count_to_be_between"].success is True
    assert by_type["expect_table_row_count_to_be_between"].observed_value == {"observed_value": 3}


def test_pushdown_off_never_opens_a_sql_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    df = pd.DataFrame({"id": [1]})
    runner = _runner_over(df, monkeypatch)
    monkeypatch.setattr(
        runner,
        "_sql_batch_definition",
        lambda *a, **k: pytest.fail("no SQL batch when pushdown is off"),
    )
    outcome = runner.run_checks(
        table="t",
        schema="s",
        checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "id"})],
    )
    assert outcome.checks[0].success is True


def test_type_check_stays_on_the_frame_beside_a_pushdown_check(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pushdown_on(monkeypatch)
    """`to_be_of_type` keeps its pandas dtype vocabulary; order is submission order."""
    runner = _uc_runner()
    calls = _orders_sql_seam(runner, tmp_path, monkeypatch, rows=[(1, 10), (2, 20)])
    df = pd.DataFrame({"id": pd.array([1, 2], dtype="int64")})
    monkeypatch.setattr(runner, "_read_table", lambda **_kw: df)
    monkeypatch.setattr(runner, "_count_rows", lambda **_kw: len(df))
    outcome = runner.run_checks(
        table="orders",
        schema="sales",
        checks=[
            CheckSpec("expect_column_values_to_be_of_type", {"column": "id", "type_": "int64"}),
            CheckSpec("expect_column_values_to_not_be_null", {"column": "id"}),
        ],
    )
    assert len(calls) == 1
    assert [c.expectation_type for c in outcome.checks] == [
        "expect_column_values_to_be_of_type",
        "expect_column_values_to_not_be_null",
    ]
    assert [c.success for c in outcome.checks] == [True, True]


def test_unknown_type_falls_to_the_frame(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _pushdown_on(monkeypatch)
    runner = _uc_runner()
    calls = _orders_sql_seam(runner, tmp_path, monkeypatch, rows=[(1, 10)])
    df = pd.DataFrame({"id": [1, 2, 3]})
    monkeypatch.setattr(runner, "_read_table", lambda **_kw: df)
    monkeypatch.setattr(runner, "_count_rows", lambda **_kw: len(df))
    outcome = runner.run_checks(
        table="orders",
        schema="sales",
        checks=[CheckSpec("expect_column_median_to_be_between", {"column": "id", "min_value": 1})],
    )
    assert calls == []
    assert outcome.checks[0].success is True


def test_schemaless_target_falls_back_to_frame_except_custom_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pushdown_on(monkeypatch)
    runner = _uc_runner()
    df = pd.DataFrame({"id": [1]})
    monkeypatch.setattr(runner, "_read_table", lambda **_kw: df)
    monkeypatch.setattr(runner, "_count_rows", lambda **_kw: len(df))
    monkeypatch.setattr(
        runner,
        "_sql_batch_definition",
        lambda *a, **k: pytest.fail("no SQL batch without a schema"),
    )
    outcome = runner.run_checks(
        table="orders",
        schema=None,
        checks=[
            CheckSpec("expect_column_values_to_not_be_null", {"column": "id"}),
            _custom_sql("SELECT * FROM {batch} WHERE id < 0"),
        ],
    )
    assert outcome.checks[0].success is True
    assert outcome.checks[1].errored is True
    assert "schema" in (outcome.checks[1].error_message or "")


def test_a_declared_sample_wins_over_pushdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """The author asked for bounded evaluation; the sampling contract beats pushdown,
    so the whole non-custom-SQL group stays on the (sampled) frame.
    """
    _pushdown_on(monkeypatch)
    runner = _sampling_runner(SampleSpec(strategy="head", rows=50))
    monkeypatch.setattr(runner, "_count_rows", lambda **_kw: 3)
    monkeypatch.setattr(
        runner,
        "_read_sampled_table",
        lambda **_kw: (pd.DataFrame({"id": [1, 2, 3]}), {"strategy": "head", "rows": 50}),
    )
    outcome = runner.run_checks(
        table="orders",
        schema="sales",
        checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "id"})],
    )
    assert outcome.checks[0].success is True
    assert outcome.checks[0].sampling is not None


def test_index_columns_forwarded_for_pushdown_and_dropped_for_pure_custom_sql(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pushdown_on(monkeypatch)
    from backend.app.datasources import unity_catalog as uc_module
    from backend.app.datasources.gx_runner import run_expectations as real

    seen: list[Any] = []

    def _spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("index_columns"))
        return real(*args, **kwargs)

    monkeypatch.setattr(uc_module, "run_expectations", _spy)

    runner = _uc_runner()
    _orders_sql_seam(runner, tmp_path, monkeypatch, rows=[(1, 10)])
    runner.run_checks(
        table="orders",
        schema="sales",
        checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "id"})],
        index_columns=["amt"],
    )
    assert seen == [["amt"]]

    seen.clear()
    runner2 = _uc_runner()
    _sqlite_batch_seam(runner2, tmp_path, rows=[1, 2], monkeypatch=monkeypatch)
    runner2.run_checks(
        table="feedback",
        schema="gold",
        checks=[_custom_sql("SELECT * FROM {batch} WHERE rating > 99")],
        index_columns=["rating"],
    )
    assert seen == [None]


def test_pushdown_allowlist_partitions_the_catalog() -> None:
    """Every catalog expectation type is consciously routed: pushdown, frame
    (`to_be_of_type`), or custom SQL. A new catalog entry must pick a side.
    """
    import json
    from pathlib import Path

    from backend.app.datasources.snowflake_dmf import DMF_EXPECTATION_TYPES

    fixture = Path(__file__).parent.parent / "fixtures" / "expectation_catalog.json"
    catalog_types = {
        e["type"]
        for e in json.loads(fixture.read_text())
        if e["kind"] == "expectation" and e["type"] not in DMF_EXPECTATION_TYPES
    }
    # Deliberately NOT pushed down, permanently: `to_be_of_type`/`in_type_list` compare a
    # dtype (pushdown would flip pandas-dtype spellings to dialect type strings — breaking).
    # The other two have no SqlAlchemy provider at all (DATAFRAME_ONLY_EXPECTATION_TYPES).
    frame_only = {
        "expect_column_values_to_be_of_type",
        "expect_column_values_to_be_in_type_list",
        "expect_column_values_to_match_strftime_format",
        "expect_column_values_to_be_json_parseable",
    }
    from backend.app.datasources.expectation_allowlist import (
        ALLOWLIST_ONLY_TYPES,
        DATAFRAME_ONLY_EXPECTATION_TYPES,
    )

    routed = SQL_PUSHDOWN_EXPECTATION_TYPES | frame_only | SQL_BATCH_EXPECTATION_TYPES
    # Allowlist-only types (e.g. a list-of-pairs shape the editor has no widget for) never
    # appear in the catalog fixture but still need a route, so exclude them on one side.
    assert catalog_types == routed - ALLOWLIST_ONLY_TYPES
    assert ALLOWLIST_ONLY_TYPES <= SQL_PUSHDOWN_EXPECTATION_TYPES
    # A union hides an overlap: a type in BOTH sets satisfies the equality above while every UC
    # run of it raises "No provider found" on the pushed-down batch.
    assert not (SQL_PUSHDOWN_EXPECTATION_TYPES & DATAFRAME_ONLY_EXPECTATION_TYPES)
    assert not (SQL_PUSHDOWN_EXPECTATION_TYPES & frame_only)


def test_fold_reflection_keyed_columns_lowercases_all_caps_compound_unique() -> None:
    # An all-caps column_list must fold to match the reflected (lower-cased) key.
    spec = CheckSpec(
        "expect_compound_columns_to_be_unique",
        {"column_list": ["ORDER_NUMBER", "CUSTOMER_ID"], "mostly": 0.9},
    )
    (folded,) = _fold_reflection_keyed_columns([spec])
    assert folded.kwargs["column_list"] == ["order_number", "customer_id"]
    assert folded.kwargs["mostly"] == 0.9
    # frozen input untouched
    assert spec.kwargs["column_list"] == ["ORDER_NUMBER", "CUSTOMER_ID"]


def test_fold_reflection_keyed_columns_leaves_mixed_case_and_other_types_alone() -> None:
    mixed = CheckSpec(
        "expect_compound_columns_to_be_unique", {"column_list": ["OrderNum", "customer_id"]}
    )
    other = CheckSpec(
        "expect_multicolumn_sum_to_equal", {"column_list": ["SUBTOTAL", "TAX"], "sum_total": 1}
    )
    folded_mixed, folded_other = _fold_reflection_keyed_columns([mixed, other])
    assert folded_mixed.kwargs["column_list"] == ["OrderNum", "customer_id"]
    assert folded_other is other


def test_reflection_key_mirrors_the_databricks_dialect_not_a_bare_lower() -> None:
    # Reserved words and quote-requiring names keep their case — a bare isupper() fold
    # would break them.
    assert _reflection_key("ORDER_NUMBER") == "order_number"
    assert _reflection_key("ORDER") == "ORDER"
    assert _reflection_key("ORDER DATE") == "ORDER DATE"
    assert _reflection_key("OrderNum") == "OrderNum"


def test_a_folding_failure_errors_the_sql_group_not_the_whole_run(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fold runs inside the SQL group's own try/except, so a failure there is an
    isolated `errored` outcome, not an unhandled crash that discards a mixed suite's
    already-computed frame-group results."""
    from backend.app.datasources import unity_catalog as uc_module

    monkeypatch.setattr(
        uc_module,
        "_fold_reflection_keyed_columns",
        lambda checks: (_ for _ in ()).throw(RuntimeError("dialect quirk")),
    )
    _pushdown_on(monkeypatch)
    runner = _uc_runner()
    _orders_sql_seam(runner, tmp_path, monkeypatch, rows=[(1, 10)])
    outcome = runner.run_checks(
        table="orders",
        schema="sales",
        checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "id"})],
    )
    assert outcome.success is False
    assert outcome.checks[0].errored is True


def test_index_column_clash_runs_without_the_index_request(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pushdown_on(monkeypatch)
    """A check on its own index column drops the locator request (Databricks
    refuses the duplicate-field index query — live-found, #1532); siblings keep it."""
    from backend.app.datasources import unity_catalog as uc_module
    from backend.app.datasources.gx_runner import run_expectations as real

    seen: list[tuple[list[str], Any]] = []

    def _spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(([c.expectation_type for c in kwargs["checks"]], kwargs.get("index_columns")))
        return real(*args, **kwargs)

    monkeypatch.setattr(uc_module, "run_expectations", _spy)
    runner = _uc_runner()
    _orders_sql_seam(runner, tmp_path, monkeypatch, rows=[(1, 10), (None, 20)])
    outcome = runner.run_checks(
        table="orders",
        schema="sales",
        checks=[
            CheckSpec("expect_column_values_to_not_be_null", {"column": "id"}),
            CheckSpec(
                "expect_column_values_to_be_between",
                {"column": "amt", "min_value": 0, "max_value": 100},
            ),
        ],
        index_columns=["id"],
    )
    assert seen == [
        (["expect_column_values_to_be_between"], ["id"]),
        (["expect_column_values_to_not_be_null"], None),
    ]
    assert [c.expectation_type for c in outcome.checks] == [
        "expect_column_values_to_not_be_null",
        "expect_column_values_to_be_between",
    ]
    assert outcome.checks[0].success is False
    assert outcome.checks[1].success is True


def test_pushdown_default_is_on() -> None:
    # The test suite pins UC_SQL_PUSHDOWN=false in conftest; the SHIPPED default is on.
    from backend.app.core.config import Settings

    assert Settings.model_fields["uc_sql_pushdown"].default is True


def test_index_column_clash_is_case_insensitive(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Databricks resolves identifiers case-insensitively; the clash compare must too."""
    from backend.app.datasources import unity_catalog as uc_module
    from backend.app.datasources.gx_runner import run_expectations as real

    seen: list[Any] = []

    def _spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("index_columns"))
        return real(*args, **kwargs)

    monkeypatch.setattr(uc_module, "run_expectations", _spy)
    _pushdown_on(monkeypatch)
    runner = _uc_runner()
    _orders_sql_seam(runner, tmp_path, monkeypatch, rows=[(1, 10)])
    runner.run_checks(
        table="orders",
        schema="sales",
        checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "ID"})],
        index_columns=["id"],
    )
    assert seen == [None]


def test_index_column_clash_is_detected_via_column_a_and_column_b(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pair check keyed on column_A/column_B, not `column`, still drops the
    index request when either side is the index column."""
    from backend.app.datasources import unity_catalog as uc_module
    from backend.app.datasources.gx_runner import run_expectations as real

    seen: list[Any] = []

    def _spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("index_columns"))
        return real(*args, **kwargs)

    monkeypatch.setattr(uc_module, "run_expectations", _spy)
    _pushdown_on(monkeypatch)
    runner = _uc_runner()
    _orders_sql_seam(runner, tmp_path, monkeypatch, rows=[(1, 10)])
    runner.run_checks(
        table="orders",
        schema="sales",
        checks=[
            CheckSpec(
                "expect_column_pair_values_a_to_be_greater_than_b",
                {"column_A": "amt", "column_B": "id"},
            )
        ],
        index_columns=["id"],
    )
    assert seen == [None]


def test_index_column_clash_is_detected_via_column_list(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check keyed on column_list, not `column`, still drops the index request
    when any listed column is the index column."""
    from backend.app.datasources import unity_catalog as uc_module
    from backend.app.datasources.gx_runner import run_expectations as real

    seen: list[Any] = []

    def _spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("index_columns"))
        return real(*args, **kwargs)

    monkeypatch.setattr(uc_module, "run_expectations", _spy)
    _pushdown_on(monkeypatch)
    runner = _uc_runner()
    _orders_sql_seam(runner, tmp_path, monkeypatch, rows=[(1, 10)])
    runner.run_checks(
        table="orders",
        schema="sales",
        checks=[
            CheckSpec(
                "expect_select_column_values_to_be_unique_within_record",
                {"column_list": ["id", "amt"]},
            )
        ],
        index_columns=["id"],
    )
    assert seen == [None]


def test_a_clash_group_failure_keeps_the_kept_groups_outcomes(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure evaluating the no-index group errors only ITS checks; the kept
    group's already-computed outcomes survive.
    """
    from backend.app.datasources import unity_catalog as uc_module
    from backend.app.datasources.gx_runner import run_expectations as real

    def _fail_second(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("name", "").startswith("suite-uc-sql-noidx"):
            raise RuntimeError("warehouse auto-stopped mid-run")
        return real(*args, **kwargs)

    monkeypatch.setattr(uc_module, "run_expectations", _fail_second)
    _pushdown_on(monkeypatch)
    runner = _uc_runner()
    _orders_sql_seam(runner, tmp_path, monkeypatch, rows=[(1, 10)])
    outcome = runner.run_checks(
        table="orders",
        schema="sales",
        checks=[
            CheckSpec("expect_column_values_to_not_be_null", {"column": "id"}),
            CheckSpec(
                "expect_column_values_to_be_between",
                {"column": "amt", "min_value": 0, "max_value": 100},
            ),
        ],
        index_columns=["id"],
    )
    assert outcome.success is False
    assert outcome.checks[0].errored is True
    assert outcome.checks[1].errored is False
    assert outcome.checks[1].success is True
