"""MCP `profile_column` feeds the profiler classification source only on the suite's own
target (ADR 0049) — the default location is the suite's target; an explicit one is not."""

from __future__ import annotations

from typing import Any

from backend.app.db.models import Asset
from backend.app.mcp import server
from backend.app.services import profile_service
from backend.app.services.profile_service import ProfileResult
from backend.tests.mcp.test_mcp_tools import _as, _suite, _user


def _capture(monkeypatch: Any) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def _fake(connection: Any, **kwargs: Any) -> ProfileResult:
        seen.update(kwargs)
        return ProfileResult(row_count=0, table=kwargs.get("table"), columns=[])

    monkeypatch.setattr(profile_service, "profile_connection", _fake)
    return seen


def _attach_asset(db_session: Any, suite: Any) -> Asset:
    asset = Asset(namespace="snowflake://a", name="PUBLIC.ORDERS")
    db_session.add(asset)
    db_session.flush()
    suite.asset_id = asset.id
    db_session.commit()
    return asset


def test_the_default_location_feeds_the_suites_asset(db_session: Any, monkeypatch: Any) -> None:
    user = _user(db_session)
    suite = _suite(db_session, user)  # target={"table": "ORDERS"}
    asset = _attach_asset(db_session, suite)
    seen = _capture(monkeypatch)
    _as(monkeypatch, db_session, user)

    server.profile_column(str(suite.id), columns=["revenue"])

    assert seen["table"] == "ORDERS"
    assert seen["asset"] is not None and seen["asset"].id == asset.id


def test_an_explicit_location_feeds_nothing(db_session: Any, monkeypatch: Any) -> None:
    """Even one spelling the same table: an explicit override may name a different table whose
    columns collide by name, and MCP keeps the conservative rule the tag floor already uses."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    _attach_asset(db_session, suite)
    seen = _capture(monkeypatch)
    _as(monkeypatch, db_session, user)

    server.profile_column(str(suite.id), columns=["revenue"], table="ORDERS")

    assert seen["asset"] is None


# ── get_asset reports the detections, and when it last looked ───────────────────────────────


def test_get_asset_lists_detected_classifications(db_session: Any, monkeypatch: Any) -> None:
    from types import SimpleNamespace

    from backend.app.services.column_detections import record_profile_detections, set_dismissed

    user = _user(db_session)
    suite = _suite(db_session, user)
    asset = _attach_asset(db_session, suite)
    _as(monkeypatch, db_session, user)

    never = server.get_asset(str(asset.id))
    assert never["detected_classifications"] == []
    assert never["detected_classifications_refreshed_at"] is None

    emails = [{"value": f"u{i}@example.com", "count": 1} for i in range(10)]
    record_profile_detections(
        db_session,
        asset,
        [
            SimpleNamespace(column="Contact", top_values=emails),
            SimpleNamespace(column="notes", top_values=emails),
        ],
    )
    set_dismissed(db_session, asset, "notes", dismissed=True, actor_id=user.id)

    out = server.get_asset(str(asset.id))
    entries = {e["column"]: e for e in out["detected_classifications"]}
    assert entries["Contact"]["detector"] == "email" and entries["Contact"]["dismissed"] is False
    assert entries["notes"]["dismissed"] is True and entries["notes"]["dismissed_at"]
    assert isinstance(entries["Contact"]["first_detected_at"], str)
    assert out["detected_classifications_refreshed_at"] is not None
