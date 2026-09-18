"""Database-growth and scheduler-dispatch cases.

These run against a SCRATCH database (`PERF_DATABASE_URL`), never the app one,
and they call the service-layer functions the API routes call — `list_runs`,
`count_runs`, `list_results`, `dashboard_summary`, `list_incidents`,
`list_pipeline_runs`, `_dispatch_due_schedules` — not hand-written SQL.
"""

from __future__ import annotations

import os
import statistics
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from backend.scripts.perf.harness import Case, Metric, register

ROW_TIERS = {"10k": 10_000, "100k": 100_000, "1m": 1_000_000}
#: The subset CI runs — statement counts do not depend on scale, so the gate does not need one.
CI_TIER = "10k"
#: Results per seeded run — a run-detail read must return a realistic page.
RESULTS_PER_RUN = 25
SCHEDULE_TIERS = {"10": 10, "1k": 1_000, "10k": 10_000}
REPEATS = 15


class PerfDatabaseUnsetError(RuntimeError):
    pass


def database_url() -> str:
    url = os.environ.get("PERF_DATABASE_URL")
    if not url:
        raise PerfDatabaseUnsetError(
            "set PERF_DATABASE_URL to a SCRATCH database (never the app database)"
        )
    if url.rsplit("/", 1)[-1] in {"dataq", "dataq_test"}:
        raise PerfDatabaseUnsetError(f"refusing to benchmark against {url.rsplit('/', 1)[-1]!r}")
    return url


@contextmanager
def _session() -> Iterator[Session]:
    engine = create_engine(database_url())
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ────────────────────────────────── seeding ──────────────────────────────────

_FIXTURE_SQL = """
INSERT INTO users (id, email, display_name, role)
SELECT :owner_id, 'perf-owner@example.test', 'Perf Owner', 'member'
WHERE NOT EXISTS (SELECT 1 FROM users WHERE id = :owner_id);

INSERT INTO connections (id, name, type, env, config)
SELECT :conn_id, 'perf-connection', 'snowflake', 'dev', '{}'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM connections WHERE id = :conn_id);

INSERT INTO assets (namespace, name, env, connection_id)
SELECT 'perf://warehouse', 'table_' || g, 'dev', :conn_id
FROM generate_series(1, 400) g
WHERE NOT EXISTS (SELECT 1 FROM assets WHERE namespace = 'perf://warehouse');

INSERT INTO suites (id, name, connection_id, target, created_by)
SELECT gen_random_uuid(), 'perf suite ' || g, :conn_id,
       '{"table": "ORDERS", "schema": "RETAIL"}'::jsonb, :owner_id
FROM generate_series(1, 10) g
WHERE NOT EXISTS (SELECT 1 FROM suites WHERE name LIKE 'perf suite %');

INSERT INTO checks (suite_id, name, kind, expectation_type, config)
SELECT s.id, 'perf check ' || g, 'expectation', 'expect_column_values_to_not_be_null', '{}'::jsonb
FROM suites s CROSS JOIN generate_series(1, 40) g
WHERE s.name LIKE 'perf suite %'
  AND NOT EXISTS (SELECT 1 FROM checks WHERE name LIKE 'perf check %');
"""

_RUNS_SQL = """
WITH suite_ids AS (
    SELECT array_agg(id ORDER BY name) AS ids FROM suites WHERE name LIKE 'perf suite %'
)
INSERT INTO runs (id, suite_id, status, created_at, started_at, finished_at)
SELECT gen_random_uuid(),
       s.ids[1 + (g % array_length(s.ids, 1))],
       (ARRAY['succeeded','failed','running','queued','cancelled'])[1 + (g % 5)],
       now() - (g || ' seconds')::interval,
       now() - (g || ' seconds')::interval,
       now() - (g || ' seconds')::interval + interval '12 seconds'
FROM generate_series(1, :rows) g, suite_ids s;
"""

