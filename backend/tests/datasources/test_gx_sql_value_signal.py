"""The SQL lanes derive the failing-population value signal from a gated, bounded query (#2014).

Real GX end to end on sqlite SQLAlchemy batches (the Snowflake / Unity-Catalog-pushdown shape —
GX registers its metric providers against the engine CLASS, so the dialect does not change which
code path runs) beside the same data on a pandas batch (the flat-file shape). The fixture is the
shape where the two lanes used to disagree: the first `SAMPLE_ROW_CAP` failing values of an
unclassified column look harmless and the failing population behind them is mostly e-mail
addresses. The frame lane classifies the population and masks; before #2014 the SQL lane
classified only the capped 20 and showed them.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import great_expectations as gx
import numpy as np
import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.engine import Engine
from structlog.testing import capture_logs

from backend.app.core.config import get_settings
from backend.app.datasources import gx_runner
from backend.app.datasources.base import (
    SAMPLE_ROW_CAP,
    VALUE_SIGNAL_SAMPLE_FAILED,
    VALUE_SIGNAL_STATUS_KEY,
    VALUE_SIGNAL_SUMMARY_KEY,
    CheckOutcome,
    CheckSpec,
)
from backend.app.datasources.gx_runner import (
    _SQL_PARTIAL_UNEXPECTED_COUNT,
    _VALUE_SIGNAL_SUMMARY_ROW_CAP,
    run_expectations,
)
from backend.app.datasources.unity_catalog import UnityCatalogCheckRunner, UnityCatalogConfig
from backend.app.services.run_service import (
    build_value_signal_gate,
    redact_sample_failures_with_state,
    value_signal_decides,
)

_HARMLESS_LEAD = SAMPLE_ROW_CAP
_EMAILS = 480
_TESTED = "channel"
_INDEX_COLUMNS = ["customer_id"]
_IN_SET = "expect_column_values_to_be_in_set"
_CHECK = CheckSpec(_IN_SET, {"column": _TESTED, "value_set": ["n/a"]})


def _always(_column: str) -> bool:
    return True


def _never(_column: str) -> bool:
    return False


def _frame(*, emails: int = _EMAILS) -> pd.DataFrame:
    channel = ["walk-in"] * _HARMLESS_LEAD + [f"buyer{i}@example.com" for i in range(emails)]
    return pd.DataFrame(
        {"customer_id": list(range(1000, 1000 + len(channel))), "channel": channel, "qty": 1}
    )


def _sqlite_url(tmp_path: Path, name: str, frame: pd.DataFrame) -> str:
    url = f"sqlite:///{tmp_path / f'{name}.db'}"
    engine = sa.create_engine(url)
    frame.to_sql("orders", engine, index=False)
    engine.dispose()
    return url


def _sql_batch(tmp_path: Path, name: str, frame: pd.DataFrame) -> tuple[Any, Any]:
    url = _sqlite_url(tmp_path, name, frame)
    context = gx.get_context(mode="ephemeral")
    source = context.data_sources.add_sqlite(name=f"sq-{name}", connection_string=url)
    asset = source.add_table_asset(name="orders", table_name="orders")
    return context, asset.add_batch_definition_whole_table("bd")


def _run_sql(
    tmp_path: Path,
    *,
    name: str = "sql",
    frame: pd.DataFrame | None = None,
    gate: Any = _always,
    checks: list[CheckSpec] | None = None,
    index_columns: list[str] | None = _INDEX_COLUMNS,
) -> CheckOutcome:
    context, batch_definition = _sql_batch(tmp_path, name, _frame() if frame is None else frame)
    outcome = run_expectations(
        context,
        batch_definition=batch_definition,
        checks=checks or [_CHECK],
        name=name,
        index_columns=index_columns,
        value_signal_gate=gate,
    )
    return outcome.checks[0]


def _run_frame(frame: pd.DataFrame, *, index_columns: list[str] | None = _INDEX_COLUMNS) -> Any:
    context = gx.get_context(mode="ephemeral")
    asset = context.data_sources.add_pandas(name="p").add_dataframe_asset(name="t")
    batch_definition = asset.add_batch_definition_whole_dataframe(name="wd")
    outcome = run_expectations(
        context,
        batch_definition=batch_definition,
        checks=[_CHECK],
        name="frame",
        batch_parameters={"dataframe": frame},
        index_columns=index_columns,
    )
    return outcome.checks[0]


def _redacted(check: CheckOutcome) -> tuple[Any, Any, list[str]]:
    return redact_sample_failures_with_state(check.sample_failures, tested_column=_TESTED)


class _StatementSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def __enter__(self) -> _StatementSpy:
        event.listen(Engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc: object) -> None:
        event.remove(Engine, "before_cursor_execute", self._record)

    def _record(
        self, conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        self.calls.append((" ".join(statement.split()), tuple(parameters or ())))

    def row_selects(self) -> list[tuple[str, Any]]:
        """Row-returning selects over the failing rows (not aggregates, not reflection)."""
        return [
            (statement, params)
            for statement, params in self.calls
            if statement.startswith("SELECT customer_id, channel")
            or statement.startswith("SELECT channel FROM")
        ]


@pytest.mark.parametrize("index_columns", [_INDEX_COLUMNS, None])
def test_sql_lane_masks_the_same_column_the_frame_lane_masks(
    tmp_path: Path, index_columns: list[str] | None
) -> None:
    """The regression: same data, same unclassified column — the SQL lane now reaches the
    frame lane's masking verdict because it classifies the failing population, not the 20."""
    sql = _run_sql(tmp_path, index_columns=index_columns)
    redacted, state, columns = _redacted(sql)
    assert redacted is not None
    shown = redacted.get("unexpected_index_list") or redacted["partial_unexpected_list"]
    assert "walk-in" not in str(shown)
    assert _TESTED in columns

    assert sql.sample_failures is not None
    summary = sql.sample_failures[VALUE_SIGNAL_SUMMARY_KEY]
    assert summary[_TESTED]["n"] == _HARMLESS_LEAD + _EMAILS
    assert summary[_TESTED]["email_count"] == _EMAILS

    if index_columns:
        frame_redacted, frame_state, frame_columns = _redacted(_run_frame(_frame()))
        assert (state, columns) == (frame_state, frame_columns)
        assert redacted["unexpected_index_list"] == frame_redacted["unexpected_index_list"]


