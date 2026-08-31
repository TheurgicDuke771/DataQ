"""Evidence-card assembly tests (ADR 0034 #761) against a real Postgres."""

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


def _monitor_check(db: Any, suite: Any, kind: str, name: str | None = None) -> Check:
    c = Check(
        suite_id=suite.id,
        name=name or f"{kind}_monitor",
        kind=kind,
        expectation_type=f"monitor:{kind}",
        config={"column": "id"} if kind == "freshness" else {},
        fail_threshold=1,
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
    with NO sample content anywhere.
    """
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
    # Three historical results + the latest breach.
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
    a delay-vs-history number (this run slower than the prior baseline).
    """
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


def test_upstream_pipeline_layer_resolves_airflow_default_run_id(
    db_session: Any, world: dict[str, Any]
) -> None:
    """#1713: Airflow's own default `run_id` (no custom id supplied) is a
    colon-bearing timestamp like `manual__2026-08-08T01:30:00+00:00` — the
    marker "<provider>:<pipeline_or_dag_id>:<provider_run_id>" then has MORE
    than two colons, and a naive split on the first/last one truncates
    `provider_run_id` to a few characters, so the real `PipelineRun` row
    (written with the correct, full run id) never matches.
    """
    conn_id = world["conn"].id
    now = datetime.now(UTC)
    run_id = "manual__2026-08-08T01:30:00+00:00"
    db_session.add(
        PipelineRun(
            provider="airflow",
            connection_id=conn_id,
            provider_run_id=run_id,
            pipeline_or_dag_id="flow_a_snowflake_load",
            env="dev",
            status="succeeded",
            started_at=now - timedelta(minutes=5),
            finished_at=now - timedelta(minutes=4),
            created_at=now - timedelta(minutes=5),
        )
    )
    db_session.commit()
    run = _run(
        db_session,
        world["suite"],
        triggered_by=f"airflow:flow_a_snowflake_load:{run_id}",
    )
    result = Result(run_id=run.id, check_id=world["check"].id, status="fail")
    db_session.add(result)
    db_session.commit()
    card = build_evidence(
        db_session, run=run, result=result, check=world["check"], asset=world["asset"]
    )
    up = card["upstream_pipeline_run"]
    assert up is not None
    assert up["pipeline_or_dag_id"] == "flow_a_snowflake_load"
    assert up["provider_run_id"] == run_id


def test_upstream_pipeline_layer_fails_closed_on_ambiguous_marker(
    db_session: Any, world: dict[str, Any]
) -> None:
    """dbt's `pipeline_or_dag_id` (job_name) is free-form webhook input with no
    colon restriction (backend/app/orchestration/dbt.py), so two DISTINCT
    PipelineRun rows can reconstruct to the identical marker string when a
    colon lands on a different side of the pipeline/run-id boundary:
    ("nightly:etl", "run-1") and ("nightly", "etl:run-1") both concat to
    "dbt:nightly:etl:run-1". Neither row is unambiguously "the" match — the
    layer must return None (evidence unavailable) rather than silently
    attributing the incident to whichever row Postgres happens to return
    first.
    """
    conn_id = world["conn"].id
    now = datetime.now(UTC)
    db_session.add_all(
        [
            PipelineRun(
                provider="dbt",
                connection_id=conn_id,
                provider_run_id="run-1",
                pipeline_or_dag_id="nightly:etl",
                env="dev",
                status="succeeded",
                started_at=now - timedelta(minutes=5),
                finished_at=now - timedelta(minutes=4),
                created_at=now - timedelta(minutes=5),
            ),
            PipelineRun(
                provider="dbt",
                connection_id=conn_id,
                provider_run_id="etl:run-1",
                pipeline_or_dag_id="nightly",
                env="dev",
                status="succeeded",
                started_at=now - timedelta(minutes=5),
                finished_at=now - timedelta(minutes=4),
                created_at=now - timedelta(minutes=5),
            ),
        ]
    )
    db_session.commit()
    run = _run(db_session, world["suite"], triggered_by="dbt:nightly:etl:run-1")
    result = Result(run_id=run.id, check_id=world["check"].id, status="fail")
    db_session.add(result)
    db_session.commit()
    card = build_evidence(
        db_session, run=run, result=result, check=world["check"], asset=world["asset"]
    )
    assert card["upstream_pipeline_run"] is None


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


# ── fix batch (PR #775 review): layer isolation + observed_value stripping ────


def test_raising_layer_degrades_to_none_other_layers_intact(
    db_session: Any, world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One broken layer (blast radius here) degrades to None with the rest of the
    card intact — the best-effort docstring made true.
    """
    from backend.app.services import incident_evidence

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("lineage walk exploded")

    monkeypatch.setattr(incident_evidence, "downstream_assets", boom)
    run = _run(db_session, world["suite"])
    result = Result(run_id=run.id, check_id=world["check"].id, status="fail", metric_value=0.5)
    db_session.add(result)
    db_session.commit()

    card = build_evidence(
        db_session, run=run, result=result, check=world["check"], asset=world["asset"]
    )
    assert card["downstream_blast_radius"] is None  # the broken layer, degraded
    assert card["check"]["name"] == "orders_not_null"  # neighbours intact
    assert card["failing_result"]["status"] == "fail"
    assert isinstance(card["metric_trend"], list)


def test_raising_layer_never_poisons_incident_sync(
    db_session: Any, world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken evidence layer must not take the run's whole incident sync down —
    the incident still opens (with the degraded card).
    """
    from sqlalchemy import select

    from backend.app.db.models import Incident
    from backend.app.services import incident_evidence, incident_service

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("lineage walk exploded")

    monkeypatch.setattr(incident_evidence, "downstream_assets", boom)
    run = _run(db_session, world["suite"])
    db_session.add(Result(run_id=run.id, check_id=world["check"].id, status="fail"))
    db_session.commit()
    incident_service.sync_incidents_for_run(db_session, run_id=run.id)

    incidents = db_session.scalars(
        select(Incident).where(Incident.suite_id == world["suite"].id)
    ).all()
    assert len(incidents) == 1
    assert incidents[0].evidence["downstream_blast_radius"] is None


def test_observed_value_sample_list_keys_stripped(db_session: Any, world: dict[str, Any]) -> None:
    """[PII] GX can mirror sample-bearing list keys into observed_value; the card
    must strip them while keeping the sanctioned scalar aggregates (#416).
    """
    secret = "leak-victim@example.com"
    run = _run(db_session, world["suite"])
    result = Result(
        run_id=run.id,
        check_id=world["check"].id,
        status="fail",
        observed_value={
            "observed_value": 12,
            "unexpected_count": 3,
            "unexpected_percent": 1.5,
            "partial_unexpected_list": [secret, "another@example.com"],
            "unexpected_index_list": [{"email": secret, "id": 7}],
        },
    )
    db_session.add(result)
    db_session.commit()

    card = build_evidence(
        db_session, run=run, result=result, check=world["check"], asset=world["asset"]
    )
    observed = card["failing_result"]["observed_value"]
    assert observed == {"observed_value": 12, "unexpected_count": 3, "unexpected_percent": 1.5}
    import json

    assert secret not in json.dumps(card)


# ── kind-aware detail (#1635) ──────────────────────────────────────────────────


def test_kind_detail_is_none_for_an_ordinary_expectation(
    db_session: Any, world: dict[str, Any]
) -> None:
    run = _run(db_session, world["suite"])
    result = Result(
        run_id=run.id,
        check_id=world["check"].id,
        status="fail",
        observed_value={"observed_value": 3},
    )
    db_session.add(result)
    db_session.commit()
    card = build_evidence(
        db_session, run=run, result=result, check=world["check"], asset=world["asset"]
    )
    assert card["kind_detail"] is None


def test_kind_detail_freshness(db_session: Any, world: dict[str, Any]) -> None:
    check = _monitor_check(db_session, world["suite"], "freshness")
    run = _run(db_session, world["suite"])
    result = Result(
        run_id=run.id,
        check_id=check.id,
        status="fail",
        observed_value={"max_timestamp": "2026-08-01T00:00:00+00:00", "age_hours": 400.5},
    )
    db_session.add(result)
    db_session.commit()
    card = build_evidence(db_session, run=run, result=result, check=check, asset=world["asset"])
    assert card["kind_detail"] == {
        "age_hours": 400.5,
        "max_timestamp": "2026-08-01T00:00:00+00:00",
    }


def test_kind_detail_volume(db_session: Any, world: dict[str, Any]) -> None:
    check = _monitor_check(db_session, world["suite"], "volume")
    run = _run(db_session, world["suite"])
    result = Result(
        run_id=run.id,
        check_id=check.id,
        status="fail",
        observed_value={"row_count": 40, "deviation_pct": -60.0},
    )
    db_session.add(result)
    db_session.commit()
    card = build_evidence(db_session, run=run, result=result, check=check, asset=world["asset"])
    assert card["kind_detail"] == {"row_count": 40, "deviation_pct": -60.0}


def test_kind_detail_schema_drift_with_changes(db_session: Any, world: dict[str, Any]) -> None:
    check = _monitor_check(db_session, world["suite"], "schema_drift")
    run = _run(db_session, world["suite"])
    result = Result(
        run_id=run.id,
        check_id=check.id,
        status="fail",
        observed_value={
            "added": ["new_col"],
            "removed": ["old_col"],
            "type_changed": [{"column": "amount", "from": "INTEGER", "to": "TEXT"}],
        },
    )
    db_session.add(result)
    db_session.commit()
    card = build_evidence(db_session, run=run, result=result, check=check, asset=world["asset"])
    assert card["kind_detail"] == {
        "added": ["new_col"],
        "removed": ["old_col"],
        "type_changed": [{"column": "amount", "from": "INTEGER", "to": "TEXT"}],
        "baseline_captured": False,
    }


def test_kind_detail_schema_drift_first_baseline_is_not_read_as_no_drift(
    db_session: Any, world: dict[str, Any]
) -> None:
    """A first-ever baseline capture has no `added`/`removed`/`type_changed` keys
    at all — `.get(..., [])` would read identically to "compared, found nothing",
    so `baseline_captured` must survive into the card (#828 class).
    """
    check = _monitor_check(db_session, world["suite"], "schema_drift")
    run = _run(db_session, world["suite"])
    result = Result(
        run_id=run.id,
        check_id=check.id,
        status="pass",
        observed_value={"baseline_captured": True, "columns_checked": 12},
    )
    db_session.add(result)
    db_session.commit()
    card = build_evidence(db_session, run=run, result=result, check=check, asset=world["asset"])
    assert card["kind_detail"] == {
        "added": [],
        "removed": [],
        "type_changed": [],
        "baseline_captured": True,
    }


def test_kind_detail_anomaly(db_session: Any, world: dict[str, Any]) -> None:
    check = _monitor_check(db_session, world["suite"], "anomaly")
    run = _run(db_session, world["suite"])
    result = Result(
        run_id=run.id,
        check_id=check.id,
        status="fail",
        observed_value={
            "target_metric": "row_count",
            "value": 900.0,
            "z_score": 4.2,
            "mean": 500.0,
            "stddev": 95.2,
            "deviation": 400.0,
            "degenerate_stddev": False,
        },
    )
    db_session.add(result)
    db_session.commit()
    card = build_evidence(db_session, run=run, result=result, check=check, asset=world["asset"])
    assert card["kind_detail"] == {
        "z_score": 4.2,
        "mean": 500.0,
        "stddev": 95.2,
        "insufficient_history": False,
    }


def test_kind_detail_anomaly_insufficient_history(db_session: Any, world: dict[str, Any]) -> None:
    check = _monitor_check(db_session, world["suite"], "anomaly")
    run = _run(db_session, world["suite"])
    result = Result(
        run_id=run.id,
        check_id=check.id,
        status="pass",
        observed_value={
            "target_metric": "row_count",
            "value": 900.0,
            "points": 1,
            "insufficient_history": True,
            "reason": "insufficient_history",
        },
    )
    db_session.add(result)
    db_session.commit()
    card = build_evidence(db_session, run=run, result=result, check=check, asset=world["asset"])
    assert card["kind_detail"] == {
        "z_score": None,
        "mean": None,
        "stddev": None,
        "insufficient_history": True,
    }


# ── cross-suite same-asset siblings (#1635) ────────────────────────────────────


def test_same_asset_siblings_sees_a_check_in_a_different_suite(
    db_session: Any, world: dict[str, Any]
) -> None:
    other_suite = _suite(db_session, world["owner"], world["conn"], table="ORDERS")
    other_check = _check(db_session, other_suite, name="orders_volume_ok")
    assert other_suite.asset_id == world["suite"].asset_id  # same table ⇒ same asset

    other_run = Run(
        suite_id=other_suite.id,
        status="succeeded",
        asset_id=other_suite.asset_id,
    )
    db_session.add(other_run)
    db_session.flush()
    db_session.add(Result(run_id=other_run.id, check_id=other_check.id, status="fail"))
    db_session.commit()

    run = _run(db_session, world["suite"])
    result = Result(run_id=run.id, check_id=world["check"].id, status="fail")
    db_session.add(result)
    db_session.commit()

    card = build_evidence(
        db_session, run=run, result=result, check=world["check"], asset=world["asset"]
    )
    siblings = card["same_asset_siblings"]
    assert len(siblings) == 1
    assert siblings[0]["check_name"] == "orders_volume_ok"
    assert siblings[0]["suite_id"] == str(other_suite.id)
    assert siblings[0]["status"] == "fail"


def test_same_asset_siblings_excludes_the_failing_check_itself(
    db_session: Any, world: dict[str, Any]
) -> None:
    run = _run(db_session, world["suite"])
    result = Result(run_id=run.id, check_id=world["check"].id, status="fail")
    db_session.add(result)
    db_session.commit()
    card = build_evidence(
        db_session, run=run, result=result, check=world["check"], asset=world["asset"]
    )
    assert card["same_asset_siblings"] == []


def test_same_asset_siblings_only_the_latest_result_per_check(
    db_session: Any, world: dict[str, Any]
) -> None:
    other_suite = _suite(db_session, world["owner"], world["conn"], table="ORDERS")
    other_check = _check(db_session, other_suite, name="orders_volume_ok")
    base = datetime.now(UTC) - timedelta(hours=1)
    for i, status in enumerate(("fail", "warn", "pass")):
        r = Run(suite_id=other_suite.id, status="succeeded", asset_id=other_suite.asset_id)
        db_session.add(r)
        db_session.flush()
        db_session.add(
            Result(
                run_id=r.id,
                check_id=other_check.id,
                status=status,
                created_at=base + timedelta(minutes=i),
            )
        )
        db_session.commit()

    run = _run(db_session, world["suite"])
    result = Result(run_id=run.id, check_id=world["check"].id, status="fail")
    db_session.add(result)
    db_session.commit()
    card = build_evidence(
        db_session, run=run, result=result, check=world["check"], asset=world["asset"]
    )
    siblings = card["same_asset_siblings"]
    assert len(siblings) == 1
    assert siblings[0]["status"] == "pass"  # the latest of the three, not the first


def test_same_asset_siblings_excludes_a_different_asset(
    db_session: Any, world: dict[str, Any]
) -> None:
    other_suite = _suite(db_session, world["owner"], world["conn"], table="PAYMENTS")
    other_check = _check(db_session, other_suite, name="payments_not_null")
    assert other_suite.asset_id != world["suite"].asset_id
    other_run = Run(suite_id=other_suite.id, status="succeeded", asset_id=other_suite.asset_id)
    db_session.add(other_run)
    db_session.flush()
    db_session.add(Result(run_id=other_run.id, check_id=other_check.id, status="fail"))
    db_session.commit()

    run = _run(db_session, world["suite"])
    result = Result(run_id=run.id, check_id=world["check"].id, status="fail")
    db_session.add(result)
    db_session.commit()
    card = build_evidence(
        db_session, run=run, result=result, check=world["check"], asset=world["asset"]
    )
    assert card["same_asset_siblings"] == []


def test_same_asset_siblings_excludes_an_operationally_failed_run(
    db_session: Any, world: dict[str, Any]
) -> None:
    """Only `succeeded` runs count (mirrors `rollup.AGGREGATABLE_RUN_STATUSES`) —
    a `failed` run's results are an operational failure, not a complete account.
    """
    other_suite = _suite(db_session, world["owner"], world["conn"], table="ORDERS")
    other_check = _check(db_session, other_suite, name="orders_volume_ok")
    other_run = Run(suite_id=other_suite.id, status="failed", asset_id=other_suite.asset_id)
    db_session.add(other_run)
    db_session.flush()
    db_session.add(Result(run_id=other_run.id, check_id=other_check.id, status="fail"))
    db_session.commit()

    run = _run(db_session, world["suite"])
    result = Result(run_id=run.id, check_id=world["check"].id, status="fail")
    db_session.add(result)
    db_session.commit()
    card = build_evidence(
        db_session, run=run, result=result, check=world["check"], asset=world["asset"]
    )
    assert card["same_asset_siblings"] == []


def test_same_asset_siblings_excludes_a_stale_result_outside_the_window(
    db_session: Any, world: dict[str, Any]
) -> None:
    other_suite = _suite(db_session, world["owner"], world["conn"], table="ORDERS")
    other_check = _check(db_session, other_suite, name="orders_volume_ok")
    stale_run = Run(
        suite_id=other_suite.id,
        status="succeeded",
        asset_id=other_suite.asset_id,
        created_at=datetime.now(UTC) - timedelta(days=30),
    )
    db_session.add(stale_run)
    db_session.flush()
    db_session.add(
        Result(
            run_id=stale_run.id,
            check_id=other_check.id,
            status="fail",
            created_at=datetime.now(UTC) - timedelta(days=30),
        )
    )
    db_session.commit()

    run = _run(db_session, world["suite"])
    result = Result(run_id=run.id, check_id=world["check"].id, status="fail")
    db_session.add(result)
    db_session.commit()
    card = build_evidence(
        db_session, run=run, result=result, check=world["check"], asset=world["asset"]
    )
    assert card["same_asset_siblings"] == []


def test_same_asset_siblings_empty_for_unresolved_asset(
    db_session: Any, world: dict[str, Any]
) -> None:
    run = _run(db_session, world["suite"])
    result = Result(run_id=run.id, check_id=world["check"].id, status="fail")
    db_session.add(result)
    db_session.commit()
    card = build_evidence(db_session, run=run, result=result, check=world["check"], asset=None)
    assert card["same_asset_siblings"] == []
