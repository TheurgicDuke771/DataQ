"""Evidence-card assembly tests (ADR 0034 #761) against a real Postgres.

The card is assembled from existing data only and **must never carry
``sample_failures`` content** (PII). This exercises each layer + the redaction
guarantee end to end.

Skips without TEST_DATABASE_URL."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.app.db.models import (
    Asset,
    Check,
    Connection,
    LineageEdge,
    PipelineRun,
    Result,
    Run,
    User,
)
from backend.app.services import suite_service
from backend.app.services.incident_evidence import build_evidence

_SF_CONFIG = {"account": "ab12345.eu-west-1", "database": "ANALYTICS", "schema": "PUBLIC"}


def _user(db: Any) -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@ex.com")
    db.add(u)
    db.flush()
    return u


def _conn(db: Any, owner: User) -> Connection:
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config=_SF_CONFIG,
        secret_ref="kv-x",
        created_by=owner.id,
    )
    db.add(conn)
    db.commit()
    return conn


def _suite(db: Any, owner: User, conn: Connection, table: str = "ORDERS") -> Any:
    return suite_service.create_suite(
        db,
        name=f"s-{uuid.uuid4().hex[:6]}",
        description=None,
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": table},
    )


def _check(db: Any, suite: Any, name: str = "orders_not_null") -> Check:
    c = Check(
        suite_id=suite.id,
        name=name,
        kind="expectation",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "id"},
    )
    db.add(c)
    db.flush()
    return c


@pytest.fixture
def world(db_session: Any) -> dict[str, Any]:
    owner = _user(db_session)
    conn = _conn(db_session, owner)
    suite = _suite(db_session, owner, conn)
    check = _check(db_session, suite)
    asset = db_session.get(Asset, suite.asset_id)
    return {"owner": owner, "conn": conn, "suite": suite, "check": check, "asset": asset}


def _run(db: Any, suite: Any, triggered_by: str = "manual") -> Run:
    run = Run(
        suite_id=suite.id, status="succeeded", triggered_by=triggered_by, asset_id=suite.asset_id
    )
    db.add(run)
    db.flush()
    return run


def test_card_has_identity_and_failing_result(db_session: Any, world: dict[str, Any]) -> None:
    run = _run(db_session, world["suite"])
    result = Result(run_id=run.id, check_id=world["check"].id, status="fail", metric_value=0.42)
    db_session.add(result)
    db_session.commit()
    card = build_evidence(
        db_session, run=run, result=result, check=world["check"], asset=world["asset"]
    )
    assert card["check"]["name"] == "orders_not_null"
    assert card["asset"]["name"] == "ANALYTICS.PUBLIC.ORDERS"
    assert card["failing_result"]["status"] == "fail"
    assert card["failing_result"]["metric_value"] == 0.42
    assert "generated_at" in card
    assert card["profile_diff"] is None  # documented null placeholder


def test_card_never_carries_sample_failures(db_session: Any, world: dict[str, Any]) -> None:
    """The PII floor: even a result stuffed with raw failing rows yields a card
    with NO sample content anywhere."""
    secret = "victim@example.com"
    run = _run(db_session, world["suite"])
    result = Result(
        run_id=run.id,
        check_id=world["check"].id,
        status="fail",
        metric_value=0.9,
        sample_failures={"partial_unexpected_list": [secret], "unexpected_count": 1},
    )
    db_session.add(result)
    db_session.commit()
    card = build_evidence(
        db_session, run=run, result=result, check=world["check"], asset=world["asset"]
    )
    import json

    blob = json.dumps(card)
    assert secret not in blob
    assert "sample_failures" not in blob
    assert "partial_unexpected_list" not in blob


def test_metric_trend_layer(db_session: Any, world: dict[str, Any]) -> None:
    # Three historical results + the latest breach. `created_at` is set explicitly
    # with increasing timestamps: the `db_session` fixture runs everything in ONE
    # transaction, so `func.now()` (transaction-start) would tie every row and make
    # the newest-first ordering nondeterministic (in production each run commits in
    # its own transaction, so timestamps differ naturally).
    base = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)
    for i, metric in enumerate((0.1, 0.2, 0.3)):
        r = _run(db_session, world["suite"])
        db_session.add(
            Result(
                run_id=r.id,
                check_id=world["check"].id,
                status="warn",
                metric_value=metric,
                created_at=base + timedelta(minutes=i),
            )
        )
        db_session.commit()
    latest_run = _run(db_session, world["suite"])
    latest = Result(
        run_id=latest_run.id,
        check_id=world["check"].id,
        status="fail",
        metric_value=0.9,
        created_at=base + timedelta(minutes=10),
    )
    db_session.add(latest)
    db_session.commit()
    card = build_evidence(
        db_session, run=latest_run, result=latest, check=world["check"], asset=world["asset"]
    )
    trend = card["metric_trend"]
    assert len(trend) == 4
    assert {r["metric_value"] for r in trend} == {0.1, 0.2, 0.3, 0.9}
    assert trend[0]["metric_value"] == 0.9  # newest first (explicit timestamps)


def test_sibling_checks_layer(db_session: Any, world: dict[str, Any]) -> None:
    sibling = _check(db_session, world["suite"], name="orders_positive")
    run = _run(db_session, world["suite"])
    failing = Result(run_id=run.id, check_id=world["check"].id, status="fail")
    db_session.add(failing)
    db_session.add(Result(run_id=run.id, check_id=sibling.id, status="pass"))
    db_session.commit()
    card = build_evidence(
        db_session, run=run, result=failing, check=world["check"], asset=world["asset"]
    )
    siblings = {s["check_name"]: s["status"] for s in card["sibling_checks"]}
    assert siblings == {"orders_positive": "pass"}  # excludes the failing check itself


def test_blast_radius_layer(db_session: Any, world: dict[str, Any]) -> None:
    downstream = Asset(namespace="snowflake://ab12345.eu-west-1", name="ANALYTICS.MART.REVENUE")
    db_session.add(downstream)
    db_session.flush()
    db_session.add(
        LineageEdge(
            upstream_asset_id=world["asset"].id,
            downstream_asset_id=downstream.id,
            source="dbt",
            connection_id=world["conn"].id,
        )
    )
    run = _run(db_session, world["suite"])
    result = Result(run_id=run.id, check_id=world["check"].id, status="fail")
    db_session.add(result)
    db_session.commit()
    card = build_evidence(
        db_session, run=run, result=result, check=world["check"], asset=world["asset"]
    )
    names = {n["name"] for n in card["downstream_blast_radius"]}
    assert "ANALYTICS.MART.REVENUE" in names


def test_upstream_pipeline_layer_with_delay(db_session: Any, world: dict[str, Any]) -> None:
    """A run triggered by an orchestration pipeline gets the upstream pipeline run +
    a delay-vs-history number (this run slower than the prior baseline)."""
    conn_id = world["conn"].id
    now = datetime.now(UTC)
    # Prior baseline: a fast succeeded run (60s).
    db_session.add(
        PipelineRun(
            provider="airflow",
            connection_id=conn_id,
            provider_run_id="prev-1",
            pipeline_or_dag_id="load_orders",
            env="dev",
            status="succeeded",
            started_at=now - timedelta(minutes=30),
            finished_at=now - timedelta(minutes=29),
            created_at=now - timedelta(minutes=30),
        )
    )
    # This pipeline run: slow (600s).
    db_session.add(
        PipelineRun(
            provider="airflow",
            connection_id=conn_id,
            provider_run_id="run-2",
            pipeline_or_dag_id="load_orders",
            env="dev",
            status="succeeded",
            started_at=now - timedelta(minutes=11),
            finished_at=now - timedelta(minutes=1),
            created_at=now - timedelta(minutes=11),
        )
    )
    db_session.commit()
    run = _run(db_session, world["suite"], triggered_by="airflow:load_orders:run-2")
    result = Result(run_id=run.id, check_id=world["check"].id, status="fail")
    db_session.add(result)
    db_session.commit()
    card = build_evidence(
        db_session, run=run, result=result, check=world["check"], asset=world["asset"]
    )
    up = card["upstream_pipeline_run"]
    assert up is not None
    assert up["pipeline_or_dag_id"] == "load_orders"
    assert up["duration_seconds"] == pytest.approx(600, abs=1)
    assert up["delay_seconds_vs_history"] == pytest.approx(540, abs=2)  # 600 - 60


def test_upstream_pipeline_none_for_manual_run(db_session: Any, world: dict[str, Any]) -> None:
    run = _run(db_session, world["suite"], triggered_by="manual")
    result = Result(run_id=run.id, check_id=world["check"].id, status="fail")
    db_session.add(result)
    db_session.commit()
    card = build_evidence(
        db_session, run=run, result=result, check=world["check"], asset=world["asset"]
    )
    assert card["upstream_pipeline_run"] is None


def test_card_degrades_with_none_check_and_asset(db_session: Any, world: dict[str, Any]) -> None:
    run = _run(db_session, world["suite"])
    result = Result(run_id=run.id, check_id=world["check"].id, status="fail")
    db_session.add(result)
    db_session.commit()
    card = build_evidence(db_session, run=run, result=result, check=None, asset=None)
    assert card["check"] is None
    assert card["asset"] is None
    assert card["downstream_blast_radius"] == []