def test_extra_query_is_bounded_and_the_locator_query_is_not_widened(tmp_path: Path) -> None:
    """One separate select at the summary cap; the locator query keeps its cheap LIMIT."""
    with _StatementSpy() as spy:
        _run_sql(tmp_path)
    limits = sorted(
        next(p for p in params if isinstance(p, int) and p >= SAMPLE_ROW_CAP)
        for statement, params in spy.row_selects()
        if statement.startswith("SELECT customer_id, channel")
    )
    assert limits == [_SQL_PARTIAL_UNEXPECTED_COUNT, _VALUE_SIGNAL_SUMMARY_ROW_CAP]
    for statement, _ in spy.row_selects():
        assert "LIMIT" in statement


def test_population_sample_stops_at_the_summary_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gx_runner, "_VALUE_SIGNAL_SUMMARY_ROW_CAP", 100)
    check = _run_sql(tmp_path)
    assert check.sample_failures is not None
    assert check.sample_failures[VALUE_SIGNAL_SUMMARY_KEY][_TESTED]["n"] == 100


def test_no_extra_query_when_the_ladder_already_decides(tmp_path: Path) -> None:
    """Gate says policy/tags/name decide every column → no query, no summary, the 20 stand."""
    with _StatementSpy() as spy:
        check = _run_sql(tmp_path, gate=_never)
    assert [
        params for statement, params in spy.row_selects() if _VALUE_SIGNAL_SUMMARY_ROW_CAP in params
    ] == []
    assert check.sample_failures is not None
    assert VALUE_SIGNAL_SUMMARY_KEY not in check.sample_failures
    assert VALUE_SIGNAL_STATUS_KEY not in check.sample_failures


def test_gate_is_asked_about_every_column_the_summary_covers(tmp_path: Path) -> None:
    asked: list[str] = []

    def gate(column: str) -> bool:
        asked.append(column)
        return column == "customer_id"

    check = _run_sql(tmp_path, gate=gate)
    assert asked == [_TESTED, "customer_id"]
    assert check.sample_failures is not None
    assert set(check.sample_failures[VALUE_SIGNAL_SUMMARY_KEY]) == {_TESTED, "customer_id"}


def test_no_extra_query_when_the_capped_sample_is_the_whole_population(tmp_path: Path) -> None:
    """At or under the cap the 20 values ARE the population — same as the frame lane."""
    with _StatementSpy() as spy:
        check = _run_sql(tmp_path, frame=_frame(emails=0))
    assert [p for _, p in spy.row_selects() if _VALUE_SIGNAL_SUMMARY_ROW_CAP in p] == []
    assert check.sample_failures is not None
    assert VALUE_SIGNAL_SUMMARY_KEY not in check.sample_failures


