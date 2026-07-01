"""Tests for the run/result persistence service.

No database or GX: a fake Session records what would be persisted, model
instances are built in memory with explicit ids, and a fake CheckRunner returns
canned outcomes (or raises). This keeps the service's lifecycle + mapping logic
under test independent of Postgres and Snowflake.
"""

import uuid
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy.orm import Session

from backend.app.datasources.base import CheckOutcome, CheckRunner, CheckSpec, SuiteOutcome
from backend.app.db.models import Check, Result, Run
from backend.app.services import run_service


class FakeSession:
    """Records add_all'd rows; counts commits/rollbacks. `add_all_raises` simulates
    a persistence failure (e.g. DB error) after the adapter has already run."""

    def __init__(
        self, *, add_all_raises: Exception | None = None, refresh_status: str | None = None
    ) -> None:
        self.added: list[Result] = []
        self.commits = 0
        self.rollbacks = 0
        self._add_all_raises = add_all_raises
        # When set, refresh() stamps this onto the refreshed object's `status`,
        # simulating a concurrent cancel that committed from another session.
        self._refresh_status = refresh_status

    def add_all(self, rows: list[Result]) -> None:
        if self._add_all_raises is not None:
            raise self._add_all_raises
        self.added.extend(rows)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1
        self.added.clear()  # discard staged-but-uncommitted rows, like a real rollback

    def refresh(self, obj: object) -> None:
        if self._refresh_status is not None:
            obj.status = self._refresh_status  # type: ignore[attr-defined]


def _sess(session: FakeSession) -> Session:
    """Type a ``FakeSession`` test double as ``Session`` for the service signatures
    (the tests still hold the ``FakeSession`` ref for their `.added`/`.commits`
    assertions; only the call arg is cast)."""
    return cast(Session, session)


class FakeRunner:
    def __init__(
        self, outcome: SuiteOutcome | None = None, raises: Exception | None = None
    ) -> None:
        self._outcome = outcome
        self._raises = raises
        self.called_with: dict[str, object] | None = None

    def run_checks(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[CheckSpec],
        index_columns: list[str] | None = None,
    ) -> SuiteOutcome:
        self.called_with = {
            "table": table,
            "schema": schema,
            "checks": checks,
            "index_columns": index_columns,
        }
        if self._raises is not None:
            raise self._raises
        assert self._outcome is not None
        return self._outcome


def _run() -> Run:
    return Run(id=uuid.uuid4(), suite_id=uuid.uuid4(), status="queued")


def _checks(n: int) -> list[Check]:
    return [
        Check(
            id=uuid.uuid4(),
            suite_id=uuid.uuid4(),
            name=f"c{i}",
            kind="expectation",
            expectation_type="x",
            config={},
        )
        for i in range(n)
    ]


def _monitor_check(kind: str, config: dict[str, object]) -> Check:
    return Check(
        id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        name=kind,
        kind=kind,
        expectation_type="",
        config=config,
    )


class FakeMonitorRunner:
    """A SQL-datasource-like runner that handles both expectation (run_checks) and
    monitor (run_monitors) kinds — so the kind-dispatch can route to each."""

    def __init__(
        self, *, check_outcomes: list[CheckOutcome], monitor_outcomes: list[CheckOutcome]
    ) -> None:
        self._check_outcomes = check_outcomes
        self._monitor_outcomes = monitor_outcomes
        self.monitors_called_with: list[object] | None = None

    def run_checks(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[CheckSpec],
        index_columns: list[str] | None = None,
    ) -> SuiteOutcome:
        return SuiteOutcome(success=True, checks=self._check_outcomes)

    def run_monitors(
        self, *, table: str, schema: str | None, monitors: list[object]
    ) -> list[CheckOutcome]:
        self.monitors_called_with = monitors
        return self._monitor_outcomes


# ───────────────────────── kind dispatch (_run_outcomes) ─────────────


