"""Tests for the run-report builder + the report DTO.

Two layers: pure helpers / DTO properties (no DB), and the DB-backed assembly —
joining results to checks, deriving worst-severity + success, the target label,
and (critically) that ``sample_failures`` raw rows are redacted at the seam.
Skips the DB layer without TEST_DATABASE_URL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from backend.app.alerting import builder
from backend.app.alerting.base import CheckReport, RunReport
from backend.app.db.models import Check, Connection, Result, Run, Suite, User

# ── pure: DTO derived properties ─────────────────────────────────────────────


def _report(
    counts: dict[str, int], *, run_status: str = "succeeded", worst: str | None = None
) -> RunReport:
    return RunReport(
        run_id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        suite_name="s",
        run_status=run_status,
        datasource_type="snowflake",
        target_label="DB.SCHEMA.T",
        worst_severity=worst,
        counts=counts,
        checks=[],
        finished_at=None,
    )


def test_total_and_failed_counts() -> None:
    rep = _report({"pass": 2, "warn": 1, "fail": 1, "critical": 2}, worst="critical")
    assert rep.total_checks == 6
    # failed = fail + critical only (warn surfaces via worst_severity)
    assert rep.failed_checks == 3


def test_has_failures_true_on_warn_only() -> None:
    assert _report({"pass": 3, "warn": 1}, worst="warn").has_failures is True


def test_has_failures_false_when_all_pass() -> None:
    assert _report({"pass": 4}).has_failures is False


def test_has_failures_true_on_run_failed_with_no_results() -> None:
    # An operational run failure (adapter raised) has no result rows at all.
    assert _report({}, run_status="failed").has_failures is True


def test_skip_and_error_do_not_count_as_failures() -> None:
    rep = _report({"pass": 1, "skip": 2, "error": 1})
    assert rep.failed_checks == 0
    assert rep.has_failures is False


# ── pure: helpers ────────────────────────────────────────────────────────────


def test_worst_severity_orders_critical_over_fail_over_warn() -> None:
    assert builder._worst_severity(["pass", "warn", "fail", "critical"]) == "critical"
    assert builder._worst_severity(["pass", "warn", "fail"]) == "fail"
    assert builder._worst_severity(["pass", "warn"]) == "warn"


def test_worst_severity_none_when_clean_or_operational() -> None:
    assert builder._worst_severity(["pass", "pass"]) is None
    assert builder._worst_severity(["skip", "error"]) is None
    assert builder._worst_severity([]) is None


def test_target_label_prefers_path_then_dotted() -> None:
    assert builder._target_label(cast(Suite, _FakeSuite({"path": "abfss://c/landing/x.csv"}))) == (
        "abfss://c/landing/x.csv"
    )
    assert (
        builder._target_label(
            cast(Suite, _FakeSuite({"catalog": "C", "schema": "S", "table": "T"}))
        )
        == "C.S.T"
    )
    assert builder._target_label(cast(Suite, _FakeSuite({"schema": "S", "table": "T"}))) == "S.T"
    assert builder._target_label(cast(Suite, _FakeSuite(None))) == "(no target)"
    assert builder._target_label(None) == "(no target)"


class _FakeSuite:
    def __init__(self, target: dict[str, Any] | None) -> None:
        self.target = target


# ── DB-backed: assembly + redaction ──────────────────────────────────────────


def _suite_with_check(
    db: Any, *, status: str, sample: dict[str, Any] | None = None
) -> tuple[Suite, Run]:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@x.io")
    db.add(owner)
    db.flush()
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a"},
        secret_ref="kv",
        created_by=owner.id,
    )
    db.add(conn)
    db.flush()
    suite = Suite(
        name="Orders QA",
        connection_id=conn.id,
        created_by=owner.id,
        target={"schema": "RETAIL", "table": "ORDERS"},
    )
    db.add(suite)
    db.flush()
    check = Check(
        suite_id=suite.id,
        name="not-null id",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "id"},
    )
    db.add(check)
    db.flush()
    run = Run(suite_id=suite.id, status="succeeded", finished_at=datetime.now(UTC))
    db.add(run)
    db.flush()
    db.add(
        Result(
            run_id=run.id,
            check_id=check.id,
            status=status,
            metric_value=12.5,
            observed_value={"unexpected_percent": 12.5},
            expected_value={"column": "id"},
            sample_failures=sample,
        )
    )
    db.commit()
    return suite, run


def test_build_report_maps_check_and_metric(db_session: Any) -> None:
    _suite, run = _suite_with_check(db_session, status="fail")

    report = builder.build_run_report(db_session, run)

    assert report.suite_name == "Orders QA"
    assert report.datasource_type == "snowflake"
    assert report.target_label == "RETAIL.ORDERS"
    assert report.run_status == "succeeded"
    assert report.success is False  # a failing check → not a success
    assert report.worst_severity == "fail"
    assert report.counts == {"fail": 1}
    assert len(report.checks) == 1
    only = report.checks[0]
    assert only.check_name == "not-null id"
    assert only.expectation_type == "expect_column_values_to_not_be_null"
    assert isinstance(only.metric_value, float) and only.metric_value == 12.5


def test_build_report_redacts_sample_rows(db_session: Any) -> None:
    raw = {
        "unexpected_count": 2,
        "unexpected_percent": 50.0,
        "partial_unexpected_list": ["alice@secret.com", "bob@secret.com"],
    }
    _suite, run = _suite_with_check(db_session, status="critical", sample=raw)

    report = builder.build_run_report(db_session, run)
    summary = report.checks[0].sample_summary
    assert summary is not None
    # Aggregate counts survive…
    assert summary["unexpected_count"] == 2
    assert summary["unexpected_percent"] == 50.0
    # …the raw cell values are masked (length kept, content gone — no PII leaves).
    assert summary["partial_unexpected_list"] == ["<redacted>", "<redacted>"]
    assert "secret.com" not in str(summary)


def test_build_report_all_pass_is_success(db_session: Any) -> None:
    _suite, run = _suite_with_check(db_session, status="pass")
    report = builder.build_run_report(db_session, run)
    assert report.success is True
    assert report.worst_severity is None
    assert report.has_failures is False


def test_check_report_is_frozen() -> None:
    rep = CheckReport(
        check_name="c",
        expectation_type="e",
        status="pass",
        metric_value=None,
        observed_value=None,
        expected_value=None,
        sample_summary=None,
    )
    try:
        rep.status = "fail"  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("CheckReport should be immutable")
