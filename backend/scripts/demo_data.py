"""Realistic demo dataset for local dev / full-stack E2E (idempotent).

Beyond the Week-1 probe fixtures, this seeds a *representative* dataset so the UI
and any end-to-end smoke run against something that looks real: connections
across all six types (the four datasources + the two orchestration providers),
several suites with varied GX expectations and severity thresholds, and one
cross-user share.

Everything goes through the **same service layer the API uses** (so configs are
validated exactly as a real create would be, and credentials are written through
the SecretStore) and every step is get-or-create, so re-running is a no-op.

Credentials here are obviously-fake placeholders — live `test()`/runs fail-soft
without real datasource access (the documented deferred smoke); the CRUD /
listing / authoring / dry-run-attempt paths are fully exercised.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.auth import _upsert_user
from backend.app.core.secrets import SecretStore
from backend.app.db.models import (
    Asset,
    Check,
    Connection,
    Incident,
    LineageEdge,
    PipelineRun,
    Result,
    Run,
    Suite,
    User,
)
from backend.app.services import (
    check_service,
    connection_service,
    incident_service,
    share_service,
    suite_service,
)
from backend.app.services.asset_service import upsert_assets

# A second collaborator so the sharing surface isn't empty.
ANALYST_OID = "demo-analyst-oid"
ANALYST_EMAIL = "analyst@dataq.local"
ANALYST_NAME = "Dana Analyst"

# (name, type, env, config, secret) — one per connection type. Configs match each
# adapter's `*Config` (extra="forbid"), so they validate like a real create.
_CONNECTIONS: list[tuple[str, str, str, dict[str, Any], str]] = [
    (
        "snowflake-analytics",
        "snowflake",
        "dev",
        {
            "account": "acme-analytics.us-east-1",
            "user": "DATAQ_SVC",
            "database": "ANALYTICS",
            "schema": "PUBLIC",
            "warehouse": "COMPUTE_WH",
            "auth_type": "password",
        },
        "demo-snowflake-dev-password",
    ),
    (
        "snowflake-analytics",
        "snowflake",
        "qa",
        {
            "account": "acme-analytics.us-east-1",
            "user": "DATAQ_SVC",
            "database": "ANALYTICS_QA",
            "schema": "PUBLIC",
            "warehouse": "COMPUTE_WH",
            "auth_type": "password",
        },
        "demo-snowflake-qa-password",
    ),
    (
        "s3-datalake",
        "s3",
        "prod",
        {
            "bucket": "acme-datalake",
            "region": "us-east-1",
            "auth_type": "access_key",
            "access_key_id": "AKIAEXAMPLEDEMO",
        },
        "demo-s3-secret-access-key",
    ),
    (
        "adls-raw",
        "adls_gen2",
        "dev",
        {
            "account_url": "https://acmeraw.blob.core.windows.net",
            "container": "raw",
            "auth_type": "sas",
        },
        "?sv=2023-demo-sas-token",
    ),
    (
        "uc-lakehouse",
        "unity_catalog",
        "uat",
        {
            "workspace_url": "https://adb-1234567890.5.azuredatabricks.net",
            "warehouse_id": "abc123demowarehouse",
        },
        "dapidemoPATtoken0123456789",
    ),
    (
        "adf-orchestrator",
        "adf",
        "prod",
        {
            "subscription_id": "00000000-0000-0000-0000-000000000000",
            "resource_group": "rg-data-platform",
            "factory_name": "acme-adf",
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "client_id": "22222222-2222-2222-2222-222222222222",
        },
        "demo-adf-sp-client-secret",
    ),
    (
        "airflow-dags",
        "airflow",
        "prod",
        {"base_url": "https://airflow.acme.internal", "auth_type": "token"},
        "demo-airflow-api-token",
    ),
]

# suite name → (connection name, env, description, [checks]).
# Each check: (name, expectation_type, config, warn, fail, critical).
_SUITES: list[tuple[str, str, str, str, list[tuple[str, str, dict[str, Any], Any, Any, Any]]]] = [
    (
        "Orders quality",
        "snowflake-analytics",
        "dev",
        "Daily integrity checks on the ANALYTICS.ORDERS table.",
        [
            (
                "order_id not null",
                "expect_column_values_to_not_be_null",
                {"column": "order_id"},
                None,
                None,
                None,
            ),
            (
                "order_id unique",
                "expect_column_values_to_be_unique",
                {"column": "order_id"},
                None,
                None,
                None,
            ),
            (
                "amount in range",
                "expect_column_values_to_be_between",
                {"column": "amount", "min_value": 0, "max_value": 100000},
                Decimal("1"),
                Decimal("5"),
                Decimal("10"),
            ),
            (
                "status in set",
                "expect_column_values_to_be_in_set",
                {"column": "status", "value_set": ["new", "paid", "shipped", "cancelled"]},
                None,
                None,
                None,
            ),
        ],
    ),
    (
        # A SECOND suite on the SAME connection + target as "Orders quality" so it
        # resolves to the SAME asset (ADR 0034) — the Assets page then renders
        # health across ≥2 composing suites of one asset (the #760 acceptance
        # criterion). Deliberately a different check angle (volume) on ORDERS.
        "Orders volume",
        "snowflake-analytics",
        "dev",
        "Row-volume monitoring on the ANALYTICS.ORDERS table (shares the asset).",
        [
            (
                "row count sane",
                "expect_table_row_count_to_be_between",
                {"min_value": 1, "max_value": 10000000},
                None,
                None,
                None,
            ),
        ],
    ),
    (
        "Customer files",
        "s3-datalake",
        "prod",
        "Schema + volume checks on the daily customer export drops.",
        [
            (
                "row count sane",
                "expect_table_row_count_to_be_between",
                {"min_value": 1, "max_value": 5000000},
                None,
                None,
                None,
            ),
            (
                "email present",
                "expect_column_values_to_not_be_null",
                {"column": "email"},
                None,
                None,
                None,
            ),
        ],
    ),
    (
        "Lakehouse events",
        "uc-lakehouse",
        "uat",
        "Validity checks on the streaming events Delta table.",
        [
            (
                "event_type in set",
                "expect_column_values_to_be_in_set",
                {"column": "event_type", "value_set": ["click", "view", "purchase"]},
                None,
                None,
                None,
            ),
        ],
    ),
]


def _get_or_create_connection(
    session: Session,
    *,
    name: str,
    conn_type: str,
    env: str,
    config: dict[str, Any],
    secret: str,
    owner: User,
    secret_store: SecretStore,
) -> Connection:
    existing = session.scalar(
        select(Connection).where(Connection.name == name, Connection.env == env)
    )
    if existing is not None:
        return existing
    return connection_service.create_connection(
        session,
        name=name,
        conn_type=conn_type,
        env=env,
        config=config,
        secret=secret,
        created_by=owner.id,
        secret_store=secret_store,
    )


# Per-suite run target (#215), datasource-shaped — so the seeded suites are
# runnable and the Results page has a target to show. Keyed by suite name; each
# is valid for its connection's datasource type (table for SQL, path for flat
# files, table+catalog for Unity Catalog), validated by `run_target` on set.
_SUITE_TARGETS: dict[str, dict[str, Any]] = {
    "Orders quality": {"table": "ORDERS", "schema": "PUBLIC"},
    # Same target as "Orders quality" → same resolved asset (ADR 0034, #760).
    "Orders volume": {"table": "ORDERS", "schema": "PUBLIC"},
    "Customer files": {"path": "customers/2026-06-01.csv", "file_format": "csv"},
    "Lakehouse events": {"table": "events", "schema": "telemetry", "catalog": "main"},
}


def _get_or_create_suite(
    session: Session, *, name: str, connection: Connection, description: str, owner: User
) -> Suite:
    existing = session.scalar(
        select(Suite).where(Suite.name == name, Suite.connection_id == connection.id)
    )
    suite = existing or suite_service.create_suite(
        session,
        name=name,
        description=description,
        connection_id=connection.id,
        created_by=owner.id,
    )
    # Backfill the run target if missing (also upgrades suites seeded before #215).
    target = _SUITE_TARGETS.get(name)
    if target is not None and suite.target is None:
        suite_service.update_suite(session, suite.id, target=target)
    return suite


def _ensure_check(
    session: Session,
    *,
    suite: Suite,
    name: str,
    expectation_type: str,
    config: dict[str, Any],
    warn: Any,
    fail: Any,
    critical: Any,
) -> None:
    existing = session.scalar(select(Check).where(Check.suite_id == suite.id, Check.name == name))
    if existing is not None:
        return
    check_service.create_check(
        session,
        suite_id=suite.id,
        name=name,
        kind="expectation",
        expectation_type=expectation_type,
        config=config,
        warn_threshold=warn,
        fail_threshold=fail,
        critical_threshold=critical,
    )


# A seeded run's results: (check name, status, metric_value, observed_value,
# expected_value, sample_failures). `metric_value` is the unexpected-% badness
# scalar (ADR 0012); operational statuses (`error`/`skip`) carry NO metric (no
# penalty weight, #122) so theirs is None. `sample_failures` (usually None)
# feeds the run-detail redacted-sample drill-down (#226/#415) — the read API
# redacts it column-aware, the seed stores the raw runner shape.
_SeedResult = tuple[
    str,
    str,
    Decimal | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]

# Run 1 — a pass/pass/warn/fail spread so the drill-down shows the severity tiers
# (ADR 0005/0016). The fail carries a failing-value sample: its tested column
# (`status`) is not PII, so the redactor lets the values surface (#415).
_SEED_RUN_RESULTS: list[_SeedResult] = [
    (
        "order_id not null",
        "pass",
        Decimal("0"),
        {"unexpected_percent": 0.0},
        {"min_value": None},
        None,
    ),
    (
        "order_id unique",
        "pass",
        Decimal("0"),
        {"unexpected_percent": 0.0},
        {"min_value": None},
        None,
    ),
    (
        "amount in range",
        "warn",
        Decimal("2.0"),
        {"unexpected_percent": 2.0},
        {"min_value": 0, "max_value": 100000},
        None,
    ),
    (
        "status in set",
        "fail",
        Decimal("6.0"),
        {"unexpected_percent": 6.0},
        {"value_set": ["new", "paid", "shipped", "cancelled"]},
        {
            "unexpected_count": 3,
            "unexpected_percent": 6.0,
            "partial_unexpected_list": ["unknwon", "REFNDED", "in transit??"],
        },
    ),
]

# Run 2 — the *operational* spectrum the first run doesn't cover: a `critical`
# breach, an `error` (the check's evaluation threw — distinct from a fail), and a
# `skip` (precondition unmet, not evaluated). Gives the Results page the full
# pass | warn | fail | critical | error | skip mix across the two runs.
_SEED_RUN_MIXED_RESULTS: list[_SeedResult] = [
    (
        "order_id not null",
        "pass",
        Decimal("0"),
        {"unexpected_percent": 0.0},
        {"min_value": None},
        None,
    ),
    (
        "status in set",
        "critical",
        Decimal("18.0"),
        {"unexpected_percent": 18.0},
        {"value_set": ["new", "paid", "shipped", "cancelled"]},
        None,
    ),
    (
        "amount in range",
        "error",
        None,
        {"error": 'column "amount" could not be cast to NUMERIC'},
        None,
        None,
    ),
    (
        "order_id unique",
        "skip",
        None,
        {"reason": "upstream load incomplete — not evaluated"},
        None,
        None,
    ),
]


def _seed_result_run(
    session: Session,
    *,
    suite: Suite,
    checks: dict[str, Check],
    marker: str,
    results: list[_SeedResult],
    started_at: datetime,
    finished_at: datetime,
) -> None:
    """Seed one succeeded run carrying `results`, mapping result rows to the
    suite's checks by name. A run can succeed while individual checks `error`/
    `skip` — the run status is execution lifecycle, the result status is the
    per-check outcome."""
    run = Run(
        suite_id=suite.id,
        status="succeeded",
        triggered_by=marker,
        # The asset stamped at dispatch (ADR 0034) — also the incident anchor.
        asset_id=suite.asset_id,
        # created_at tracks started_at so the Results list (ordered created_at
        # desc) is deterministic across seeds — runs share one transaction, so
        # the server-default now() would tie.
        created_at=started_at,
        started_at=started_at,
        finished_at=finished_at,
    )
    session.add(run)
    session.flush()  # assign run.id for the result FKs
    for name, status, metric, observed, expected, sample in results:
        check = checks.get(name)
        if check is None:  # suite without the expected check (shouldn't happen) — skip
            continue
        session.add(
            Result(
                run_id=run.id,
                check_id=check.id,
                status=status,
                metric_value=metric,
                observed_value=observed,
                expected_value=expected,
                sample_failures=sample,
            )
        )


def _seed_runs(session: Session, *, suite: Suite) -> int:
    """Seed runs for `suite`: a severity-spread run, an operational-spectrum run,
    and a terminal-`failed` run.

    Idempotent on the `triggered_by` seed markers, so re-running adds nothing.
    Gives the Results page real content spanning the full result vocabulary —
    pass | warn | fail | critical | error | skip across the two succeeded runs —
    plus one terminal-`failed` run (adapter couldn't reach the warehouse, the
    documented deferred-smoke shape)."""
    succeeded_marker = "seed:run:succeeded"
    mixed_marker = "seed:run:mixed"
    failed_marker = "seed:run:failed"
    existing = set(
        session.scalars(
            select(Run.triggered_by).where(
                Run.suite_id == suite.id,
                Run.triggered_by.in_([succeeded_marker, mixed_marker, failed_marker]),
            )
        )
    )
    now = datetime.now(UTC)
    created = 0
    checks = {c.name: c for c in session.scalars(select(Check).where(Check.suite_id == suite.id))}

    if succeeded_marker not in existing:
        _seed_result_run(
            session,
            suite=suite,
            checks=checks,
            marker=succeeded_marker,
            results=_SEED_RUN_RESULTS,
            started_at=now - timedelta(minutes=5),
            finished_at=now - timedelta(minutes=4, seconds=48),
        )
        created += 1
    else:
        # Backfill (idempotent): DBs seeded before `sample_failures` rode the
        # seed keep their existing run (the marker guard skips it), so attach
        # the sample to the already-seeded fail result — the run-detail sample
        # drill-down then works on old stacks without wiping runs first.
        fail_check = checks.get("status in set")
        fail_sample = next((r[5] for r in _SEED_RUN_RESULTS if r[0] == "status in set"), None)
        if fail_check is not None and fail_sample is not None:
            run_id = session.scalars(
                select(Run.id).where(Run.suite_id == suite.id, Run.triggered_by == succeeded_marker)
            ).first()
            stale = session.scalars(
                select(Result).where(
                    Result.run_id == run_id,
                    Result.check_id == fail_check.id,
                    Result.sample_failures.is_(None),
                )
            ).first()
            if stale is not None:
                stale.sample_failures = fail_sample

    if mixed_marker not in existing:
        _seed_result_run(
            session,
            suite=suite,
            checks=checks,
            marker=mixed_marker,
            results=_SEED_RUN_MIXED_RESULTS,
            started_at=now - timedelta(minutes=3, seconds=30),
            finished_at=now - timedelta(minutes=3, seconds=18),
        )
        created += 1

    if failed_marker not in existing:
        session.add(
            Run(
                suite_id=suite.id,
                status="failed",
                triggered_by=failed_marker,
                created_at=now - timedelta(minutes=6),
                started_at=now - timedelta(minutes=6),
                finished_at=now - timedelta(minutes=5, seconds=58),
            )
        )
        created += 1

    # Normalize seed-run timestamps on EVERY reseed (existing rows included):
    # keeps the runs inside the dashboard's 7-day window on old stacks, and
    # keeps the operational-failure run (no results) OLDER than the
    # result-bearing runs — `suite_performance` reads each suite's LATEST run,
    # so a newest run without results would blank the Suite Performance panel.
    offsets: dict[str, tuple[timedelta, timedelta]] = {
        failed_marker: (timedelta(minutes=6), timedelta(minutes=5, seconds=58)),
        succeeded_marker: (timedelta(minutes=5), timedelta(minutes=4, seconds=48)),
        mixed_marker: (timedelta(minutes=3, seconds=30), timedelta(minutes=3, seconds=18)),
    }
    seed_runs = session.scalars(
        select(Run).where(Run.suite_id == suite.id, Run.triggered_by.in_(list(offsets)))
    )
    for run in seed_runs:
        start_offset, finish_offset = offsets[str(run.triggered_by)]
        run.created_at = now - start_offset
        run.started_at = now - start_offset
        run.finished_at = now - finish_offset

    return created


def _seed_second_suite_run(session: Session, *, suite: Suite) -> int:
    """Seed one passing run on a second suite that shares an asset (#760).

    Gives the shared-asset detail page real health rolled up across TWO composing
    suites (the acceptance criterion), not just the first. Idempotent on the marker."""
    marker = "seed:run:orders-volume"
    existing = session.scalar(
        select(Run.id).where(Run.suite_id == suite.id, Run.triggered_by == marker)
    )
    if existing is not None:
        return 0
    now = datetime.now(UTC)
    checks = {c.name: c for c in session.scalars(select(Check).where(Check.suite_id == suite.id))}
    _seed_result_run(
        session,
        suite=suite,
        checks=checks,
        marker=marker,
        results=[
            (
                "row count sane",
                "pass",
                Decimal("0"),
                {"observed_value": 42315},
                {"min_value": 1},
                None,
            )
        ],
        started_at=now - timedelta(minutes=4),
        finished_at=now - timedelta(minutes=3, seconds=52),
    )
    return 1


# Monitored orchestrator runs (`pipeline_runs` ≠ `runs`) for the monitoring feed:
# (provider, (connection name, env), provider_run_id, pipeline/dag id, status,
# failure_reason). provider_run_id is fixed so the upsert key dedupes re-runs.
_SEED_PIPELINE_RUNS: list[tuple[str, tuple[str, str], str, str, str, str | None]] = [
    ("adf", ("adf-orchestrator", "prod"), "seed-adf-0001", "daily_orders_load", "succeeded", None),
    (
        "airflow",
        ("airflow-dags", "prod"),
        "seed-airflow-0001",
        "events_streaming",
        "failed",
        "Task 'load_events' failed: upstream source timed out",
    ),
]


def _seed_pipeline_runs(session: Session, *, connections: dict[tuple[str, str], Connection]) -> int:
    """Seed orchestrator pipeline-runs for the monitoring tab. Idempotent on the
    (provider, provider_run_id) unique key."""
    now = datetime.now(UTC)
    created = 0
    for provider, conn_key, provider_run_id, pipeline_id, status, reason in _SEED_PIPELINE_RUNS:
        connection = connections.get(conn_key)
        if connection is None:
            continue
        already = session.scalar(
            select(PipelineRun.id).where(
                PipelineRun.provider == provider,
                PipelineRun.provider_run_id == provider_run_id,
            )
        )
        if already is not None:
            continue
        session.add(
            PipelineRun(
                provider=provider,
                connection_id=connection.id,
                provider_run_id=provider_run_id,
                pipeline_or_dag_id=pipeline_id,
                env=connection.env,
                status=status,
                started_at=now - timedelta(minutes=10),
                finished_at=now - timedelta(minutes=8),
                failure_reason=reason,
                last_updated_at=now - timedelta(minutes=8),
            )
        )
        created += 1
    return created


def _seed_incidents(session: Session, *, suite: Suite) -> int:
    """Roll the suite's seeded failing runs up into incidents through the REAL
    lifecycle engine (`sync_incidents_for_run`) so a fresh stack's AssetDetail
    shows a populated Incidents panel (#761) — not a hand-inserted row.

    Idempotent: skipped once the suite has any incident (a re-run of the seed must
    not attach spurious occurrences to the same runs). Chronological order so the
    severity-spread run opens and the mixed run attaches, like real runs would.
    """
    if session.scalar(select(Incident.id).where(Incident.suite_id == suite.id)) is not None:
        return 0
    runs = list(
        session.scalars(
            select(Run)
            .where(Run.suite_id == suite.id, Run.status == "succeeded")
            .order_by(Run.created_at)
        )
    )
    session.commit()  # sync reads committed state; flush the seeded rows first
    for run in runs:
        incident_service.sync_incidents_for_run(session, run_id=run.id)
    return int(
        session.scalar(
            select(func.count()).select_from(Incident).where(Incident.suite_id == suite.id)
        )
        or 0
    )


def seed_demo_data(session: Session, *, owner: User, secret_store: SecretStore) -> dict[str, int]:
    """Seed the representative dataset. Returns a count summary. Idempotent."""
    analyst = _upsert_user(
        session, aad_object_id=ANALYST_OID, email=ANALYST_EMAIL, display_name=ANALYST_NAME
    )

    connections: dict[tuple[str, str], Connection] = {}
    for name, conn_type, env, config, secret in _CONNECTIONS:
        conn = _get_or_create_connection(
            session,
            name=name,
            conn_type=conn_type,
            env=env,
            config=config,
            secret=secret,
            owner=owner,
            secret_store=secret_store,
        )
        connections[(name, env)] = conn

    suite_count = check_count = 0
    first_suite: Suite | None = None
    for suite_name, conn_name, env, description, checks in _SUITES:
        suite = _get_or_create_suite(
            session,
            name=suite_name,
            connection=connections[(conn_name, env)],
            description=description,
            owner=owner,
        )
        first_suite = first_suite or suite
        suite_count += 1
        for check_name, exp_type, config, warn, fail, critical in checks:
            _ensure_check(
                session,
                suite=suite,
                name=check_name,
                expectation_type=exp_type,
                config=config,
                warn=warn,
                fail=fail,
                critical=critical,
            )
            check_count += 1

    # Share the first suite with the analyst (idempotent: skip if already shared).
    if first_suite is not None:
        already = share_service.list_shares(session, first_suite.id, actor_id=owner.id)
        if not any(s.user_id == analyst.id for s in already):
            share_service.grant_share(
                session,
                first_suite.id,
                actor_id=owner.id,
                target_user_id=analyst.id,
                permission="edit",
            )

    # Runs + results on the first suite (the Results page surface) and a couple
    # of monitored pipeline-runs for the orchestration feed.
    run_count = _seed_runs(session, suite=first_suite) if first_suite is not None else 0
    # A second suite ("Orders volume") shares the ORDERS asset — seed a passing run
    # on it so the Assets page shows health across ≥2 composing suites (#760).
    volume_suite = session.scalar(
        select(Suite).where(
            Suite.name == "Orders volume",
            Suite.connection_id == connections[("snowflake-analytics", "dev")].id,
        )
    )
    if volume_suite is not None:
        run_count += _seed_second_suite_run(session, suite=volume_suite)
    pipeline_run_count = _seed_pipeline_runs(session, connections=connections)
    incident_count = _seed_incidents(session, suite=first_suite) if first_suite else 0
    edge_count = _seed_lineage(session, suite=first_suite) if first_suite else 0

    session.commit()
    return {
        "connections": len(connections),
        "suites": suite_count,
        "checks": check_count,
        "shares": 1,
        "runs": run_count,
        "pipeline_runs": pipeline_run_count,
        "incidents": incident_count,
        "lineage_edges": edge_count,
    }


# The demo lineage chain around the shared ORDERS asset. Two hops each way, so the
# asset's graph (#805) has real depth-≥2 provenance AND blast radius to lay out —
# without it the lineage view has nothing to show on a fresh install, and its e2e
# would have nothing to assert.
#
#   RAW.ORDERS_RAW ─┐
#                   ├─► PUBLIC.STG_ORDERS ─► PUBLIC.ORDERS ─► MART.REVENUE ─► BI.REVENUE_DAILY
#   RAW.CUSTOMERS ──┘
_DEMO_LINEAGE: tuple[tuple[str, str], ...] = (
    ("ANALYTICS.RAW.ORDERS_RAW", "ANALYTICS.PUBLIC.STG_ORDERS"),
    ("ANALYTICS.RAW.CUSTOMERS", "ANALYTICS.PUBLIC.STG_ORDERS"),
    ("ANALYTICS.PUBLIC.STG_ORDERS", "ANALYTICS.PUBLIC.ORDERS"),
    ("ANALYTICS.PUBLIC.ORDERS", "ANALYTICS.MART.REVENUE"),
    ("ANALYTICS.MART.REVENUE", "ANALYTICS.BI.REVENUE_DAILY"),
)


def _seed_lineage(session: Session, *, suite: Suite) -> int:
    """Seed a lineage neighbourhood around the suite's asset (idempotent).

    Anchored on the *resolved* asset's namespace so the seeded neighbours share its
    OpenLineage identity space (ADR 0034) — the same rule the dbt/Marquez importers
    follow, so a real import later dedupes against these rather than forking them.
    Edges are tagged ``source='dbt'`` (the manifest importer's provenance) and carry
    the suite's connection, matching what `refresh_dbt_edges` would have written.
    """
    if suite.asset_id is None:
        return 0
    anchor = session.get(Asset, suite.asset_id)
    if anchor is None:
        return 0

    names = {n for edge in _DEMO_LINEAGE for n in edge}
    asset_ids = upsert_assets(
        session,
        [
            {
                "namespace": anchor.namespace,
                "name": name,
                "env": anchor.env,
                "connection_id": suite.connection_id,
            }
            for name in sorted(names)
        ],
        preserve_provenance=True,
    )

    seeded = 0
    for upstream, downstream in _DEMO_LINEAGE:
        up_id = asset_ids[(anchor.namespace, upstream)]
        down_id = asset_ids[(anchor.namespace, downstream)]
        exists = session.scalar(
            select(LineageEdge.id).where(
                LineageEdge.upstream_asset_id == up_id,
                LineageEdge.downstream_asset_id == down_id,
                LineageEdge.source == "dbt",
            )
        )
        if exists is not None:
            continue
        session.add(
            LineageEdge(
                upstream_asset_id=up_id,
                downstream_asset_id=down_id,
                source="dbt",
                connection_id=suite.connection_id,
            )
        )
        seeded += 1
    session.flush()
    return seeded
