"""Tests for the Teams Adaptive Card renderer (pure — no I/O)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from backend.app.alerting import card
from backend.app.alerting.base import CheckReport, RunReport


def _report(
    *,
    checks: list[CheckReport] | None = None,
    counts: dict[str, int] | None = None,
    run_status: str = "succeeded",
    worst: str | None = "fail",
) -> RunReport:
    return RunReport(
        run_id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        suite_name="Orders QA",
        run_status=run_status,
        datasource_type="snowflake",
        target_label="RETAIL.ORDERS",
        worst_severity=worst,
        counts=counts or {"pass": 1, "fail": 1},
        checks=checks or [],
        finished_at=datetime(2026, 6, 26, 12, 0, tzinfo=UTC),
    )


def _check(status: str = "fail", **kw: object) -> CheckReport:
    base: dict[str, object] = {
        "check_name": "not-null id",
        "expectation_type": "expect_column_values_to_not_be_null",
        "status": status,
        "metric_value": 12.5,
        "observed_value": {"unexpected_percent": 12.5},
        "expected_value": {"column": "id"},
        "sample_summary": {"unexpected_count": 3},
    }
    base.update(kw)
    return CheckReport(**base)  # type: ignore[arg-type]


def test_message_wraps_an_adaptive_card() -> None:
    msg = card.render_teams_message(_report())
    assert msg["type"] == "message"
    (attachment,) = msg["attachments"]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    content = attachment["content"]
    assert content["type"] == "AdaptiveCard"
    assert content["version"] == "1.4"


def test_card_carries_datasource_target_and_severity() -> None:
    content = card.render_teams_message(_report())["attachments"][0]["content"]
    factset = next(b for b in content["body"] if b["type"] == "FactSet")
    facts = {f["title"]: f["value"] for f in factset["facts"]}
    assert facts["Datasource"] == "snowflake"
    assert facts["Target"] == "RETAIL.ORDERS"
    assert facts["Severity"] == "fail"
    assert facts["Finished"] == "2026-06-26T12:00:00+00:00"


def test_card_lists_failing_checks_with_observed_vs_expected() -> None:
    content = card.render_teams_message(
        _report(checks=[_check(status="fail"), _check(status="pass")])
    )["attachments"][0]["content"]
    texts = [b.get("text", "") for b in content["body"] if b["type"] == "TextBlock"]
    blob = "\n".join(texts)
    # The failing check is rendered; the passing one is not listed.
    assert "not-null id" in blob
    assert "observed unexpected_percent=12.5" in blob
    assert "expected column=id" in blob
    assert "3 rows" in blob  # the redacted sample count
    # Exactly one failing-check block (the passing check is excluded).
    assert sum("not-null id" in t for t in texts) == 1


def test_operational_failure_has_no_check_tally() -> None:
    content = card.render_teams_message(
        _report(run_status="failed", checks=[], counts={}, worst=None)
    )["attachments"][0]["content"]
    texts = [b.get("text", "") for b in content["body"] if b["type"] == "TextBlock"]
    assert any("Run failed to execute" in t for t in texts)


def test_check_overflow_is_summarised() -> None:
    content = card.render_teams_message(_report(checks=[_check() for _ in range(13)]))[
        "attachments"
    ][0]["content"]
    texts = [b.get("text", "") for b in content["body"] if b["type"] == "TextBlock"]
    # 10 rows rendered + a "+3 more".
    assert sum("not-null id" in t for t in texts) == 10
    assert any("+3 more" in t for t in texts)


def test_compact_handles_empty() -> None:
    assert card._compact(None) == "—"
    assert card._compact({}) == "—"
    assert card._compact({"a": 1, "b": 2}) == "a=1, b=2"
