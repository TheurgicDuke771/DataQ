"""Tests for the result retention sweep (`purge_expired_sample_failures`).

DB-backed (real Postgres): the sweep is a bulk UPDATE keyed on `created_at` +
the JSONB `sample_failures` column, which can't be faithfully faked. Verifies it
scrubs only old, unpurged rows that still carry samples, keeps `metric_value`
(trends survive — ADR 0012), is idempotent, and honours the disable sentinel.
Skips without TEST_DATABASE_URL.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from backend.app.db.models import Check, Connection, Result, Run, Suite, User
from backend.app.services import run_service

NOW = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
_UNSET = object()  # sentinel so an explicit sample=None is stored as SQL NULL


def _check_and_run(db_session: Any) -> tuple[Check, Run]:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a"},
        secret_ref="kv-sf",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name="s", connection_id=conn.id, created_by=owner.id, target={"table": "T"})
    db_session.add(suite)
    db_session.flush()
    check = Check(suite_id=suite.id, name="c", expectation_type="expect_x", config={})
    db_session.add(check)
    db_session.flush()
    run = Run(suite_id=suite.id, status="succeeded")
    db_session.add(run)
    db_session.flush()
    return check, run


def _result(
    db_session: Any,
    *,
    age_days: int,
    sample: Any = _UNSET,
    purged_at: datetime | None = None,
    metric: Decimal | None = None,
) -> Result:
    check, run = _check_and_run(db_session)
    row = Result(
        run_id=run.id,
        check_id=check.id,
        status="fail",
        metric_value=metric,
        sample_failures={"rows": [{"id": 1}]} if sample is _UNSET else sample,
        sample_failures_purged_at=purged_at,
        created_at=NOW - timedelta(days=age_days),
    )
    db_session.add(row)
    db_session.commit()
    return row


def test_scrubs_old_rows_keeps_metric(db_session: Any) -> None:
    old = _result(db_session, age_days=40, metric=Decimal("9.5"))

    purged = run_service.purge_expired_sample_failures(db_session, retention_days=30, now=NOW)

    assert purged == 1
    db_session.refresh(old)
    assert old.sample_failures is None
    assert old.sample_failures_purged_at == NOW
    # the row + the SQL-aggregatable scalar survive (ADR 0012)
    assert old.metric_value == Decimal("9.5")
    assert old.status == "fail"


def test_keeps_rows_inside_window(db_session: Any) -> None:
    recent = _result(db_session, age_days=10)

    purged = run_service.purge_expired_sample_failures(db_session, retention_days=30, now=NOW)

    assert purged == 0
    db_session.refresh(recent)
    assert recent.sample_failures == {"rows": [{"id": 1}]}
    assert recent.sample_failures_purged_at is None


def test_skips_rows_with_no_sample(db_session: Any) -> None:
    """A row whose sample is already NULL is untouched (no spurious stamp)."""
    no_sample = _result(db_session, age_days=40, sample=None)

    purged = run_service.purge_expired_sample_failures(db_session, retention_days=30, now=NOW)

    assert purged == 0
    db_session.refresh(no_sample)
    assert no_sample.sample_failures_purged_at is None


def test_idempotent_already_purged(db_session: Any) -> None:
    """A second sweep doesn't re-stamp an already-purged row (purged_at set)."""
    earlier = NOW - timedelta(days=5)
    already = _result(db_session, age_days=40, sample=None, purged_at=earlier)

    purged = run_service.purge_expired_sample_failures(db_session, retention_days=30, now=NOW)

    assert purged == 0
    db_session.refresh(already)
    assert already.sample_failures_purged_at == earlier  # untouched


def test_disabled_when_retention_non_positive(db_session: Any) -> None:
    old = _result(db_session, age_days=400)

    assert run_service.purge_expired_sample_failures(db_session, retention_days=0, now=NOW) == 0
    assert run_service.purge_expired_sample_failures(db_session, retention_days=-1, now=NOW) == 0
    db_session.refresh(old)
    assert old.sample_failures is not None  # nothing scrubbed
