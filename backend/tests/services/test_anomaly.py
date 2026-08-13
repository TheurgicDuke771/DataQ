"""anomaly engine tests (#593) — the pure scoring, the baseline payload's
load/trim round-trip, the driver-boundary measurement, and the executor's
cold-start/score/persist lifecycle against the real test DB."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine.default import DefaultDialect
from sqlalchemy.orm import Session

from backend.app.datasources.monitors import (
    ANOMALY_DEGENERATE_Z,
    MonitorConfigError,
    anomaly_params,
)
from backend.app.db.models import Check, Connection, MonitorBaseline, Suite, User
from backend.app.services import anomaly
from backend.app.services.anomaly import (
    BASELINE_VERSION,
    Observation,
    build_anomaly_executor,
    build_score_payload,
    dump_baseline,
    eligible_values,
    load_observations,
    measure_metric,
    score,
    trim,
)
from backend.app.services.monitor_baseline import get_baseline
from backend.tests.support.fake_secret_store import FakeSecretStore

# A Wednesday, so weekday filtering is observable.
_NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)


def _params(**overrides: Any) -> Any:
    return anomaly_params({"target_metric": "row_count", **overrides})


# ───────────────────────── pure scoring ─────────────────────────


def test_score_matches_a_hand_computed_z() -> None:
    """priors 10/12/14/16/18 → mean 14, SAMPLE stddev sqrt(10) ≈ 3.162278.
    value 20 → |20-14| / 3.162278 = 1.897367. Hand-computed so a silent switch to
    the population stddev (which would give 2.121) fails here."""
    z, mean, stddev, degenerate = score(20.0, [10.0, 12.0, 14.0, 16.0, 18.0])
    assert mean == 14.0
    assert stddev == pytest.approx(3.1622776601, rel=1e-9)
    assert z == pytest.approx(1.8973665961, rel=1e-9)
    assert degenerate is False


def test_score_is_symmetric_a_drop_scores_like_a_spike() -> None:
    """The metric is |deviation| — a collapse in row count must escalate exactly
    like a spike, or half the incidents the kind exists for go unreported."""
    priors = [100.0, 110.0, 90.0, 105.0, 95.0]
    high, _, _, _ = score(140.0, priors)
    low, _, _, _ = score(60.0, priors)
    assert high == pytest.approx(low)


def test_degenerate_stddev_with_an_identical_value_is_zero() -> None:
    z, mean, stddev, degenerate = score(50.0, [50.0, 50.0, 50.0])
    assert (z, mean, stddev, degenerate) == (0.0, 50.0, 0.0, True)


def test_degenerate_stddev_with_a_different_value_is_the_finite_sentinel() -> None:
    """The true z is +inf. `severity.extract_metric` DROPS a non-finite metric as
    "nothing to band", which would resolve maximal deviation to a silent `pass` —
    so the sentinel is a documented finite number, not arithmetic."""
    z, _, _, degenerate = score(51.0, [50.0, 50.0, 50.0])
    assert z == ANOMALY_DEGENERATE_Z
    assert degenerate is True
    from backend.app.datasources.base import CheckOutcome
    from backend.app.services.severity import extract_metric

    assert extract_metric(CheckOutcome("monitor:anomaly", success=True, metric_value=z)) == Decimal(
        str(ANOMALY_DEGENERATE_Z)
    )


def test_score_refuses_fewer_than_two_priors() -> None:
    with pytest.raises(MonitorConfigError):
        score(1.0, [1.0])


# ───────────────────────── payload shape ─────────────────────────


def test_payload_below_min_points_is_the_cold_start_shape() -> None:
    payload = build_score_payload(500.0, [1.0, 2.0, 3.0], _params(min_points=7))
    assert payload["insufficient_history"] is True
    assert payload["reason"] == "insufficient_history"
    assert payload["points"] == 3
    assert "z_score" not in payload  # no verdict is computed, let alone reported


def test_payload_at_exactly_min_points_scores() -> None:
    """The boundary: `< min_points` skips, `== min_points` evaluates. An off-by-one
    here would leave every check one run behind forever."""
    priors = [10.0] * 2 + [12.0]
    payload = build_score_payload(11.0, priors, _params(min_points=3))
    assert "insufficient_history" not in payload
    assert payload["points"] == 3
    assert isinstance(payload["z_score"], float)


def test_payload_carries_everything_the_verdict_rests_on() -> None:
    payload = build_score_payload(20.0, [10.0, 12.0, 14.0, 16.0, 18.0], _params(min_points=3))
    assert payload["value"] == 20.0
    assert payload["mean"] == 14.0
    assert payload["stddev"] == pytest.approx(3.162278, abs=1e-6)
    assert payload["deviation"] == 6.0
    assert payload["target_metric"] == "row_count"
    assert payload["degenerate_stddev"] is False


# ───────────────────── window / seasonality selection ─────────────────────


def _obs(days_ago: int, value: float) -> Observation:
    return Observation(ts=_NOW - timedelta(days=days_ago), value=value)


def test_eligible_values_takes_the_last_window_in_order() -> None:
    observations = [_obs(n, float(n)) for n in range(10, 0, -1)]  # oldest first
    values = eligible_values(observations, now=_NOW, params=_params(window=3))
    assert values == [3.0, 2.0, 1.0]


def test_seasonality_keeps_only_the_same_weekday() -> None:
    """`_NOW` is a Wednesday. With seasonality on, only the observations 7 and 14
    days back count — the Monday-is-always-triple case the option exists for."""
    observations = [_obs(n, float(n)) for n in (14, 10, 7, 3, 1)]
    values = eligible_values(observations, now=_NOW, params=_params(seasonality=True))
    assert values == [14.0, 7.0]
    # …and with it off, everything counts.
    assert eligible_values(observations, now=_NOW, params=_params()) == [
        14.0,
        10.0,
        7.0,
        3.0,
        1.0,
    ]


def test_seasonality_still_respects_the_window() -> None:
    observations = [_obs(n, float(n)) for n in (28, 21, 14, 7)]
    values = eligible_values(observations, now=_NOW, params=_params(window=3, seasonality=True))
    assert values == [21.0, 14.0, 7.0]


def test_trim_keeps_the_newest_window() -> None:
    observations = [_obs(n, float(n)) for n in range(5, 0, -1)]
    assert [o.value for o in trim(observations, _params(window=3))] == [3.0, 2.0, 1.0]


def test_trim_keeps_seven_windows_when_seasonal() -> None:
    """The retained ring has to outlast the weekday filter — trimming to `window`
    would leave a seasonal check with at most one or two same-weekday points and a
    permanent cold start."""
    observations = [_obs(n, float(n)) for n in range(40, 0, -1)]
    assert len(trim(observations, _params(window=4, seasonality=True))) == 28


# ───────────────────── baseline payload round-trip ─────────────────────


def _row(baseline: dict[str, Any]) -> MonitorBaseline:
    return MonitorBaseline(check_id=uuid.uuid4(), kind="anomaly", baseline=baseline)


def test_baseline_round_trips() -> None:
    params = _params()
    observations = [_obs(2, 100.0), _obs(1, 110.0)]
    restored = load_observations(_row(dump_baseline(observations, params)), params)
    assert [(o.ts, o.value) for o in restored] == [(o.ts, o.value) for o in observations]


def test_no_row_is_a_cold_start() -> None:
    assert load_observations(None, _params()) == []


def test_a_future_payload_version_is_not_misread() -> None:
    payload = dump_baseline([_obs(1, 5.0)], _params())
    payload["version"] = BASELINE_VERSION + 1
    assert load_observations(_row(payload), _params()) == []


def test_a_different_target_metric_restarts_learning() -> None:
    """Row counts and staleness hours are different quantities in different units.
    Scoring one against the other's history would be a confident number about
    nothing, so editing the target metric restarts the baseline."""
    stored = dump_baseline([_obs(1, 32840.0)], _params())
    reread = load_observations(
        _row(stored), _params(target_metric="freshness_age_hours", column="ts")
    )
    assert reread == []


def test_malformed_entries_are_dropped_individually() -> None:
    """A single odd JSONB entry must not error the check — the surviving history is
    still a valid baseline."""
    payload = dump_baseline([_obs(2, 10.0)], _params())
    payload["observations"] += [
        {"ts": "not-a-date", "value": 1.0},
        {"ts": _NOW.isoformat(), "value": None},
        {"ts": _NOW.isoformat(), "value": float("inf")},
        {"ts": _NOW.isoformat(), "value": True},  # bool is an int subclass
        "not-a-dict",
    ]
    assert [o.value for o in load_observations(_row(payload), _params())] == [10.0]


def test_a_non_list_observations_payload_is_a_cold_start() -> None:
    payload = dump_baseline([], _params())
    payload["observations"] = {"not": "a list"}
    assert load_observations(_row(payload), _params()) == []


def test_a_naive_stored_timestamp_is_read_as_utc() -> None:
    """Everything we write is UTC-aware, but a hand-edited or legacy row must not
    explode the weekday comparison with a naive/aware mix."""
    payload = dump_baseline([], _params())
    payload["observations"] = [{"ts": "2026-07-29T12:00:00", "value": 5.0}]
    (restored,) = load_observations(_row(payload), _params())
    assert restored.ts == _NOW


# ───────────────────── measurement (driver boundary) ─────────────────────


def _sql_connection(conn_type: str = "snowflake") -> Connection:
    return Connection(
        id=uuid.uuid4(),
        name="sf",
        type=conn_type,
        env="dev",
        config={
            "account": "acct",
            "user": "u",
            "database": "DB",
            "schema": "PUBLIC",
            "warehouse": "WH",
        },
        secret_ref="ref",
        created_by=uuid.uuid4(),
    )


def _patch_scalar(monkeypatch: pytest.MonkeyPatch, scalar: Any) -> dict[str, Any]:
    """Stand in for the live connection, capturing the statement it was handed."""
    seen: dict[str, Any] = {}

    class _Conn:
        # A real Connection exposes `.dialect` (the engine's) — `measure_metric`
        # needs it whenever `catalog` is given (#936), so the fake carries a
        # stand-in too; every existing test here passes `catalog=None`, which
        # never touches it.
        dialect = DefaultDialect()

        def execute(self, statement: Any) -> Any:
            seen["statement"] = statement

            class _Res:
                @staticmethod
                def scalar() -> Any:
                    return scalar

            return _Res()

    @contextmanager
    def fake_open(connection: Connection, secret_store: Any) -> Any:
        yield _Conn()

    monkeypatch.setattr(anomaly, "_open_connection", fake_open)
    return seen


@pytest.mark.parametrize(
    ("scalar", "expected"),
    [(32840, 32840.0), (Decimal("32840"), 32840.0), ("32840", 32840.0)],
)
def test_row_count_measurement_accepts_driver_types(
    monkeypatch: pytest.MonkeyPatch, scalar: Any, expected: float
) -> None:
    """Snowflake returns a COUNT as Decimal, Databricks as int (#953's shape: the
    type comes from the DRIVER, and every hand-built fixture agrees with our model
    by construction). The parametrisation is the point."""
    seen = _patch_scalar(monkeypatch, scalar)
    value = measure_metric(
        _sql_connection(),
        table="ORDERS",
        schema="RETAIL",
        catalog=None,
        params=_params(),
        secret_store=FakeSecretStore(default="secret", raise_on_write=True),
        now=_NOW,
    )
    assert value == expected
    assert "count" in str(seen["statement"]).lower()


def test_row_count_measurement_refuses_a_non_numeric_scalar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_scalar(monkeypatch, None)
    with pytest.raises(MonitorConfigError):
        measure_metric(
            _sql_connection(),
            table="ORDERS",
            schema=None,
            catalog=None,
            params=_params(),
            secret_store=FakeSecretStore(default="secret", raise_on_write=True),
            now=_NOW,
        )


@pytest.mark.parametrize(
    "scalar",
    [
        datetime(2026, 7, 29, 6, 0, tzinfo=UTC),
        datetime(2026, 7, 29, 6, 0),  # naive TIMESTAMP_NTZ
        "2026-07-29T06:00:00",  # the Databricks connector returns a str (#953)
    ],
)
def test_freshness_measurement_accepts_driver_types(
    monkeypatch: pytest.MonkeyPatch, scalar: Any
) -> None:
    seen = _patch_scalar(monkeypatch, scalar)
    value = measure_metric(
        _sql_connection(),
        table="ORDERS",
        schema=None,
        catalog=None,
        params=_params(target_metric="freshness_age_hours", column="loaded_at"),
        secret_store=FakeSecretStore(default="secret", raise_on_write=True),
        now=_NOW,
    )
    assert value == 6.0
    assert "max" in str(seen["statement"]).lower()


def test_freshness_measurement_on_an_empty_table_is_an_error_not_a_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing MAX has no age. Scoring it as 0 would poison the baseline with a
    fabricated point and then report every real reading as an anomaly."""
    _patch_scalar(monkeypatch, None)
    with pytest.raises(MonitorConfigError, match="unavailable"):
        measure_metric(
            _sql_connection(),
            table="ORDERS",
            schema=None,
            catalog=None,
            params=_params(target_metric="freshness_age_hours", column="loaded_at"),
            secret_store=FakeSecretStore(default="secret", raise_on_write=True),
            now=_NOW,
        )