def test_run_outcomes_routes_by_kind_and_keeps_check_order() -> None:
    # checks interleaved: [expectation, freshness, expectation] — outcomes must come
    # back in that same order (so they zip 1:1 onto the result rows).
    checks = [_checks(1)[0], _monitor_check("freshness", {"column": "ts"}), _checks(1)[0]]
    runner = FakeMonitorRunner(
        check_outcomes=[CheckOutcome("e1", success=True), CheckOutcome("e2", success=True)],
        monitor_outcomes=[CheckOutcome("monitor:freshness", success=True, metric_value=5.0)],
    )

    outcomes = run_service._run_outcomes(
        cast(CheckRunner, runner), table="T", schema=None, checks=checks
    )

    assert [o.expectation_type for o in outcomes] == ["e1", "monitor:freshness", "e2"]
    assert runner.monitors_called_with is not None and len(runner.monitors_called_with) == 1


def test_run_outcomes_monitor_on_non_sql_runner_raises() -> None:
    # FakeRunner has no run_monitors → not a MonitorRunner → monitor check rejected
    # (freshness/volume need a SQL datasource).
    runner = FakeRunner(outcome=SuiteOutcome(success=True, checks=[]))
    with pytest.raises(NotImplementedError, match="monitor"):
        run_service._run_outcomes(
            runner,
            table="T",
            schema=None,
            checks=[_monitor_check("volume", {"min_rows": 1, "max_rows": 9})],
        )


def test_run_outcomes_unsupported_kind_raises() -> None:
    runner = FakeRunner(outcome=SuiteOutcome(success=True, checks=[]))
    with pytest.raises(NotImplementedError, match="schema_drift"):
        run_service._run_outcomes(
            runner, table="T", schema=None, checks=[_monitor_check("schema_drift", {})]
        )


# ───────────────────────── success path ────────────────────────────


def test_successful_run_persists_results_and_marks_succeeded() -> None:
    session = FakeSession()
    run = _run()
    checks = _checks(2)
    outcome = SuiteOutcome(
        success=False,  # a check failed, but the RUN still executed
        checks=[
            CheckOutcome("expect_a", success=True, observed_value={"observed_value": 5}),
            CheckOutcome(
                "expect_b",
                success=False,
                expected_value={"column": "id"},
                sample_failures={"unexpected_count": 1},
            ),
        ],
    )
    runner = FakeRunner(outcome=outcome)

    result = run_service.execute_run(
        _sess(session), run=run, checks=checks, runner=runner, table="ORDERS", schema="FIN"
    )

    assert result is run
    assert run.status == "succeeded"  # ran to completion despite a failed check
    assert run.started_at is not None and run.finished_at is not None
    assert len(session.added) == 2
    statuses = {r.check_id: r.status for r in session.added}
    assert statuses[checks[0].id] == "pass"
    assert statuses[checks[1].id] == "fail"
    # adapter received specs derived from the checks + the target table
    assert runner.called_with == {
        "table": "ORDERS",
        "schema": "FIN",
        "checks": [CheckSpec("x", {}), CheckSpec("x", {})],
        "index_columns": None,
    }


def test_results_link_to_run_and_check_ids() -> None:
    session = FakeSession()
    run = _run()
    checks = _checks(1)
    runner = FakeRunner(SuiteOutcome(success=True, checks=[CheckOutcome("x", success=True)]))

    run_service.execute_run(_sess(session), run=run, checks=checks, runner=runner, table="T")

    (row,) = session.added
    assert row.run_id == run.id
    assert row.check_id == checks[0].id


# ───────────────────────── NaN sanitisation ────────────────────────


def test_nan_in_sample_failures_is_sanitised_before_persist() -> None:
    session = FakeSession()
    runner = FakeRunner(
        SuiteOutcome(
            success=False,
            checks=[
                CheckOutcome(
                    "x",
                    success=False,
                    sample_failures={"partial_unexpected_list": [float("nan"), 2.0]},
                )
            ],
        )
    )

    run_service.execute_run(_sess(session), run=_run(), checks=_checks(1), runner=runner, table="T")

    (row,) = session.added
    assert row.sample_failures == {"partial_unexpected_list": [None, 2.0]}


