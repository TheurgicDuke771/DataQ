"""Tests for the result retention sweep (`purge_expired_sample_failures`).

DB-backed (real Postgres): the sweep is a bulk UPDATE keyed on `created_at` +
the JSONB `sample_failures`/`observed_value` columns, which can't be faithfully
faked. Verifies it scrubs only old, unpurged rows that still carry samples,
keeps `metric_value` (trends survive — ADR 0012), is idempotent, and honours
the disable sentinel — plus (#1253) that `observed_value`'s sweep touches ONLY
the list-shaped set-oriented-expectation case and never a scalar aggregate.
Skips without TEST_DATABASE_URL.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.sql.dml import Update

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
    observed: Any = None,
) -> Result:
    check, run = _check_and_run(db_session)
    row = Result(
        run_id=run.id,
        check_id=check.id,
        status="fail",
        metric_value=metric,
        sample_failures={"rows": [{"id": 1}]} if sample is _UNSET else sample,
        sample_failures_purged_at=purged_at,
        observed_value=observed,
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
    """Covers BOTH sibling columns: the early `retention_days <= 0` return must
    guard the observed_value half too, not just sample_failures — a row with a
    list-shaped observed_value seeded here would catch a future refactor that
    splits the single early-return into two per-column guards and gets the
    observed_value one wrong."""
    old = _result(db_session, age_days=400, observed={"observed_value": ["still@here.example"]})

    assert run_service.purge_expired_sample_failures(db_session, retention_days=0, now=NOW) == 0
    assert run_service.purge_expired_sample_failures(db_session, retention_days=-1, now=NOW) == 0
    db_session.refresh(old)
    assert old.sample_failures is not None  # nothing scrubbed
    assert old.observed_value == {"observed_value": ["still@here.example"]}


# ── #1253: observed_value's sibling sweep ────────────────────────────────────


def test_scrubs_old_list_shaped_observed_value(db_session: Any) -> None:
    """The set-oriented-expectation shape (#1229/#1252) — a raw distinct-value
    list — is the one PII-bearing `observed_value` shape, and it's nulled past
    the retention window same as `sample_failures`."""
    old = _result(
        db_session,
        age_days=40,
        sample=None,
        observed={"observed_value": ["alice@example.com", "bob@example.com"]},
    )

    purged = run_service.purge_expired_sample_failures(db_session, retention_days=30, now=NOW)

    assert purged == 1
    db_session.refresh(old)
    assert old.observed_value is None


def test_keeps_scalar_observed_value(db_session: Any) -> None:
    """The critical negative case — a scalar aggregate (row count, mean)
    sharing the same wrapper shape as the PII-bearing list case must survive
    the sweep untouched, since it's what `metric_value` trends and anomaly
    baselines read from (ADR 0012). Getting this backwards would silently
    destroy legitimate metric data."""
    old = _result(
        db_session,
        age_days=40,
        sample=None,
        observed={"observed_value": 34680},
    )

    purged = run_service.purge_expired_sample_failures(db_session, retention_days=30, now=NOW)

    assert purged == 0
    db_session.refresh(old)
    assert old.observed_value == {"observed_value": 34680}


def test_keeps_recent_list_shaped_observed_value(db_session: Any) -> None:
    """Inside the retention window: even the PII-bearing shape is untouched."""
    recent = _result(db_session, age_days=5, sample=None, observed={"observed_value": ["a@x.com"]})

    purged = run_service.purge_expired_sample_failures(db_session, retention_days=30, now=NOW)

    assert purged == 0
    db_session.refresh(recent)
    assert recent.observed_value == {"observed_value": ["a@x.com"]}


def test_leaves_error_and_reason_shapes_untouched(db_session: Any) -> None:
    """`{"error": ...}` / `{"unparsed_value": ..., "column": ...}` / `{"reason":
    ...}` never nest a top-level `observed_value` key, so the sweep's
    `jsonb_typeof(observed_value -> 'observed_value') = 'array'` condition
    can't match them — confirmed here rather than assumed."""
    error_row = _result(db_session, age_days=40, sample=None, observed={"error": "boom"})
    unparsed_row = _result(
        db_session,
        age_days=40,
        sample=None,
        observed={"unparsed_value": "not-a-date", "column": "created_at"},
    )
    skip_row = _result(db_session, age_days=40, sample=None, observed={"reason": "no baseline yet"})

    purged = run_service.purge_expired_sample_failures(db_session, retention_days=30, now=NOW)

    assert purged == 0
    for row in (error_row, unparsed_row, skip_row):
        db_session.refresh(row)
    assert error_row.observed_value == {"error": "boom"}
    assert unparsed_row.observed_value == {"unparsed_value": "not-a-date", "column": "created_at"}
    assert skip_row.observed_value == {"reason": "no baseline yet"}


def test_purges_both_sibling_columns_independently_and_sums(db_session: Any) -> None:
    """A row whose `sample_failures` AND `observed_value` are both scrubbable
    counts as 2 in the return value — independent UPDATEs, not one row-count."""
    old = _result(
        db_session,
        age_days=40,
        sample={"rows": [{"id": 1}]},
        observed={"observed_value": ["x@example.com"]},
        metric=Decimal("12.0"),
    )

    purged = run_service.purge_expired_sample_failures(db_session, retention_days=30, now=NOW)

    assert purged == 2
    db_session.refresh(old)
    assert old.sample_failures is None
    assert old.observed_value is None
    assert old.sample_failures_purged_at == NOW
    assert old.metric_value == Decimal("12.0")  # trend scalar survives (ADR 0012)


def test_idempotent_second_sweep_observed_value(db_session: Any) -> None:
    """No dedicated `observed_value_purged_at` column: idempotency relies on the
    column itself going SQL NULL, so a second sweep must not error or re-count."""
    old = _result(
        db_session, age_days=40, sample=None, observed={"observed_value": ["x@example.com"]}
    )

    first = run_service.purge_expired_sample_failures(db_session, retention_days=30, now=NOW)
    second = run_service.purge_expired_sample_failures(db_session, retention_days=30, now=NOW)

    assert first == 1
    assert second == 0
    db_session.refresh(old)
    assert old.observed_value is None


# ── #323: bounded batching ────────────────────────────────────────────────────


def test_batches_across_multiple_chunks_purge_everything(db_session: Any) -> None:
    """A candidate set larger than one batch must still be swept in full — not
    just the first `chunk_size` rows. Passes a `chunk_size` far smaller than the
    default (`_PURGE_SWEEP_CHUNK`) so a small, fast seed can still force the
    `_purge_column` loop through several iterations: 7 eligible rows over
    chunk_size=3 means 3 full batches (3, 3, 1) before the `affected <
    chunk_size` sentinel fires. A regression that dropped the loop back to a
    single UPDATE would still purge every row here (chunk_size is irrelevant
    to a single unbounded UPDATE), so this alone doesn't prove batching
    happened — `test_batch_count_matches_the_configured_chunk_size` below
    proves that half via the number of UPDATE statements actually issued."""
    rows = [_result(db_session, age_days=40, metric=Decimal(str(i))) for i in range(7)]

    purged = run_service.purge_expired_sample_failures(
        db_session, retention_days=30, now=NOW, chunk_size=3
    )

    assert purged == 7
    for row in rows:
        db_session.refresh(row)
        assert row.sample_failures is None
        assert row.sample_failures_purged_at == NOW
        # the trend scalar survives regardless of which batch a row landed in
        assert row.metric_value is not None


def test_batch_count_matches_the_configured_chunk_size(db_session: Any, monkeypatch: Any) -> None:
    """Proves the loop actually iterates in bounded chunks — not just that the
    end result is complete (`test_batches_across_multiple_chunks_purge_everything`
    would pass just the same against the old single-UPDATE shape, since
    chunk_size doesn't change what a single UPDATE touches). Counts the
    UPDATEs `_purge_column` issues against `results` by wrapping
    `Session.execute`; 7 eligible rows over chunk_size=3 must take exactly 3
    UPDATE statements (3 + 3 + 1), not 1."""
    for i in range(7):
        _result(db_session, age_days=40, metric=Decimal(str(i)))

    executed: list[Any] = []
    original_execute = db_session.execute

    def _counting_execute(statement: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(statement, Update):
            executed.append(statement)
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", _counting_execute)

    purged = run_service.purge_expired_sample_failures(
        db_session, retention_days=30, now=NOW, chunk_size=3
    )

    assert purged == 7
    # sample_failures batches (3) + observed_value batches (1, nothing eligible
    # there — the loop still runs once and exits on `affected < chunk_size`)
    assert len(executed) == 4


def test_chunk_size_does_not_leak_rows_outside_the_retention_window(db_session: Any) -> None:
    """A small chunk_size must not accidentally sweep rows that are still
    inside the retention window — the per-chunk subquery re-applies the full
    predicate every iteration, not just on the first batch."""
    old_rows = [_result(db_session, age_days=40, metric=Decimal(str(i))) for i in range(5)]
    recent = _result(db_session, age_days=5, metric=Decimal("99"))

    purged = run_service.purge_expired_sample_failures(
        db_session, retention_days=30, now=NOW, chunk_size=2
    )

    assert purged == 5
    for row in old_rows:
        db_session.refresh(row)
        assert row.sample_failures is None
    db_session.refresh(recent)
    assert recent.sample_failures == {"rows": [{"id": 1}]}
    assert recent.sample_failures_purged_at is None