def test_measurement_quotes_a_mixed_case_column_through_the_dialect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#476/#937: the measurement must go through the Core builders so the
    CONNECTION's dialect quotes identifiers. Hand-rolled `"` would be actively
    wrong on Unity Catalog, which uses backticks."""
    from snowflake.sqlalchemy import snowdialect

    seen = _patch_scalar(monkeypatch, datetime(2026, 7, 29, 6, 0, tzinfo=UTC))
    measure_metric(
        _sql_connection(),
        table="ORDERS",
        schema=None,
        catalog=None,
        params=_params(target_metric="freshness_age_hours", column="Loaded_At"),
        secret_store=FakeSecretStore(default="secret", raise_on_write=True),
        now=_NOW,
    )
    sql = str(seen["statement"].compile(dialect=snowdialect.SnowflakeDialect()))
    assert '"Loaded_At"' in sql


def test_measurement_quotes_a_mixed_case_catalog_through_the_connections_dialect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#936: a `catalog`-qualified (Unity Catalog, 3-part) measurement now quotes
    a mixed-case catalog/schema too — not just the table. Proven with Databricks'
    BACKTICK dialect, not Snowflake's `"`, on the connection's own `.dialect`
    (`_open_connection`'s real `Connection.dialect`, stood in here) — a
    hardcoded quote character could not pass this."""
    from databricks.sqlalchemy.base import DatabricksDialect

    seen: dict[str, Any] = {}

    class _Conn:
        dialect = DatabricksDialect()

        def execute(self, statement: Any) -> Any:
            seen["statement"] = statement

            class _Res:
                @staticmethod
                def scalar() -> Any:
                    return 42

            return _Res()

    @contextmanager
    def fake_open(connection: Connection, secret_store: Any) -> Any:
        yield _Conn()

    monkeypatch.setattr(anomaly, "_open_connection", fake_open)

    measure_metric(
        _sql_connection("unity_catalog"),
        table="Orders",
        schema="Retail",
        catalog="MyCat",
        params=_params(),
        secret_store=_FakeStore(),
        now=_NOW,
    )
    assert "`MyCat`.`Retail`.`Orders`" in str(seen["statement"])