# ───────────────────────── failure path ────────────────────────────


def test_runner_exception_marks_failed_and_persists_no_results() -> None:
    session = FakeSession()
    run = _run()
    runner = FakeRunner(raises=RuntimeError("cannot reach warehouse"))

    result = run_service.execute_run(
        _sess(session), run=run, checks=_checks(2), runner=runner, table="T"
    )

    assert result.status == "failed"
    assert run.finished_at is not None
    assert session.added == []  # no half-written results


def test_persistence_failure_marks_failed_not_stuck_running() -> None:
    """If add_all/commit fails after a successful run, the run must reach a
    terminal 'failed' state (not stay 'running') and roll back partial inserts."""
    session = FakeSession(add_all_raises=RuntimeError("db connection lost"))
    run = _run()
    runner = FakeRunner(SuiteOutcome(success=True, checks=[CheckOutcome("x", success=True)]))

    result = run_service.execute_run(
        _sess(session), run=run, checks=_checks(1), runner=runner, table="T"
    )

    assert result.status == "failed"
    assert run.finished_at is not None
    assert session.rollbacks == 1


def test_outcome_count_mismatch_marks_failed() -> None:
    """zip(strict=True): if the adapter returns the wrong number of outcomes."""
    session = FakeSession()
    runner = FakeRunner(SuiteOutcome(success=True, checks=[CheckOutcome("x", success=True)]))

    run_service.execute_run(_sess(session), run=_run(), checks=_checks(3), runner=runner, table="T")

    assert session.added == []


def test_empty_run_still_succeeds() -> None:
    session = FakeSession()
    run = _run()
    runner = FakeRunner(SuiteOutcome(success=True, checks=[]))

    run_service.execute_run(_sess(session), run=run, checks=[], runner=runner, table="T")

    assert run.status == "succeeded"
    assert session.added == []


def test_thresholds_derive_tier_and_persist_metric() -> None:
    """execute_run wires severity post-processing (ADR 0016): the unexpected-%
    is banded against the check's thresholds and persisted as metric_value."""
    session = FakeSession()
    run = _run()
    check = Check(
        id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        name="c",
        kind="expectation",
        expectation_type="x",
        config={},
        warn_threshold=Decimal("1"),
        fail_threshold=Decimal("5"),
        critical_threshold=Decimal("20"),
    )
    outcome = SuiteOutcome(
        success=False,
        checks=[CheckOutcome("x", success=False, sample_failures={"unexpected_percent": 7.5})],
    )
    run_service.execute_run(
        _sess(session), run=run, checks=[check], runner=FakeRunner(outcome=outcome), table="T"
    )

    persisted = session.added[0]
    assert persisted.status == "fail"  # 7.5 ≥ fail(5), < critical(20)
    assert persisted.metric_value == Decimal("7.5")


def test_errored_check_maps_to_error_status_without_failing_siblings() -> None:
    """A check the runner could not evaluate (`outcome.errored`) is an operational
    `error` result (#122) — no severity, no metric — and never fails its siblings:
    the sibling still maps to its tier and the RUN still succeeds."""
    session = FakeSession()
    run = _run()
    checks = _checks(2)
    outcome = SuiteOutcome(
        success=False,  # GX marks the suite failed because one check raised
        checks=[
            CheckOutcome(
                "expect_bad",
                success=False,
                errored=True,
                error_message='Error: The column "nope" in BatchData does not exist.',
            ),
            CheckOutcome("expect_ok", success=True, observed_value={"observed_value": 3}),
        ],
    )

    result = run_service.execute_run(
        _sess(session), run=run, checks=checks, runner=FakeRunner(outcome=outcome), table="T"
    )

    assert result.status == "succeeded"  # an errored check doesn't fail the run
    by_check = {r.check_id: r for r in session.added}
    errored = by_check[checks[0].id]
    assert errored.status == "error"  # not 'fail' — it never evaluated
    assert errored.metric_value is None
    assert errored.observed_value == {"error": outcome.checks[0].error_message}
    assert by_check[checks[1].id].status == "pass"  # sibling unaffected