def test_clean_and_aggregate_checks_never_pay_the_query(tmp_path: Path) -> None:
    checks = [
        CheckSpec(_IN_SET, {"column": "qty", "value_set": [1]}),
        CheckSpec("expect_column_max_to_be_between", {"column": "qty", "max_value": 0}),
    ]
    context, batch_definition = _sql_batch(tmp_path, "agg", _frame())
    with _StatementSpy() as spy:
        outcome = run_expectations(
            context,
            batch_definition=batch_definition,
            checks=checks,
            name="agg",
            index_columns=_INDEX_COLUMNS,
            value_signal_gate=_always,
        )
    assert [p for _, p in spy.calls if _VALUE_SIGNAL_SUMMARY_ROW_CAP in p] == []
    assert outcome.checks[0].success is True
    assert outcome.checks[1].success is False


def test_a_check_passing_under_mostly_still_gets_the_signal(tmp_path: Path) -> None:
    """`mostly` lets a check pass with its unexpected rows still sampled and shown; the frame
    lane summarises them regardless of the verdict, so the SQL lane must too."""
    lenient = CheckSpec(_IN_SET, {"column": _TESTED, "value_set": ["n/a"], "mostly": 0.0})
    sql = _run_sql(tmp_path, checks=[lenient])
    assert sql.success is True
    redacted, _, columns = _redacted(sql)
    assert redacted is not None
    assert "walk-in" not in str(redacted["unexpected_index_list"])
    assert _TESTED in columns


def test_frame_lane_never_runs_the_extra_query() -> None:
    """The frame lane's own locator list already carries the population (#1995)."""

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("frame lane must not build a population validator")

    mp = pytest.MonkeyPatch()
    mp.setattr(gx_runner, "_batch_validator", _boom)
    try:
        context = gx.get_context(mode="ephemeral")
        asset = context.data_sources.add_pandas(name="p2").add_dataframe_asset(name="t2")
        outcome = run_expectations(
            context,
            batch_definition=asset.add_batch_definition_whole_dataframe(name="wd2"),
            checks=[_CHECK],
            name="frame-gated",
            batch_parameters={"dataframe": _frame()},
            index_columns=_INDEX_COLUMNS,
            value_signal_gate=_always,
        )
    finally:
        mp.undo()
    sample = outcome.checks[0].sample_failures
    assert sample is not None
    assert sample[VALUE_SIGNAL_SUMMARY_KEY][_TESTED]["email_count"] == _EMAILS


def test_failed_population_query_degrades_honestly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed extra query keeps the capped-sample verdict, records why, and says so."""

    def _unreachable(*_a: Any, **_k: Any) -> Any:
        raise sa.exc.OperationalError("SELECT", {}, Exception("warehouse went away"))

    monkeypatch.setattr(gx_runner, "_batch_validator", _unreachable)
    with capture_logs() as logs:
        check = _run_sql(tmp_path)

    assert check.success is False and check.errored is False
    sample = check.sample_failures
    assert sample is not None
    assert sample[VALUE_SIGNAL_STATUS_KEY] == VALUE_SIGNAL_SAMPLE_FAILED
    assert VALUE_SIGNAL_SUMMARY_KEY not in sample
    assert sample["unexpected_count"] == _HARMLESS_LEAD + _EMAILS
    failures = [e for e in logs if e["event"] == "gx_value_signal_sample_failed"]
    assert failures and failures[0]["log_level"] == "warning"
    assert failures[0]["error_type"] == "OperationalError"

    # Today's 20-value fallback, with the marker consumed rather than rendered or counted.
    redacted, state, _ = _redacted(check)
    assert redacted is not None
    assert VALUE_SIGNAL_STATUS_KEY not in redacted
    assert state == "none"


def test_one_failing_check_does_not_starve_its_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The checks resolve in one batch (#2107); a failure there retries them singly, so one
    check's broken metric still leaves its sibling a summary."""
    real = gx_runner._population_metric
    broken = _CHECK.kwargs["column"]

    def _one_breaks(validator: Any, spec: CheckSpec, index_columns: Any) -> Any:
        if spec.kwargs["column"] == broken:
            raise RuntimeError("transient")
        return real(validator, spec, index_columns)

    monkeypatch.setattr(gx_runner, "_population_metric", _one_breaks)
    other = CheckSpec(_IN_SET, {"column": "customer_id", "value_set": [0]})
    context, batch_definition = _sql_batch(tmp_path, "two", _frame())
    outcome = run_expectations(
        context,
        batch_definition=batch_definition,
        checks=[_CHECK, other],
        name="two",
        index_columns=None,
        value_signal_gate=_always,
    )
    first, second = (check.sample_failures for check in outcome.checks)
    assert first is not None and second is not None
    assert first[VALUE_SIGNAL_STATUS_KEY] == VALUE_SIGNAL_SAMPLE_FAILED
    assert VALUE_SIGNAL_SUMMARY_KEY in second