_RESULTS_SQL = """
WITH check_ids AS (
    SELECT array_agg(id) AS ids FROM (
        SELECT id FROM checks WHERE name LIKE 'perf check %' LIMIT :per_run
    ) c
), target_runs AS (
    SELECT id, created_at FROM runs ORDER BY created_at DESC LIMIT :run_count
)
INSERT INTO results (id, run_id, check_id, status, metric_value, duration_ms, created_at)
SELECT gen_random_uuid(), r.id, c.ids[g], (ARRAY['pass','pass','warn','fail'])[1 + (g % 4)],
       g * 1.5, 40 + g, r.created_at
FROM target_runs r, generate_series(1, :per_run) g, check_ids c;
"""

_INCIDENTS_SQL = """
WITH asset_ids AS (
    SELECT array_agg(id ORDER BY name) AS ids FROM assets WHERE namespace = 'perf://warehouse'
), suite_check AS (
    SELECT array_agg(id) AS check_ids, array_agg(suite_id) AS suite_ids
    FROM (SELECT id, suite_id FROM checks WHERE name LIKE 'perf check %' LIMIT 400) c
)
INSERT INTO incidents (id, asset_id, check_id, suite_id, status, last_seen_at,
                       created_at, updated_at)
SELECT gen_random_uuid(),
       a.ids[1 + ((g / 400) % array_length(a.ids, 1))],
       sc.check_ids[1 + (g % array_length(sc.check_ids, 1))],
       sc.suite_ids[1 + (g % array_length(sc.suite_ids, 1))],
       (ARRAY['open','acknowledged','resolved'])[1 + (g % 3)],
       now() - (g || ' seconds')::interval,
       now() - (g || ' seconds')::interval,
       now() - (g || ' seconds')::interval
FROM generate_series(0, :rows - 1) g, asset_ids a, suite_check sc
ON CONFLICT DO NOTHING;
"""

_PIPELINE_RUNS_SQL = """
INSERT INTO pipeline_runs (id, provider, connection_id, provider_run_id, pipeline_or_dag_id,
                           env, status, created_at)
SELECT gen_random_uuid(),
       (ARRAY['adf','airflow','dbt'])[1 + (g % 3)], :conn_id, 'perf-' || (g + :offset),
       'pipeline_' || (g % 20), 'dev',
       (ARRAY['succeeded','failed','running'])[1 + (g % 3)],
       now() - (g || ' seconds')::interval
FROM generate_series(1, :rows) g;
"""

OWNER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
CONN_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")

_TRUNCATE = text(
    "TRUNCATE results, incidents, pipeline_runs, runs, schedules, checks, suites, "
    "assets, connections, users RESTART IDENTITY CASCADE"
)