def test_errored_check_with_thresholds_is_still_error_not_banded() -> None:
    """Thresholds don't apply to an errored check — there's no metric to band, so
    it must resolve to `error`, not slip through severity derivation as a tier."""
    session = FakeSession()
    run = _run()
    check = Check(
        id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        name="c",
        kind="expectation",
        expectation_type="x",
        config={},
        warn_threshold=Decimal("1"),
        fail_threshold=Decimal("5"),
        critical_threshold=Decimal("20"),
    )
    outcome = SuiteOutcome(success=False, checks=[CheckOutcome("x", success=False, errored=True)])

    run_service.execute_run(
        _sess(session), run=run, checks=[check], runner=FakeRunner(outcome=outcome), table="T"
    )

    persisted = session.added[0]
    assert persisted.status == "error"
    assert persisted.metric_value is None
    assert persisted.observed_value is None  # no message → no observed payload


def test_skip_run_marks_all_checks_skip_and_run_succeeded() -> None:
    """skip_run (#122) records a `skip` Result per check without an adapter run,
    and the run succeeds — it executed, it just had nothing to validate."""
    session = FakeSession()
    run = _run()
    checks = _checks(3)

    result = run_service.skip_run(_sess(session), run=run, checks=checks, reason="batch_not_found")

    assert result.status == "succeeded"
    assert run.started_at is not None and run.finished_at is not None
    assert len(session.added) == 3
    assert all(r.status == "skip" for r in session.added)
    assert all(r.observed_value == {"reason": "batch_not_found"} for r in session.added)
    assert all(r.metric_value is None for r in session.added)


def test_cancel_during_execution_keeps_cancelled_and_persists_no_results() -> None:
    """If a cancel commits while GX is running, the worker must not overwrite it
    with a terminal success: refresh() sees the 'cancelled' status, the moot
    results are rolled back, and the run stays cancelled (A2 cooperative guard)."""
    session = FakeSession(refresh_status="cancelled")  # a concurrent cancel landed
    run = _run()
    checks = _checks(1)
    outcome = SuiteOutcome(success=True, checks=[CheckOutcome("x", success=True)])

    result = run_service.execute_run(
        _sess(session), run=run, checks=checks, runner=FakeRunner(outcome=outcome), table="T"
    )

    assert result.status == "cancelled"  # not 'succeeded' — cancel wins
    assert session.added == []  # staged results rolled back, nothing persisted
    assert session.rollbacks >= 1


def test_cancel_during_execution_that_also_errors_stays_cancelled() -> None:
    """A run cancelled mid-flight that then ALSO raises must stay 'cancelled', not
    be masked as 'failed' (the cooperative guard applies on the failure path too)."""
    session = FakeSession(refresh_status="cancelled")  # cancel landed during the run
    run = _run()
    runner = FakeRunner(raises=RuntimeError("warehouse dropped mid-run"))

    result = run_service.execute_run(
        _sess(session), run=run, checks=_checks(1), runner=runner, table="T"
    )

    assert result.status == "cancelled"  # not 'failed'
    assert session.added == []


def test_non_expectation_kind_fails_run_without_invoking_runner() -> None:
    """A reserved (non-expectation) check kind has no runner in v1 (ADR 0012):
    the run fails loudly rather than silently feeding it to GX, and the adapter
    is never called."""
    session = FakeSession()
    run = _run()
    freshness = Check(
        id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        name="stale_load",
        kind="freshness",  # constraint-valid, but no runner in v1
        expectation_type="",
        config={"interval_hours": 24},
    )
    runner = FakeRunner(SuiteOutcome(success=True, checks=[]))

    result = run_service.execute_run(
        _sess(session), run=run, checks=[freshness], runner=runner, table="T"
    )

    assert result.status == "failed"  # NotImplementedError → terminal 'failed'
    assert runner.called_with is None  # dispatch short-circuited before the adapter
    assert session.added == []  # nothing persisted
    assert session.rollbacks == 1