def test_pending_checks_share_one_population_resolution(tmp_path: Path) -> None:
    """Two pending checks pay ONE shared `table.row_count` COUNT in the population phase,
    not one each (#2107) — GX dedupes a batch's shared dependencies."""
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    other = CheckSpec(_IN_SET, {"column": "customer_id", "value_set": [0]})
    context, batch_definition = _sql_batch(tmp_path, "shared", _frame())
    statements: list[str] = []

    def _record(_c: Any, _cur: Any, statement: str, *_rest: Any) -> None:
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", _record)
    try:
        outcome = run_expectations(
            context,
            batch_definition=batch_definition,
            checks=[_CHECK, other],
            name="shared",
            index_columns=None,
            value_signal_gate=_always,
        )
    finally:
        event.remove(Engine, "before_cursor_execute", _record)

    assert all(
        VALUE_SIGNAL_SUMMARY_KEY in (check.sample_failures or {}) for check in outcome.checks
    )
    population = [s for s in statements if "LIMIT" in s and "unexpected_values" not in s]
    assert len(population) >= 2, statements
    first_population = statements.index(population[0])
    counts_after = [s for s in statements[first_population - 2 :] if "table.row_count" in s]
    assert len(counts_after) == 1, statements[first_population - 2 :]


def test_unity_catalog_pushdown_lane_carries_the_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through `UnityCatalogCheckRunner.run_checks` on its SQL (pushdown) batch — the gate is
    forwarded, not dropped between the runner and `run_expectations`."""
    url = _sqlite_url(tmp_path, "uc", _frame())
    runner = UnityCatalogCheckRunner(
        config=UnityCatalogConfig.model_validate(
            {"workspace_url": "https://adb-1.2.azuredatabricks.net", "warehouse_id": "w1"}
        ),
        token="t",
        catalog="main",
    )

    def _seam(context: Any, *, table: str, schema: str) -> tuple[Any, Any]:
        datasource = context.data_sources.add_sqlite(name="uc-sql", connection_string=url)
        asset = datasource.add_table_asset(name="orders", table_name="orders")
        return datasource, asset.add_batch_definition_whole_table(name="whole_table")

    def _no_frame(**_kwargs: Any) -> Any:
        raise AssertionError("pushdown type must not read a DataFrame")

    monkeypatch.setenv("UC_SQL_PUSHDOWN", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(runner, "_sql_batch_definition", _seam)
    monkeypatch.setattr(runner, "_read_table", _no_frame)
    monkeypatch.setattr(runner, "_count_rows", _no_frame)
    try:
        outcome = runner.run_checks(
            table="orders",
            schema="gold",
            checks=[_CHECK],
            index_columns=_INDEX_COLUMNS,
            value_signal_gate=_always,
        )
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    sample = outcome.checks[0].sample_failures
    assert sample is not None
    assert sample[VALUE_SIGNAL_SUMMARY_KEY][_TESTED]["email_count"] == _EMAILS


# ── the gate: which columns the ladder cannot decide without values ──────────


@pytest.mark.parametrize(
    ("column", "policy", "tags", "decides"),
    [
        ("channel", None, None, True),
        ("customer_id", {"identifier_column": "customer_id"}, None, True),
        ("channel", {"pii_columns": ["CHANNEL"]}, None, False),
        ("channel", None, {"channel": "PII"}, False),
        ("customer_email", None, None, False),
        ("channel", {"require_classification": True}, None, False),
        ("channel", {"require_classification": True}, {"channel": "public"}, True),
        (
            "customer_id",
            {"require_classification": True, "identifier_column": "customer_id"},
            None,
            True,
        ),
        ("channel", None, {"channel": "internal"}, True),
    ],
)
def test_value_signal_decides(
    column: str, policy: dict[str, Any] | None, tags: dict[str, str] | None, decides: bool
) -> None:
    assert value_signal_decides(column, policy, tags) is decides
    assert build_value_signal_gate(policy, tags)(column) is decides


def test_gate_snapshots_policy_at_capture() -> None:
    policy: dict[str, Any] = {"pii_columns": []}
    gate = build_value_signal_gate(policy, None)
    policy["pii_columns"].append("channel")
    policy["require_classification"] = True
    assert gate("channel") is True


@pytest.mark.parametrize("count", [Decimal("500"), np.int64(500), 500.0, 500])
def test_any_numeric_unexpected_count_opens_the_gate(count: Any) -> None:
    outcome = CheckOutcome(_IN_SET, success=False, sample_failures={"unexpected_count": count})
    assert gx_runner._needs_population_signal(_CHECK, outcome, None, _always) is True


@pytest.mark.parametrize("count", [True, None, "500", Decimal("20"), SAMPLE_ROW_CAP])
def test_non_numeric_or_capped_count_keeps_the_gate_shut(count: Any) -> None:
    outcome = CheckOutcome(_IN_SET, success=False, sample_failures={"unexpected_count": count})
    assert gx_runner._needs_population_signal(_CHECK, outcome, None, _always) is False