def test_measurement_on_a_non_sql_datasource_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defence in depth behind the author-time gate: the message names the real
    restriction rather than surfacing as a generic classified failure."""
    conn = Connection(
        id=uuid.uuid4(),
        name="lake",
        type="adls_gen2",
        env="dev",
        config={"account_name": "acct", "container": "c"},
        secret_ref="ref",
        created_by=uuid.uuid4(),
    )
    with pytest.raises(MonitorConfigError, match="SQL datasource"):
        measure_metric(
            conn,
            table="orders",
            schema=None,
            catalog=None,
            params=_params(),
            secret_store=FakeSecretStore(default="secret", raise_on_write=True),
            now=_NOW,
        )


# ───────────────────── executor lifecycle (real DB) ─────────────────────


@pytest.fixture
def graph(db_session: Session) -> tuple[Session, Connection, Check]:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(user)
    db_session.flush()
    conn = _sql_connection()
    conn.created_by = user.id
    db_session.add(conn)
    db_session.flush()
    suite = Suite(
        name=f"s-{uuid.uuid4().hex[:8]}",
        connection_id=conn.id,
        created_by=user.id,
        target={"table": "ORDERS"},
    )
    db_session.add(suite)
    db_session.flush()
    check = Check(
        suite_id=suite.id,
        name="volume anomaly",
        kind="anomaly",
        expectation_type="monitor:anomaly",
        config={"target_metric": "row_count", "window": 5, "min_points": 3},
        fail_threshold=Decimal("3"),
    )
    db_session.add(check)
    db_session.flush()
    return db_session, conn, check


def _executor(
    session: Session,
    conn: Connection,
    scalar: Any,
    monkeypatch: pytest.MonkeyPatch,
    *,
    persist: bool = True,
) -> Any:
    _patch_scalar(monkeypatch, scalar)
    return build_anomaly_executor(
        session,
        connection=conn,
        target_table="ORDERS",
        target_schema=None,
        target_catalog=None,
        secret_store=FakeSecretStore(default="secret", raise_on_write=True),
        persist=persist,
    )


def _stored_values(session: Session, check: Check) -> list[float]:
    session.flush()
    row = get_baseline(session, check.id)
    assert row is not None
    return [o["value"] for o in row.baseline["observations"]]


@contextmanager
def _captured_sql(session: Session) -> Any:
    """Record every statement the session actually sends to Postgres.

    Asserting against the EMITTED SQL rather than a mocked call is what makes the
    locking test mean something: `with_for_update()` is only worth anything if it
    reaches the wire, and a spy on our own helper would pass even if the clause
    were silently dropped by a query rewrite."""
    from sqlalchemy import event

    statements: list[str] = []
    bind = session.get_bind()

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    event.listen(bind, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(bind, "before_cursor_execute", record)


def _baseline_selects(statements: list[str]) -> list[str]:
    return [
        s
        for s in statements
        if "monitor_baselines" in s and s.lstrip().upper().startswith("SELECT")
    ]


def test_first_run_skips_and_still_records_the_observation(
    graph: tuple[Session, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, conn, check = graph
    outcome = _executor(session, conn, 1000, monkeypatch)(check)
    assert outcome.skipped is True
    assert outcome.metric_value is None
    assert outcome.observed_value is not None
    assert outcome.observed_value["points"] == 0
    assert outcome.observed_value["value"] == 1000.0
    # Recording on a skip is how the history accrues — without it the check would
    # skip forever.
    assert _stored_values(session, check) == [1000.0]


def test_history_accrues_then_scores_at_min_points(
    graph: tuple[Session, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, conn, check = graph
    for value in (1000, 1000, 1000):
        outcome = _executor(session, conn, value, monkeypatch)(check)
        assert outcome.skipped is True  # 0, then 1, then 2 priors — all < min_points 3
        session.flush()
    scored = _executor(session, conn, 1000, monkeypatch)(check)
    assert scored.skipped is False
    assert scored.metric_value == 0.0  # identical history, identical value
    assert scored.observed_value is not None
    assert scored.observed_value["points"] == 3


def test_a_spike_against_a_learned_baseline_bands_as_a_high_z(
    graph: tuple[Session, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, conn, check = graph
    for value in (100, 110, 90, 105):
        _executor(session, conn, value, monkeypatch)(check)
        session.flush()
    outcome = _executor(session, conn, 400, monkeypatch)(check)
    assert outcome.metric_value is not None and outcome.metric_value > 3.0
    # The check's fail threshold (3) bands it — no new severity machinery (ADR 0016).
    from backend.app.services.severity import resolve_status

    status, metric = resolve_status(
        outcome,
        warn_threshold=check.warn_threshold,
        fail_threshold=check.fail_threshold,
        critical_threshold=check.critical_threshold,
    )
    assert status == "fail"
    assert metric is not None and metric > 3


def test_the_window_trims_the_stored_history(
    graph: tuple[Session, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, conn, check = graph  # window=5
    for value in range(1, 9):
        _executor(session, conn, value, monkeypatch)(check)
        session.flush()
    assert _stored_values(session, check) == [4.0, 5.0, 6.0, 7.0, 8.0]


def test_dry_run_mode_never_persists(
    graph: tuple[Session, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, conn, check = graph
    outcome = _executor(session, conn, 1000, monkeypatch, persist=False)(check)
    assert outcome.observed_value is not None
    assert outcome.observed_value["dry_run"] is True
    session.flush()
    assert get_baseline(session, check.id) is None


def test_dry_run_against_an_existing_baseline_scores_without_writing(
    graph: tuple[Session, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, conn, check = graph
    for value in (100, 110, 90):
        _executor(session, conn, value, monkeypatch)(check)
        session.flush()
    before = _stored_values(session, check)
    outcome = _executor(session, conn, 400, monkeypatch, persist=False)(check)
    assert outcome.metric_value is not None and outcome.metric_value > 0
    assert _stored_values(session, check) == before  # the preview added nothing


def test_a_concurrent_first_run_does_not_break_the_commit(
    graph: tuple[Session, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two first runs of one suite both see no baseline under READ COMMITTED and
    both insert. The loser must not raise an IntegrityError that discards every
    sibling result row (#122) — ON CONFLICT DO NOTHING makes it a no-op."""
    session, conn, check = graph
    session.add(MonitorBaseline(check_id=check.id, kind="anomaly", baseline={"version": 1}))
    session.flush()
    outcome = _executor(session, conn, 1000, monkeypatch)(check)
    session.flush()  # must not raise
    assert outcome.skipped is True