# ── redact_sample_failures (#226) ─────────────────────────────────────────────


def test_redact_sample_failures_none_and_empty_pass_through() -> None:
    assert run_service.redact_sample_failures(None) is None
    assert run_service.redact_sample_failures({}) is None


def test_redact_sample_failures_keeps_counts_classifies_row_dicts() -> None:
    # A dict-shaped list is redacted per column by the classifier: the `id` locator is
    # shown, the `ssn` PII masked (counts always kept).
    out = run_service.redact_sample_failures(
        {
            "unexpected_count": 3,
            "unexpected_percent": 12.5,
            "partial_unexpected_list": [{"id": 1, "ssn": "111-22-3333"}],
        }
    )
    assert out == {
        "unexpected_count": 3,
        "unexpected_percent": 12.5,
        "partial_unexpected_list": [{"id": 1, "ssn": "<redacted>"}],
    }


def test_redact_sample_failures_masks_scalar_list_preserving_length() -> None:
    # Column-values expectations yield a flat list of raw cell values, not dicts.
    out = run_service.redact_sample_failures(
        {"partial_unexpected_list": ["a@x.com", "b@y.com", "c@z.com"]}
    )
    assert out == {"partial_unexpected_list": ["<redacted>", "<redacted>", "<redacted>"]}


def test_redact_sample_failures_masks_unknown_keys_and_nested_values() -> None:
    # Any non-summary key is treated as data and fully masked, including nesting.
    out = run_service.redact_sample_failures(
        {"unexpected_index_list": [{"row": {"name": "Alice"}}]}
    )
    assert out == {"unexpected_index_list": [{"row": {"name": "<redacted>"}}]}


# ── column-aware redaction (#415) ─────────────────────────────────────────────


def test_redact_surfaces_non_pii_tested_column_values() -> None:
    # The whole point: a non-PII tested column's failing values are now shown.
    out = run_service.redact_sample_failures(
        {"unexpected_count": 2, "partial_unexpected_list": [-12.5, -5.0]},
        tested_column="LINE_TOTAL",
    )
    assert out == {"unexpected_count": 2, "partial_unexpected_list": [-12.5, -5.0]}


def test_redact_masks_pii_tested_column_by_name_heuristic() -> None:
    # A PII-looking tested column stays masked even when it's the tested column.
    out = run_service.redact_sample_failures(
        {"partial_unexpected_list": ["a@x.com", "b@y.com"]},
        tested_column="CUSTOMER_EMAIL",
    )
    assert out == {"partial_unexpected_list": ["<redacted>", "<redacted>"]}


def test_redact_masks_tested_column_listed_in_policy_pii() -> None:
    # An explicit policy PII list masks a column the heuristic wouldn't catch.
    out = run_service.redact_sample_failures(
        {"partial_unexpected_list": [42, 43]},
        tested_column="SALARY",
        policy={"pii_columns": ["SALARY"]},
    )
    assert out == {"partial_unexpected_list": ["<redacted>", "<redacted>"]}


def test_redact_index_list_shows_identifier_and_tested_masks_rest() -> None:
    # Row-dicts: identifier + tested column shown, PII + unclassified masked.
    out = run_service.redact_sample_failures(
        {
            "unexpected_index_list": [
                {"ORDER_NUMBER": "ORD-1041", "LINE_TOTAL": -12.5, "EMAIL": "a@x.com"},
            ]
        },
        tested_column="LINE_TOTAL",
        policy={"identifier_column": "ORDER_NUMBER"},
    )
    assert out == {
        "unexpected_index_list": [
            {"ORDER_NUMBER": "ORD-1041", "LINE_TOTAL": -12.5, "EMAIL": "<redacted>"},
        ]
    }


