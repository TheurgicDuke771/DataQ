"""`run_service.historical_check_context` — #1489."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.db.models import Check, CheckVersion, Connection, Result, Run, Suite, User
from backend.app.services import run_service

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _suite_and_check(
    db_session: Any, *, expectation_type: str = "expect_column_values_to_not_be_null"
) -> tuple[Suite, Check]:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"o-{uuid.uuid4().hex[:8]}@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "x"},
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name="orders", connection_id=conn.id, created_by=owner.id)
    db_session.add(suite)
    db_session.flush()
    check = Check(
        suite_id=suite.id,
        name="values",
        kind="expectation",
        expectation_type=expectation_type,
        config={"column": "LIVE_COLUMN"},
    )
    db_session.add(check)
    db_session.flush()
    return suite, check


def _version(
    db_session: Any,
    check: Check,
    *,
    version_no: int,
    created_at: datetime,
    expectation_type: str,
    column: str,
) -> CheckVersion:
    version = CheckVersion(
        check_id=check.id,
        version_no=version_no,
        name=check.name,
        kind=check.kind,
        expectation_type=expectation_type,
        config={"column": column},
        created_at=created_at,
    )
    db_session.add(version)
    db_session.flush()
    return version


def _result(db_session: Any, check: Check, *, created_at: datetime) -> Result:
    run = Run(suite_id=check.suite_id, status="succeeded", triggered_by="manual")
    db_session.add(run)
    db_session.flush()
    result = Result(run_id=run.id, check_id=check.id, status="fail", created_at=created_at)
    db_session.add(result)
    db_session.flush()
    return result


def test_resolves_the_version_in_effect_when_each_result_was_written(db_session: Any) -> None:
    # #1486's own motivating case: a check that reported a MEAN when a result was written, later
    # edited to MAX/MIN — the old result must not retroactively look like a literal-cell exposure.
    _suite, check = _suite_and_check(db_session, expectation_type="expect_table_row_count_to_equal")
    _version(
        db_session,
        check,
        version_no=1,
        created_at=_T0,
        expectation_type="expect_column_values_to_not_be_null",
        column="v1_column",
    )
    _version(
        db_session,
        check,
        version_no=2,
        created_at=_T0 + timedelta(days=1),
        expectation_type="expect_column_max_to_be_between",
        column="v2_column",
    )
    before_edit = _result(db_session, check, created_at=_T0 + timedelta(hours=1))
    after_edit = _result(db_session, check, created_at=_T0 + timedelta(days=2))

    context = run_service.historical_check_context(
        db_session, [before_edit, after_edit], {check.id: check}
    )

    assert context[before_edit.id] == ("v1_column", "expect_column_values_to_not_be_null")
    assert context[after_edit.id] == ("v2_column", "expect_column_max_to_be_between")


def test_falls_back_to_the_live_check_with_no_version_history(db_session: Any) -> None:
    # A check created before #280 shipped (or seeded directly, bypassing check_service) has no
    # check_versions rows at all — no worse off than before this function existed.
    _suite, check = _suite_and_check(db_session, expectation_type="expect_column_max_to_be_between")
    result = _result(db_session, check, created_at=_T0)

    context = run_service.historical_check_context(db_session, [result], {check.id: check})

    assert context[result.id] == ("LIVE_COLUMN", "expect_column_max_to_be_between")


def test_falls_back_to_the_earliest_version_when_the_result_predates_it(db_session: Any) -> None:
    # Clock skew / same-instant edge case: every version is AFTER the result's created_at. "Before
    # the first edit" is a closer approximation of history than jumping to whatever the check is
    # today.
    _suite, check = _suite_and_check(db_session)
    _version(
        db_session,
        check,
        version_no=1,
        created_at=_T0 + timedelta(days=1),
        expectation_type="expect_column_min_to_be_between",
        column="earliest_column",
    )
    result = _result(db_session, check, created_at=_T0)

    context = run_service.historical_check_context(db_session, [result], {check.id: check})

    assert context[result.id] == ("earliest_column", "expect_column_min_to_be_between")


def test_a_result_with_no_check_id_resolves_to_none() -> None:
    # Defensive: a result whose check was hard-deleted (nullable FK path, if it
    # ever exists) must not raise.
    result = Result(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        check_id=None,
        status="fail",
        created_at=_T0,
    )
    context = run_service.historical_check_context(None, [result], {})  # type: ignore[arg-type]
    assert context[result.id] == (None, None)