def seed(rows: int, *, echo: Callable[[str], None] = print) -> None:
    """Seed the scratch database to `rows` runs.

    A database left larger by an earlier tier is TRUNCATED first: seeding only
    upwards would let a "10k" tier report itself while measuring a million rows.
    """
    with _session() as session:
        have = session.scalar(text("SELECT count(*) FROM runs")) or 0
        if have > rows:
            echo(f"scratch database holds {have} runs, over the {rows} tier — truncating")
            session.execute(_TRUNCATE)
            session.commit()
        for statement in _FIXTURE_SQL.split(";\n"):
            if statement.strip():
                session.execute(text(statement), {"owner_id": OWNER_ID, "conn_id": CONN_ID})
        session.commit()

        have = session.scalar(text("SELECT count(*) FROM runs")) or 0
        if have < rows:
            echo(f"seeding runs {have} -> {rows}")
            session.execute(text(_RUNS_SQL), {"rows": rows - have})
            session.commit()
        have_results = session.scalar(text("SELECT count(*) FROM results")) or 0
        want_results = rows
        if have_results < want_results:
            echo(f"seeding results {have_results} -> {want_results}")
            session.execute(
                text(_RESULTS_SQL),
                {"per_run": RESULTS_PER_RUN, "run_count": want_results // RESULTS_PER_RUN},
            )
            session.commit()
        have_incidents = session.scalar(text("SELECT count(*) FROM incidents")) or 0
        want_incidents = min(rows, 120_000)
        if have_incidents < want_incidents:
            echo(f"seeding incidents {have_incidents} -> {want_incidents}")
            session.execute(text(_INCIDENTS_SQL), {"rows": want_incidents})
            session.commit()
        have_pipeline = session.scalar(text("SELECT count(*) FROM pipeline_runs")) or 0
        want_pipeline = min(rows, 120_000)
        if have_pipeline < want_pipeline:
            echo(f"seeding pipeline_runs {have_pipeline} -> {want_pipeline}")
            session.execute(
                text(_PIPELINE_RUNS_SQL),
                {
                    "rows": want_pipeline - have_pipeline,
                    "offset": have_pipeline,
                    "conn_id": CONN_ID,
                },
            )
            session.commit()
    _vacuum_analyze()
    echo("vacuum analyze done")


def create_database(*, echo: Callable[[str], None] = print) -> None:
    """Create the scratch database named by `PERF_DATABASE_URL` if it is absent."""
    url = database_url()
    name = url.rsplit("/", 1)[-1]
    engine = create_engine(f"{url.rsplit('/', 1)[0]}/postgres", isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            exists = conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name})
            if exists:
                echo(f"{name} already exists")
                return
            conn.execute(text(f'CREATE DATABASE "{name}"'))
            echo(f"created {name}")
    finally:
        engine.dispose()


def _vacuum_analyze() -> None:
    """VACUUM cannot run inside a transaction block — its own AUTOCOMMIT connection."""
    engine = create_engine(database_url(), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("VACUUM ANALYZE"))
    finally:
        engine.dispose()


def reset(*, echo: Callable[[str], None] = print) -> None:
    """Empty the scratch database's benchmark rows."""
    with _session() as session:
        session.execute(_TRUNCATE)
        session.commit()
        echo("scratch database truncated")


# ─────────────────────────────── measurement ───────────────────────────────