def test_redact_sample_failures_masks_non_numeric_value_under_safe_key() -> None:
    # The safe-key passthrough trusts value *shape*: a non-number under a safe key
    # (a hypothetical future runner stowing row data there) must still be masked.
    out = run_service.redact_sample_failures(
        {
            "unexpected_count": 3,  # genuine scalar → kept
            "unexpected_percent": ["secret@x.com"],  # not a number → masked
        }
    )
    assert out == {"unexpected_count": 3, "unexpected_percent": ["<redacted>"]}


def test_redact_shows_surrogate_person_key_by_classifier() -> None:
    # No policy: the classifier alone shows a surrogate key (customer_id) as the row
    # locator and masks the PII, without any explicit identifier_column.
    out = run_service.redact_sample_failures(
        {
            "unexpected_index_list": [
                {"CUSTOMER_ID": 4471, "QTY": -3, "CUSTOMER_EMAIL": "a@x.com"},
            ]
        },
        tested_column="QTY",
    )
    assert out == {
        "unexpected_index_list": [{"CUSTOMER_ID": 4471, "QTY": -3, "CUSTOMER_EMAIL": "<redacted>"}]
    }


def test_redact_masks_natural_key_holding_emails() -> None:
    # A `user_id` whose VALUES are emails is a natural key leaking a direct identifier —
    # the value signal overrides the id-shaped name and masks it.
    out = run_service.redact_sample_failures(
        {
            "unexpected_index_list": [
                {"USER_ID": "ada@acme.io", "STATUS": "bad"},
                {"USER_ID": "bo@acme.io", "STATUS": "bad"},
            ]
        },
    )
    assert out == {
        "unexpected_index_list": [
            {"USER_ID": "<redacted>", "STATUS": "bad"},
            {"USER_ID": "<redacted>", "STATUS": "bad"},
        ]
    }


def test_redact_identifier_override_cannot_unmask_pii_column() -> None:
    # A designated identifier that is affirmatively PII (name) must stay masked — an
    # override picks a locator, it can't un-mask a direct identifier.
    out = run_service.redact_sample_failures(
        {"unexpected_index_list": [{"EMAIL": "a@x.com", "QTY": -3}]},
        tested_column="QTY",
        policy={"identifier_column": "EMAIL"},
    )
    assert out == {"unexpected_index_list": [{"EMAIL": "<redacted>", "QTY": -3}]}


def test_redact_identifier_override_masks_natural_key_of_emails() -> None:
    # A `user_id` designated identifier whose VALUES are emails → value floor masks it.
    out = run_service.redact_sample_failures(
        {"unexpected_index_list": [{"USER_ID": "a@x.com", "QTY": -3}]},
        tested_column="QTY",
        policy={"identifier_column": "USER_ID"},
    )
    assert out == {"unexpected_index_list": [{"USER_ID": "<redacted>", "QTY": -3}]}


def test_redact_tested_column_match_is_case_insensitive() -> None:
    # GX returns warehouse casing (Snowflake upper-cases); the check config's column may
    # differ in case. The tested column's non-PII value must still surface.
    out = run_service.redact_sample_failures(
        {"unexpected_index_list": [{"LINE_TOTAL": -12.5, "EMAIL": "a@x.com"}]},
        tested_column="line_total",
    )
    assert out == {"unexpected_index_list": [{"LINE_TOTAL": -12.5, "EMAIL": "<redacted>"}]}


def test_redact_datasource_tag_is_a_floor_override_cannot_unmask() -> None:
    # Level 1 governance tag marks a column sensitive; even an explicit identifier_column
    # override (level 3) cannot un-mask it.
    out = run_service.redact_sample_failures(
        {"unexpected_index_list": [{"ACCOUNT_REF": "ACC-9", "AMOUNT": -1}]},
        tested_column="AMOUNT",
        policy={"identifier_column": "ACCOUNT_REF"},
        tags={"ACCOUNT_REF": "sensitive"},
    )
    assert out == {"unexpected_index_list": [{"ACCOUNT_REF": "<redacted>", "AMOUNT": -1}]}