def test_a_measurement_failure_is_the_checks_error_not_the_runs(
    graph: tuple[Session, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, conn, check = graph

    @contextmanager
    def boom(connection: Connection, secret_store: Any) -> Any:
        raise RuntimeError("snowflake://user:SECRET@acct/db unreachable")
        yield  # pragma: no cover

    monkeypatch.setattr(anomaly, "_open_connection", boom)
    executor = build_anomaly_executor(
        session,
        connection=conn,
        target_table="ORDERS",
        target_schema=None,
        target_catalog=None,
        secret_store=FakeSecretStore(default="secret", raise_on_write=True),
    )
    outcome = executor(check)
    assert outcome.errored is True
    # The raw driver text can carry a DSN / SAS-signed URL (#828/#900) — the
    # classified reason is what reaches the result row.
    assert "SECRET" not in (outcome.error_message or "")
    session.flush()
    assert get_baseline(session, check.id) is None  # nothing recorded from a failure


def test_a_bad_config_errors_the_check_with_an_actionable_message(
    graph: tuple[Session, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, conn, check = graph
    check.config = {"target_metric": "nonsense"}
    outcome = _executor(session, conn, 1, monkeypatch)(check)
    assert outcome.errored is True
    # Safe-marked: it names the user's own config, so it persists verbatim.
    assert "nonsense" in (outcome.error_message or "")


def test_the_executor_locks_the_baseline_row_before_rewriting_it(
    graph: tuple[Session, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lost-update guard. The executor reads the observation list and writes
    that list plus one entry, so two overlapping runs of the SAME check (an
    overdue schedule firing while a Run-Now is in flight) would both read the same
    list and the second commit would silently drop the first's measurement — a gap
    nothing downstream can detect, since a rolling window has no per-observation
    identity. `FOR UPDATE` serialises them for the rest of the transaction.

    Asserted against the SQL that reaches Postgres, not a call spy."""
    session, conn, check = graph
    _executor(session, conn, 100, monkeypatch)(check)  # establish the row
    session.flush()
    with _captured_sql(session) as statements:
        _executor(session, conn, 110, monkeypatch)(check)
        session.flush()
    selects = _baseline_selects(statements)
    assert selects, "the executor must read the baseline row"
    assert any("FOR UPDATE" in s.upper() for s in selects)


def test_a_dry_run_takes_no_write_lock(
    graph: tuple[Session, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A preview writes nothing, so locking would let a check-editor preview block
    a real scheduled run for the length of its transaction."""
    session, conn, check = graph
    _executor(session, conn, 100, monkeypatch)(check)
    session.flush()
    with _captured_sql(session) as statements:
        _executor(session, conn, 110, monkeypatch, persist=False)(check)
    selects = _baseline_selects(statements)
    assert selects, "the preview must still read the baseline row"
    assert not any("FOR UPDATE" in s.upper() for s in selects)


def test_the_result_reports_when_learning_started_and_when_it_last_updated(
    graph: tuple[Session, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two different questions, and only one of the timestamps moves. This kind
    rewrites its baseline on EVERY run, so reporting `captured_at` alone would
    show day 1 forever — six months in, a payload claiming the baseline was
    captured in January is indistinguishable from a monitor that stopped
    learning. `captured_at` has no `onupdate`; `updated_at` does."""
    session, conn, check = graph
    _executor(session, conn, 100, monkeypatch)(check)  # first capture
    session.flush()
    row = get_baseline(session, check.id)
    assert row is not None
    # Backdate the whole row so "learning started" is unambiguously in the past.
    session.execute(
        text(
            "UPDATE monitor_baselines SET captured_at = captured_at - interval '30 days', "
            "updated_at = updated_at - interval '30 days' WHERE id = :i"
        ),
        {"i": row.id},
    )
    session.expire_all()
    backdated_capture = get_baseline(session, check.id)
    assert backdated_capture is not None
    captured_iso = backdated_capture.captured_at.isoformat()

    _executor(session, conn, 110, monkeypatch)(check)  # rewrites the row → bumps updated_at
    session.flush()
    session.expire_all()
    outcome = _executor(session, conn, 120, monkeypatch)(check)
    observed = outcome.observed_value
    assert observed is not None
    # Learning started 30 days ago and has NOT been re-stamped…
    assert observed["baseline_captured_at"] == captured_iso
    # …while the window was last written by the preceding run.
    assert observed["baseline_updated_at"] > observed["baseline_captured_at"]


def test_an_unparseable_timestamp_cell_travels_structurally_not_in_the_message(
    graph: tuple[Session, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#989, applied to this kind. An anomaly over `freshness_age_hours` hits the
    exact same garbage-cell case a plain freshness monitor does, and
    `run_monitor_specs` puts the offending cell on `observed_value` — never in the
    message, which is persisted verbatim and rendered wherever a result is shown,
    while `observed_value` passes through the read layer's column-policy
    redaction. Dropping it here would make the same error diagnosable on one kind
    and undiagnosable on the other."""
    session, conn, check = graph
    check.config = {"target_metric": "freshness_age_hours", "column": "order_ts"}
    outcome = _executor(session, conn, "13/07/2026", monkeypatch)(check)
    assert outcome.errored is True
    assert "13/07/2026" not in (outcome.error_message or "")
    assert outcome.observed_value == {"unparsed_value": "13/07/2026", "column": "order_ts"}
    # …and the message still names the column, so the error stays actionable.
    assert "order_ts" in (outcome.error_message or "")


def test_an_error_without_a_cell_carries_no_observed_value(
    graph: tuple[Session, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The type-mismatch branch names only the TYPE, so there is nothing to
    redact and `observed_value` must stay empty — pinned so a later edit doesn't
    quietly start echoing there instead."""
    session, conn, check = graph
    check.config = {"target_metric": "freshness_age_hours", "column": "order_ts"}
    outcome = _executor(session, conn, 12345, monkeypatch)(check)
    assert outcome.errored is True
    assert "12345" not in (outcome.error_message or "")
    assert outcome.observed_value is None


def test_rebaseline_restarts_the_cold_start(
    graph: tuple[Session, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a deliberate step change the old history is misleading; re-baselining
    drops it and the next run legitimately reports the cold-start skip again."""
    from backend.app.services.monitor_baseline import rebaseline

    session, conn, check = graph
    for value in (100, 110, 90, 105):
        _executor(session, conn, value, monkeypatch)(check)
        session.flush()
    assert rebaseline(session, check) is True
    session.flush()
    outcome = _executor(session, conn, 400, monkeypatch)(check)
    assert outcome.skipped is True
    assert outcome.observed_value is not None
    assert outcome.observed_value["points"] == 0
