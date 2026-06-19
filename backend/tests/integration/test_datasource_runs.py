"""End-to-end suite runs across the three datasource runner types.

Real `run_service.execute_run` + real Postgres persistence + (for flat-file and
Unity Catalog) **real GX execution** on an in-memory DataFrame — the whole path:
`check.kind` dispatch → runner → GX validation → severity derivation → `Result`
rows. Only the file download / table read is stubbed with a canned frame; the
warehouse runners' live connect is the deferred-smoke seam, so Snowflake runs
through a canned outcome (its GX result-mapping is covered in
`datasources/test_snowflake.py`).

Skips without `TEST_DATABASE_URL` (the `db_session` fixture).
"""

import uuid
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import select

from backend.app.datasources import flatfile
from backend.app.datasources.base import CheckOutcome, CheckSpec, SuiteOutcome
from backend.app.datasources.unity_catalog import UnityCatalogCheckRunner, UnityCatalogConfig
from backend.app.db.models import Check, Connection, Result, Run, Suite, User
from backend.app.services import run_service
from backend.tests.support.adversarial import ADVERSARIAL_FRAMES

# Two checks every datasource test reuses: a column null-check (fails on a null)
# and a table row-count check (column-agnostic, passes).
_CHECKS = [
    {
        "name": "id_notnull",
        "type": "expect_column_values_to_not_be_null",
        "config": {"column": "id"},
    },
    {
        "name": "rowcount",
        "type": "expect_table_row_count_to_be_between",
        "config": {"min_value": 1, "max_value": 100},
    },
]


def _seed(
    db_session: Any,
    *,
    conn_type: str,
    config: dict[str, Any],
    checks_spec: list[dict[str, Any]] = _CHECKS,
) -> tuple[Suite, list[Check]]:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"{conn_type}@ex")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"{conn_type}-{uuid.uuid4().hex[:6]}",
        type=conn_type,
        env="dev",
        config=config,
        secret_ref="kv-ref",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name="s", connection_id=conn.id, created_by=owner.id)
    db_session.add(suite)
    db_session.flush()
    checks = [
        Check(
            suite_id=suite.id,
            name=c["name"],
            kind="expectation",
            expectation_type=c["type"],
            config=c["config"],
        )
        for c in checks_spec
    ]
    db_session.add_all(checks)
    db_session.flush()
    return suite, checks


def _queued_run(db_session: Any, suite: Suite) -> Run:
    run = Run(suite_id=suite.id, status="queued")
    db_session.add(run)
    db_session.commit()
    return run


def _assert_persisted(db_session: Any, run: Run, checks: list[Check]) -> None:
    """The canonical end-to-end assertion: run succeeded, both Results persisted,
    severity derived (null-check fails, row-count passes)."""
    assert run.status == "succeeded"
    results = db_session.scalars(select(Result).where(Result.run_id == run.id)).all()
    by_check = {r.check_id: r for r in results}
    assert len(results) == 2
    # the column has a null → GX null-check fails → binary fallback status 'fail'
    assert by_check[checks[0].id].status == "fail"
    # 3 rows, within [1, 100] → pass, observed row count persisted
    assert by_check[checks[1].id].status == "pass"
    assert by_check[checks[1].id].observed_value == {"observed_value": 3}


_SAMPLE = pd.DataFrame({"id": [1, 2, None], "amt": [10, 20, 30]})  # one null in 'id'


# ───────────────────────── flat-file (real GX) ─────────────────────