@contextmanager
def _counted(session: Session) -> Iterator[list[str]]:
    """Every SQL statement the measured call issues — the N+1 tripwire."""
    seen: list[str] = []

    def before(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        seen.append(statement)

    event.listen(session.get_bind(), "before_cursor_execute", before)
    try:
        yield seen
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", before)


def _percentiles(samples: list[float]) -> tuple[float, float]:
    ordered = sorted(samples)
    p50 = statistics.median(ordered)
    p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
    return p50 * 1000, p95 * 1000


def _time_read(call: Callable[[], Any], *, repeats: int = REPEATS) -> list[float]:
    call()  # warm the plan cache; the first call's compile cost is not the read
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        call()
        samples.append(time.perf_counter() - started)
    return samples


def _reads(session: Session) -> dict[str, Callable[[], Any]]:
    from backend.app.services import (
        dashboard_service,
        incident_service,
        orchestration_service,
        run_service,
    )

    newest = session.execute(
        text("SELECT id FROM runs ORDER BY created_at DESC LIMIT 1")
    ).scalar_one()

    return {
        "runs_list": lambda: run_service.list_runs(session, user_id=OWNER_ID, limit=50),
        "runs_count": lambda: run_service.count_runs(session, user_id=OWNER_ID),
        "run_detail_results": lambda: run_service.list_results(session, newest),
        "dashboard_summary": lambda: dashboard_service.dashboard_summary(
            session, user_id=OWNER_ID, window_days=7
        ),
        "incidents_list": lambda: incident_service.list_incidents(
            session, user_id=OWNER_ID, limit=100
        ),
        "incidents_count": lambda: incident_service.count_incidents(session, user_id=OWNER_ID),
        "pipeline_runs_list": lambda: orchestration_service.list_pipeline_runs(session, limit=50),
        "pipeline_runs_count": lambda: orchestration_service.count_pipeline_runs(session),
    }


def _db_reads() -> list[Metric]:
    metrics: list[Metric] = []
    with _session() as session:
        actual = session.scalar(text("SELECT count(*) FROM runs")) or 0
        metrics.append(Metric("seeded_runs", float(actual), "rows", "observe"))
        metrics.append(
            Metric(
                "seeded_results",
                float(session.scalar(text("SELECT count(*) FROM results")) or 0),
                "rows",
                "observe",
            )
        )
        for name, call in _reads(session).items():
            with _counted(session) as statements:
                call()
            samples = _time_read(call)
            p50, p95 = _percentiles(samples)
            metrics.append(Metric(f"{name}_p50_ms", p50, "ms", "observe"))
            metrics.append(Metric(f"{name}_p95_ms", p95, "ms", "observe"))
            metrics.append(
                Metric(f"{name}_statements", float(len(statements)), "queries", "strict")
            )
    return metrics


def _bind_db() -> Callable[[], list[Metric]]:
    def run() -> list[Metric]:
        return _db_reads()

    return run


def _prepare_db(rows: int) -> Callable[[], None]:
    def prepare() -> None:
        seed(rows, echo=lambda msg: None)

    return prepare


# ──────────────────────────── scheduler dispatch ────────────────────────────


def _seed_schedules(session: Session, due: int) -> None:
    session.execute(text("DELETE FROM schedules"))
    session.execute(
        text("""
            WITH suite_ids AS (
                SELECT array_agg(id ORDER BY name) AS ids FROM suites WHERE name LIKE 'perf suite %'
            )
            INSERT INTO schedules (id, suite_id, cron, timezone, enabled, next_run_at,
                                   created_at, updated_at)
            SELECT gen_random_uuid(), s.ids[1 + (g % array_length(s.ids, 1))], '*/5 * * * *',
                   'UTC', true, now() - interval '1 minute', now(), now()
            FROM generate_series(1, :due) g, suite_ids s
            """),
        {"due": due},
    )
    session.commit()


def _dispatch(due: int) -> list[Metric]:
    from backend.app.services import run_dispatch
    from backend.app.worker import tasks

    with _session() as session:
        _seed_schedules(session, due)
        original = run_dispatch.dispatch_or_fail
        # The enqueue seam only — everything DB-side (the SKIP LOCKED claim, the
        # cron advance, the run INSERT, the commit) runs for real.
        run_dispatch.dispatch_or_fail = lambda *args, **kwargs: True
        try:
            started = time.perf_counter()
            summary = tasks._dispatch_due_schedules(session)
            elapsed = time.perf_counter() - started
        finally:
            run_dispatch.dispatch_or_fail = original

    return [
        Metric("schedules_due", float(summary["due"]), "schedules", "strict"),
        Metric("dispatch_wall_s", elapsed, "s", "observe"),
        Metric(
            "schedules_per_s",
            summary["due"] / elapsed if elapsed else 0.0,
            "schedules/s",
            "observe",
        ),
        Metric("ms_per_schedule", (elapsed * 1000) / max(summary["due"], 1), "ms", "observe"),
    ]


def _bind_dispatch(due: int) -> Callable[[], list[Metric]]:
    def run() -> list[Metric]:
        return _dispatch(due)

    return run


def _register() -> None:
    for tier, rows in ROW_TIERS.items():
        register(
            Case(
                id=f"db_read.{tier}",
                family="db_read",
                datasource="postgres",
                tier=f"{tier} runs / {tier} results",
                fn=_bind_db(),
                prepare=_prepare_db(rows),
                tags=("full", "ci") if tier == CI_TIER else ("full",),
            )
        )
    for tier, due in SCHEDULE_TIERS.items():
        register(
            Case(
                id=f"scheduler.{tier}",
                family="scheduler",
                datasource="postgres",
                tier=f"{due} due schedules",
                fn=_bind_dispatch(due),
                prepare=_prepare_db(ROW_TIERS[CI_TIER]),
                tags=("full", "ci") if tier == "10" else ("full",),
            )
        )


_register()
