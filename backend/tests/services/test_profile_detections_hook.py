"""`profile_connection` feeds the profiler classification source (ADR 0049) — and only for the
suite's own target."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.db.models import Asset, AssetColumnClassification, Connection, User
from backend.app.services import column_detections as cd
from backend.app.services import live_probe, profile_service
from backend.app.services.profile_service import ColumnProfile, ProfileResult
from backend.tests.support.fake_secret_store import FakeSecretStore

_EMAILS = [{"value": f"u{i}@example.com", "count": 1} for i in range(10)]


def _connection(db_session: Any, conn_type: str = "snowflake", **config: Any) -> Connection:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"o-{uuid.uuid4().hex[:6]}@acme.io")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type=conn_type,
        env="dev",
        config=config or {"account": "a", "schema": "PUBLIC"},
        secret_ref="kv",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    return conn


def _fake(monkeypatch: Any) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def _inner(connection: Any, **kwargs: Any) -> ProfileResult:
        seen.update(kwargs)
        return ProfileResult(
            row_count=10,
            table=kwargs.get("table"),
            columns=[ColumnProfile("contact", 0, 0.0, 10, None, None, _EMAILS)],
        )

    monkeypatch.setattr(profile_service, "_profile_connection", _inner)
    return seen


def _profile(db_session: Any, conn: Connection, asset: Asset | None) -> ProfileResult:
    return profile_service.profile_connection(
        conn,
        session=db_session,
        columns=["contact"],
        top_n=20,
        table="ORDERS",
        secret_store=FakeSecretStore(default="s"),
        asset=asset,
    )


def _rows(db_session: Any) -> list[AssetColumnClassification]:
    return list(db_session.query(AssetColumnClassification))


def test_a_profile_of_the_assets_own_target_persists_its_detections(
    db_session: Any, monkeypatch: Any
) -> None:
    _fake(monkeypatch)
    conn = _connection(db_session)
    asset = Asset(namespace="snowflake://a", name="PUBLIC.ORDERS")
    db_session.add(asset)
    db_session.flush()

    result = _profile(db_session, conn, asset)

    assert [c.column for c in result.columns] == ["contact"]
    (row,) = _rows(db_session)
    assert (row.asset_id, row.column_name, row.detector) == (asset.id, "contact", "email")
    # A one-column probe: it records what it saw and does NOT stamp the asset as looked-at.
    assert asset.detected_classifications_refreshed_at is None


def test_a_profile_with_no_asset_persists_nothing(db_session: Any, monkeypatch: Any) -> None:
    _fake(monkeypatch)
    _profile(db_session, _connection(db_session), None)
    assert _rows(db_session) == []


def test_a_failing_persist_never_fails_the_profile(db_session: Any, monkeypatch: Any) -> None:
    _fake(monkeypatch)
    asset = Asset(namespace="snowflake://a", name="PUBLIC.ORDERS")
    db_session.add(asset)
    db_session.flush()

    def _boom(*a: Any, **k: Any) -> int:
        raise RuntimeError("down")

    monkeypatch.setattr(cd, "record_profile_detections", _boom)
    result = _profile(db_session, _connection(db_session), asset)
    assert result.row_count == 10


def test_suggest_policy_for_target_threads_the_asset_through(
    db_session: Any, monkeypatch: Any
) -> None:
    """The worker's auto-classify runs on the suite's own target, so it feeds the source too."""
    _fake(monkeypatch)
    monkeypatch.setattr(profile_service, "list_columns", lambda *a, **k: ["contact"])
    asset = Asset(namespace="snowflake://a", name="PUBLIC.ORDERS")
    db_session.add(asset)
    db_session.flush()
    policy = profile_service.suggest_policy_for_target(
        _connection(db_session),
        session=db_session,
        table="ORDERS",
        secret_store=FakeSecretStore(default="s"),
        asset=asset,
    )
    assert policy["pii_columns"] == ["contact"]
    assert [r.column_name for r in _rows(db_session)] == ["contact"]
    # Every column was profiled, so this one stamps the asset.
    db_session.refresh(asset)
    assert asset.detected_classifications_refreshed_at is not None


def test_a_probe_of_another_table_keeps_a_detection_like_any_masking_verdict() -> None:
    """`applicable_tags` narrows the floor for a probe of some other table: clearances belong to
    the asset they were given on, masking verdicts travel — a detection is a masking verdict."""
    tags = {"email": "detected", "id": "public", "ssn": "sensitive"}
    assert live_probe.applicable_tags(tags, probed_other_target=True) == {
        "email": "detected",
        "ssn": "sensitive",
    }


# ── which explicit probe locations count as the suite's own target ──────────────────────────


def _suite(conn_type: str, target: dict[str, Any] | None) -> SimpleNamespace:
    return SimpleNamespace(target=target)


def _probe(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "table": None,
        "schema": None,
        "catalog": None,
        "path": None,
        "namespace": None,
    }
    base.update(kw)
    return base


@pytest.mark.parametrize(
    ("conn_type", "target", "probe", "own"),
    [
        (
            "snowflake",
            {"table": "ORDERS", "schema": "SALES"},
            _probe(table="orders", schema="sales"),
            True,
        ),
        ("snowflake", {"table": "ORDERS", "schema": "SALES"}, _probe(table="ORDERS"), False),
        (
            "snowflake",
            {"table": "ORDERS", "schema": "SALES"},
            _probe(table="ORDERS", schema="OTHER"),
            False,
        ),
        (
            "snowflake",
            {"table": "ORDERS", "schema": "SALES"},
            _probe(table="RETURNS", schema="SALES"),
            False,
        ),
        ("snowflake", None, _probe(table="ORDERS", schema="SALES"), False),
        (
            "unity_catalog",
            {"table": "t", "schema": "s", "catalog": "c"},
            _probe(table="T", schema="S", catalog="C"),
            True,
        ),
        (
            "unity_catalog",
            {"table": "t", "schema": "s", "catalog": "c"},
            _probe(table="T", schema="S"),
            False,
        ),
        ("s3", {"path": "landing/orders.csv"}, _probe(path="landing/orders.csv"), True),
        ("s3", {"path": "landing/orders.csv"}, _probe(path="landing/returns.csv"), False),
        (
            "s3",
            {"batch": {"prefix": "landing/", "pattern": "orders_*.csv", "pick": "latest"}},
            _probe(path="landing/orders_1.csv"),
            False,
        ),
    ],
)
def test_probe_is_own_target(conn_type: str, target: Any, probe: dict[str, Any], own: bool) -> None:
    """Strict, field for field: an omitted schema resolves to the connection default, which may
    be a different table of the same name; a batch target names no concrete file."""
    conn = SimpleNamespace(type=conn_type)
    assert live_probe.probe_is_own_target(_suite(conn_type, target), conn, **probe) is own