def test_flatfile_suite_run_persists_results(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, checks = _seed(db_session, conn_type="s3", config={"bucket": "b", "region": "r"})
    run = _queued_run(db_session, suite)
    monkeypatch.setattr(flatfile, "read_dataframe", lambda **k: _SAMPLE)
    runner = flatfile.FlatFileCheckRunner(conn_type="s3", config={}, secret="x")

    run_service.execute_run(db_session, run=run, checks=checks, runner=runner, table="data/o.csv")
    _assert_persisted(db_session, run, checks)


def test_flatfile_run_persists_error_status_for_unevaluable_check(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end (#122): a check GX can't evaluate (missing column) persists as
    `error` — not `fail` — while its sibling persists normally and the run still
    succeeds. Also proves the `results.status` CHECK constraint accepts `error`."""
    checks_spec = [
        {
            "name": "missing_col",
            "type": "expect_column_values_to_not_be_null",
            "config": {"column": "does_not_exist"},
        },
        {
            "name": "rowcount",
            "type": "expect_table_row_count_to_be_between",
            "config": {"min_value": 1, "max_value": 100},
        },
    ]
    suite, checks = _seed(
        db_session, conn_type="s3", config={"bucket": "b", "region": "r"}, checks_spec=checks_spec
    )
    run = _queued_run(db_session, suite)
    monkeypatch.setattr(flatfile, "read_dataframe", lambda **k: _SAMPLE)
    runner = flatfile.FlatFileCheckRunner(conn_type="s3", config={}, secret="x")

    run_service.execute_run(db_session, run=run, checks=checks, runner=runner, table="data/o.csv")

    assert run.status == "succeeded"
    results = db_session.scalars(select(Result).where(Result.run_id == run.id)).all()
    by_check = {r.check_id: r for r in results}
    assert by_check[checks[0].id].status == "error"  # unevaluable → error (persisted by Postgres)
    assert by_check[checks[0].id].metric_value is None
    assert by_check[checks[1].id].status == "pass"  # sibling unaffected


# ───────────────────────── Unity Catalog (real GX) ─────────────────


def test_unity_catalog_suite_run_persists_results(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = {"workspace_url": "https://adb-1.2.azuredatabricks.net", "warehouse_id": "w1"}
    suite, checks = _seed(db_session, conn_type="unity_catalog", config=cfg)
    run = _queued_run(db_session, suite)
    runner = UnityCatalogCheckRunner(
        config=UnityCatalogConfig.model_validate(cfg), token="t", catalog="main"
    )
    monkeypatch.setattr(runner, "_read_table", lambda **k: _SAMPLE)

    run_service.execute_run(
        db_session, run=run, checks=checks, runner=runner, table="orders", schema="sales"
    )
    _assert_persisted(db_session, run, checks)


# ───────────────────────── Snowflake (canned; live deferred) ───────


class _CannedRunner:
    """Stands in for the warehouse-bound Snowflake runner (no live connect)."""

    def run_checks(
        self, *, table: str, schema: str | None, checks: list[CheckSpec]
    ) -> SuiteOutcome:
        return SuiteOutcome(
            success=False,
            checks=[
                CheckOutcome(
                    "expect_column_values_to_not_be_null",
                    success=False,
                    sample_failures={"unexpected_percent": 33.3},
                ),
                CheckOutcome(
                    "expect_table_row_count_to_be_between",
                    success=True,
                    observed_value={"observed_value": 3},
                ),
            ],
        )


def test_snowflake_suite_run_persists_results(db_session: Any) -> None:
    cfg = {"account": "ab1", "user": "u", "database": "d", "schema": "s", "warehouse": "w"}
    suite, checks = _seed(db_session, conn_type="snowflake", config=cfg)
    run = _queued_run(db_session, suite)

    run_service.execute_run(
        db_session, run=run, checks=checks, runner=_CannedRunner(), table="ORDERS", schema="PUBLIC"
    )
    _assert_persisted(db_session, run, checks)


def test_skip_run_persists_skip_results(db_session: Any) -> None:
    """End to end (#122): a run with nothing to evaluate (e.g. the target batch
    hasn't landed) persists a `skip` Result per check and succeeds. Proves the
    `results.status` CHECK constraint accepts `skip`."""
    suite, checks = _seed(db_session, conn_type="s3", config={"bucket": "b", "region": "r"})
    run = _queued_run(db_session, suite)

    run_service.skip_run(db_session, run=run, checks=checks, reason="batch_not_found")

    assert run.status == "succeeded"
    results = db_session.scalars(select(Result).where(Result.run_id == run.id)).all()
    assert len(results) == 2
    assert all(r.status == "skip" for r in results)
    assert all(r.observed_value == {"reason": "batch_not_found"} for r in results)


# ───────────────────────── adversarial robustness ──────────────────


@pytest.mark.parametrize(
    ("name", "frame"),
    # a representative hostile subset — numpy + pyarrow backends, exotic scalars
    [
        c
        for c in ADVERSARIAL_FRAMES
        if c[0] in {"mixed_int_str", "arrow_struct", "bytes_values", "empty_rows"}
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_flatfile_run_survives_adversarial_frame(
    db_session: Any, monkeypatch: pytest.MonkeyPatch, name: str, frame: pd.DataFrame
) -> None:
    # a column-agnostic row-count monitor must still run + persist over a file
    # whose data column is hostile — the run path is robust end to end.
    row_count_only = [
        {
            "name": "rowcount",
            "type": "expect_table_row_count_to_be_between",
            "config": {"min_value": 0, "max_value": 10**9},
        }
    ]
    suite, checks = _seed(
        db_session,
        conn_type="s3",
        config={"bucket": "b", "region": "r"},
        checks_spec=row_count_only,
    )
    run = _queued_run(db_session, suite)

    monkeypatch.setattr(flatfile, "read_dataframe", lambda **k: frame)
    runner = flatfile.FlatFileCheckRunner(conn_type="s3", config={}, secret="x")
    run_service.execute_run(db_session, run=run, checks=checks, runner=runner, table="f.parquet")

    assert run.status == "succeeded"
    results = db_session.scalars(select(Result).where(Result.run_id == run.id)).all()
    assert len(results) == 1 and results[0].status == "pass"
