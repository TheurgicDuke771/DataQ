# ruff: noqa: F811 — `captured`/`uc_connection` are fixtures imported from test_columns.
"""Classification propagation through column lineage (#1710) — additive-only.

Driven by the CAPTURED Unity Catalog payload through the real provider + real refresh (the
`captured` fixture): raw.feedback → silver.feedback → gold.feedback_sentiment, with `customer_id`
and `email` copied raw→silver, `customer_id` copied on to gold, and gold `sentiment` derived from
`comment`.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.db.models import Asset, Connection, LineageEdge, Suite, User
from backend.app.lineage import columns as lineage_columns
from backend.app.services import column_tags as ct
from backend.app.services import run_service
from backend.app.services.asset_service import upsert_assets
from backend.tests.lineage.test_columns import (  # noqa: F401
    _NS,
    _asset_id,
    _name,
    captured,
    uc_connection,
)


def _asset(db_session: Session, schema: str, table: str) -> Asset:
    asset = db_session.get(Asset, _asset_id(db_session, schema, table))
    assert asset is not None
    return asset


def _tag(db_session: Session, schema: str, table: str, tags: dict[str, str] | None) -> Asset:
    asset = _asset(db_session, schema, table)
    asset.column_tags = tags
    db_session.flush()
    return asset


def test_a_copied_column_inherits_sensitive_across_two_hops(
    db_session: Session, captured: Connection
) -> None:
    _tag(db_session, "raw", "feedback", {"customer_id": ct.SENSITIVE})
    gold = _asset(db_session, "gold", "feedback_sentiment")
    effective = ct.effective_column_tags(db_session, gold)
    assert effective is not None and effective["customer_id"] == ct.SENSITIVE
    inherited = ct.inherited_sensitive(db_session, gold)
    assert inherited["customer_id"] == [(_asset_id(db_session, "raw", "feedback"), "customer_id")]


def test_a_derived_column_inherits_too(db_session: Session, captured: Connection) -> None:
    """A value derived from a sensitive column can carry it (sentiment ← comment): mask."""
    _tag(db_session, "raw", "feedback", {"comment": ct.SENSITIVE})
    gold = _asset(db_session, "gold", "feedback_sentiment")
    assert (ct.effective_column_tags(db_session, gold) or {}).get("sentiment") == ct.SENSITIVE


def test_the_columns_own_verdict_wins_both_ways(db_session: Session, captured: Connection) -> None:
    _tag(db_session, "raw", "feedback", {"customer_id": ct.SENSITIVE, "email": ct.SENSITIVE})
    silver = _tag(db_session, "silver", "feedback", {"customer_id": ct.NON_SENSITIVE})
    effective = ct.effective_column_tags(db_session, silver) or {}
    # The steward looked at silver.customer_id (e.g. it is hashed there) — their answer stands.
    assert effective["customer_id"] == ct.NON_SENSITIVE
    # silver.email has no own verdict — inherited.
    assert effective["email"] == ct.SENSITIVE


def test_public_is_never_inherited(db_session: Session, captured: Connection) -> None:
    _tag(db_session, "raw", "feedback", {"customer_id": ct.NON_SENSITIVE})
    silver = _asset(db_session, "silver", "feedback")
    assert ct.effective_column_tags(db_session, silver) is None
    assert ct.inherited_sensitive(db_session, silver) == {}


def test_an_unread_upstream_contributes_nothing(db_session: Session, captured: Connection) -> None:
    silver = _tag(db_session, "silver", "feedback", {"rating": ct.SENSITIVE})
    assert ct.effective_column_tags(db_session, silver) == {"rating": ct.SENSITIVE}


def test_a_gap_edge_contributes_nothing(db_session: Session, captured: Connection) -> None:
    """A dbt edge carries no pairs, so a tagged table behind it cannot be attributed a column."""
    ids = upsert_assets(db_session, [{"namespace": _NS, "name": _name("landing", "x")}])
    landing = ids[(_NS, _name("landing", "x"))]
    db_session.get(Asset, landing).column_tags = {"customer_id": ct.SENSITIVE}  # type: ignore[union-attr]
    db_session.add(
        LineageEdge(
            upstream_asset_id=landing,
            downstream_asset_id=_asset_id(db_session, "raw", "feedback"),
            source="dbt",
            connection_id=captured.id,
        )
    )
    db_session.flush()
    raw = _asset(db_session, "raw", "feedback")
    assert ct.effective_column_tags(db_session, raw) is None


def test_the_setting_opts_out(
    db_session: Session, captured: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tag(db_session, "raw", "feedback", {"email": ct.SENSITIVE})
    silver = _asset(db_session, "silver", "feedback")
    monkeypatch.setenv("LINEAGE_CLASSIFICATION_PROPAGATION", "false")
    get_settings.cache_clear()
    try:
        assert ct.effective_column_tags(db_session, silver) is None
    finally:
        monkeypatch.delenv("LINEAGE_CLASSIFICATION_PROPAGATION")
        get_settings.cache_clear()
    assert (ct.effective_column_tags(db_session, silver) or {}).get("email") == ct.SENSITIVE


def test_a_lineage_error_falls_back_to_own_tags_and_keeps_the_session(
    db_session: Session, captured: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tag(db_session, "raw", "feedback", {"email": ct.SENSITIVE})
    silver = _tag(db_session, "silver", "feedback", {"rating": ct.SENSITIVE})

    def boom(session: Session, asset_id: Any, **_: Any) -> Any:
        session.execute(text("SELECT 1 / 0"))  # a real Postgres error

    monkeypatch.setattr(lineage_columns, "upstream_column_sources", boom)
    assert ct.effective_column_tags(db_session, silver) == {"rating": ct.SENSITIVE}
    assert db_session.execute(text("SELECT 1")).scalar() == 1


def test_the_redaction_read_path_masks_an_inherited_column(
    db_session: Session, captured: Connection
) -> None:
    """End to end through the canonical read path every REST/MCP surface uses."""
    user = User(aad_object_id=uuid.uuid4().hex, email=f"p-{uuid.uuid4().hex[:6]}@x.io")
    db_session.add(user)
    db_session.flush()
    suite = Suite(name="s", connection_id=captured.id, created_by=user.id)
    suite.asset_id = _asset_id(db_session, "silver", "feedback")
    db_session.add(suite)
    db_session.flush()
    # `channel` is copied raw→silver; neither its name nor its value looks like PII.
    observed = {"observed_value": "web"}

    def read() -> Any:
        return run_service.redact_observed_value(
            observed,
            tested_column="channel",
            expectation_type="expect_column_values_to_be_in_set",
            tags=run_service.asset_column_tags(db_session, suite),
        )

    assert read() == observed  # control: shown without an upstream classification
    _tag(db_session, "raw", "feedback", {"channel": ct.SENSITIVE})
    assert read() != observed
    assert "web" not in str(read())


def test_upstream_sources_cover_every_column_in_one_walk(
    db_session: Session, captured: Connection
) -> None:
    gold = _asset_id(db_session, "gold", "feedback_sentiment")
    raw = _asset_id(db_session, "raw", "feedback")
    walk = lineage_columns.upstream_column_sources(db_session, gold)
    assert (raw, "customer_id") in walk.sources["customer_id"]
    assert (raw, "comment") in walk.sources["sentiment"]
    assert not walk.truncated
    capped = lineage_columns.upstream_column_sources(db_session, gold, max_depth=1)
    assert capped.truncated and (raw, "customer_id") not in capped.sources["customer_id"]


# ── every redaction read path uses the effective map, not the raw column ────────────────


@pytest.fixture
def silver_run(db_session: Session, captured: Connection) -> dict[str, Any]:
    from datetime import UTC, datetime

    from backend.app.db.models import Check, Incident, Result, Run

    user = User(aad_object_id=uuid.uuid4().hex, email=f"w-{uuid.uuid4().hex[:6]}@x.io")
    db_session.add(user)
    db_session.flush()
    silver = _asset_id(db_session, "silver", "feedback")
    suite = Suite(name="w", connection_id=captured.id, created_by=user.id)
    suite.asset_id = silver
    db_session.add(suite)
    db_session.flush()
    check = Check(
        suite_id=suite.id,
        name="channel set",
        kind="expectation",
        expectation_type="expect_column_values_to_be_in_set",
        config={"column": "channel", "value_set": ["app"]},
    )
    db_session.add(check)
    db_session.flush()
    run = Run(suite_id=suite.id, status="succeeded", triggered_by="t", asset_id=silver)
    db_session.add(run)
    db_session.flush()
    result = Result(
        run_id=run.id, check_id=check.id, status="fail", observed_value={"observed_value": "web"}
    )
    db_session.add(result)
    incident = Incident(
        asset_id=silver,
        check_id=check.id,
        suite_id=suite.id,
        status="open",
        last_seen_at=datetime.now(UTC),
        evidence={"failing_result": {"observed_value": {"observed_value": "web"}}},
    )
    db_session.add(incident)
    db_session.flush()
    _tag(db_session, "raw", "feedback", {"channel": ct.SENSITIVE})
    return {
        "run": run,
        "result": result,
        "check": check,
        "incident": incident,
        "asset": db_session.get(Asset, silver),
    }


def test_alert_report_masks_an_inherited_column(
    db_session: Session, silver_run: dict[str, Any]
) -> None:
    from backend.app.alerting.builder import build_run_report

    report = build_run_report(db_session, silver_run["run"])
    (check_report,) = report.checks
    assert "web" not in str(check_report.observed_value)


def test_incident_evidence_context_carries_inherited_tags(
    db_session: Session, silver_run: dict[str, Any]
) -> None:
    from backend.app.services.incident_evidence import resolve_redaction_contexts

    contexts = resolve_redaction_contexts(
        db_session,
        run=silver_run["run"],
        results=[silver_run["result"]],
        checks={silver_run["check"].id: silver_run["check"]},
        asset=silver_run["asset"],
    )
    (context,) = contexts.values()
    assert (context.tags or {}).get("channel") == ct.SENSITIVE


def test_stale_incident_backfill_masks_an_inherited_column(
    db_session: Session, silver_run: dict[str, Any]
) -> None:
    from backend.app.services.incident_service import redact_stale_evidence

    redact_stale_evidence(db_session)
    db_session.refresh(silver_run["incident"])
    assert "web" not in str(silver_run["incident"].evidence)
