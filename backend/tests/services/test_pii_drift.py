"""pii_drift monitor kind (ADR 0049 phase 2) — profile at run time, baseline-and-skip, roll
forward, and every rung that takes a column out of "unclassified"."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.datasources.monitors import STATEFUL_MONITOR_KINDS
from backend.app.db.models import Asset, AssetColumnClassification, Check, Connection, Suite, User
from backend.app.services import column_detections, pii_drift, profile_service, stateful_monitors
from backend.app.services.monitor_baseline import get_baseline
from backend.app.services.pii_drift import build_pii_drift_executor
from backend.app.services.profile_service import ColumnProfile, ProfileResult
from backend.tests.support.fake_secret_store import FakeSecretStore

_EMAILS = [{"value": f"u{i}@example.com", "count": 1} for i in range(10)]
_PLAIN = [{"value": v, "count": 1} for v in ("new", "paid", "shipped", "void", "held")]


def _profile(**columns: list[dict[str, Any]]) -> list[ColumnProfile]:
    return [
        ColumnProfile(name, 0, 0.0, len(values), None, None, values)
        for name, values in columns.items()
    ]


@pytest.fixture
def graph(db_session: Session) -> dict[str, Any]:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(user)
    db_session.flush()
    conn = Connection(
        name=f"pg-{uuid.uuid4().hex[:8]}",
        type="postgres",
        env="dev",
        config={"host": "db", "database": "app", "schema": "public"},
        secret_ref="kv",
        created_by=user.id,
    )
    db_session.add(conn)
    db_session.flush()
    asset = Asset(namespace="postgres://db", name="public.orders")
    db_session.add(asset)
    db_session.flush()
    suite = Suite(
        name=f"s-{uuid.uuid4().hex[:8]}",
        connection_id=conn.id,
        created_by=user.id,
        target={"table": "orders", "schema": "public"},
        asset_id=asset.id,
    )
    db_session.add(suite)
    db_session.flush()
    check = Check(
        suite_id=suite.id,
        name="pii drift",
        kind="pii_drift",
        expectation_type="monitor:pii_drift",
        config={},
    )
    db_session.add(check)
    db_session.commit()
    return {"session": db_session, "conn": conn, "suite": suite, "asset": asset, "check": check}


class _Fake:
    """A swappable profile: tests change `columns` between runs."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, columns: list[ColumnProfile]) -> None:
        self.columns = columns
        self.seen: list[dict[str, Any]] = []
        monkeypatch.setattr(
            profile_service, "list_columns", lambda *a, **k: [c.column for c in self.columns]
        )

        def _inner(connection: Any, **kwargs: Any) -> ProfileResult:
            self.seen.append(kwargs)
            return ProfileResult(row_count=10, table=kwargs.get("table"), columns=self.columns)

        monkeypatch.setattr(profile_service, "_profile_connection", _inner)


def _executor(g: dict[str, Any], *, persist: bool = True) -> Any:
    return build_pii_drift_executor(
        g["session"],
        connection=g["conn"],
        target_table="orders",
        target_schema="public",
        target_catalog=None,
        secret_store=FakeSecretStore(default="s", raise_on_write=True),
        persist=persist,
    )


def _rows(session: Session, asset: Asset) -> list[AssetColumnClassification]:
    return list(
        session.scalars(
            select(AssetColumnClassification).where(AssetColumnClassification.asset_id == asset.id)
        )
    )


# ── the lifecycle ────────────────────────────────────────────────────────────────────────────


def test_first_run_baselines_what_already_looks_like_pii_and_skips(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _Fake(monkeypatch, _profile(contact=_EMAILS, status=_PLAIN))
    out = _executor(graph)(graph["check"])

    assert out.skipped is True and out.errored is False
    assert out.observed_value == {
        "columns_profiled": 2,
        "columns_detected": 1,
        "baseline_captured": True,
        "detected_columns": ["contact"],
    }
    row = get_baseline(graph["session"], graph["check"].id)
    assert row is not None and row.baseline == {"columns": ["contact"]}
    # The run fed the classification source as a FULL profile: a row, and the stamp moved.
    assert [r.column_name for r in _rows(graph["session"], graph["asset"])] == ["contact"]
    graph["session"].refresh(graph["asset"])
    assert graph["asset"].detected_classifications_refreshed_at is not None


def test_a_column_that_starts_looking_like_pii_is_reported_once_then_absorbed(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _Fake(monkeypatch, _profile(contact=_EMAILS, status=_PLAIN))
    run = _executor(graph)
    run(graph["check"])  # baseline: {contact}

    steady = run(graph["check"])
    assert steady.skipped is False and steady.success is True and steady.metric_value == 0.0

    fake.columns = _profile(contact=_EMAILS, status=_PLAIN, notes=_EMAILS)
    drifted = run(graph["check"])
    assert drifted.success is False and drifted.metric_value == 1.0
    assert drifted.observed_value is not None
    assert drifted.observed_value["new_columns"] == ["notes"]
    assert drifted.observed_value["known_columns"] == ["contact"]
    assert drifted.observed_value["columns_detected"] == 2
    assert "baseline_captured_at" in drifted.observed_value

    # Rolled forward: the next run does not report `notes` again.
    again = run(graph["check"])
    assert again.success is True and again.metric_value == 0.0
    row = get_baseline(graph["session"], graph["check"].id)
    assert row is not None and row.baseline == {"columns": ["contact", "notes"]}


# ── every rung that takes a column out of "unclassified" ────────────────────────────────────


def test_a_column_the_suite_policy_already_decided_is_not_drift(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    graph["suite"].column_policy = {"pii_columns": ["Notes"], "identifier_column": "contact"}
    graph["session"].commit()
    _Fake(monkeypatch, _profile(contact=_EMAILS, notes=_EMAILS, other=_EMAILS))
    out = _executor(graph)(graph["check"])
    assert out.observed_value is not None
    assert out.observed_value["detected_columns"] == ["other"]


def test_a_column_with_its_own_warehouse_tag_is_not_drift(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Either verdict: the steward looked at it, so it is classified, not drifting."""
    graph["asset"].column_tags = {"contact": "public", "notes": "sensitive"}
    graph["session"].commit()
    _Fake(monkeypatch, _profile(contact=_EMAILS, notes=_EMAILS, other=_EMAILS))
    out = _executor(graph)(graph["check"])
    assert out.observed_value is not None
    assert out.observed_value["detected_columns"] == ["other"]


def test_a_dismissed_detection_is_not_drift(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _Fake(monkeypatch, _profile(contact=_EMAILS))
    run = _executor(graph)
    run(graph["check"])  # baseline {contact}, detection persisted
    column_detections.set_dismissed(
        graph["session"], graph["asset"], "contact", dismissed=True, actor_id=None
    )
    fake.columns = _profile(contact=_EMAILS, notes=_EMAILS)
    out = run(graph["check"])
    assert out.observed_value is not None
    assert out.observed_value["new_columns"] == ["notes"]
    assert out.observed_value["columns_detected"] == 1, "the dismissed column left the set"


def test_ignore_columns_are_left_out(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    graph["check"].config = {"ignore_columns": ["CONTACT"]}
    graph["session"].commit()
    _Fake(monkeypatch, _profile(contact=_EMAILS, notes=_EMAILS))
    out = _executor(graph)(graph["check"])
    assert out.observed_value is not None
    assert out.observed_value["detected_columns"] == ["notes"]


# ── dry run, failures, dispatch ──────────────────────────────────────────────────────────────


def test_a_dry_run_persists_nothing_but_still_sees_this_profiles_detections(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _Fake(monkeypatch, _profile(contact=_EMAILS))
    out = _executor(graph, persist=False)(graph["check"])
    assert out.skipped is True
    assert out.observed_value is not None
    assert out.observed_value["dry_run"] is True
    assert out.observed_value["detected_columns"] == ["contact"]
    assert get_baseline(graph["session"], graph["check"].id) is None
    assert _rows(graph["session"], graph["asset"]) == []
    # …and the profile was NOT handed the asset, so nothing could have been stamped.
    assert fake.seen and all("asset" not in k or k["asset"] is None for k in fake.seen)


def test_the_persisted_run_hands_the_profiler_the_asset_as_a_full_profile(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    real = profile_service.profile_connection

    def spy(connection: Any, **kwargs: Any) -> ProfileResult:
        captured.update(kwargs)
        return real(connection, **kwargs)

    _Fake(monkeypatch, _profile(contact=_EMAILS))
    monkeypatch.setattr(profile_service, "profile_connection", spy)
    _executor(graph)(graph["check"])
    assert captured["asset"] is not None and captured["asset"].id == graph["asset"].id
    assert captured["full_profile"] is True
    assert captured["table"] == "orders" and captured["schema"] == "public"


def test_a_profile_failure_is_a_per_check_error_with_a_classified_message(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*a: Any, **k: Any) -> list[str]:
        raise RuntimeError("password authentication failed for user 'x' at 10.0.0.1")

    monkeypatch.setattr(profile_service, "list_columns", _boom)
    out = _executor(graph)(graph["check"])
    assert out.errored is True and out.error_message is not None
    assert out.error_message.startswith("could not profile the target: ")
    assert "10.0.0.1" not in out.error_message
    assert get_baseline(graph["session"], graph["check"].id) is None


def test_a_suite_with_no_asset_is_an_error_not_a_pass(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    graph["suite"].asset_id = None
    graph["session"].commit()
    _Fake(monkeypatch, _profile(contact=_EMAILS))
    out = _executor(graph)(graph["check"])
    assert out.errored is True and "asset" in (out.error_message or "")


def test_file_and_iceberg_targets_are_addressed_the_way_the_profiler_expects() -> None:
    s3 = Connection(name="s3", type="s3", env="dev", config={}, secret_ref="kv")
    ice = Connection(name="ice", type="iceberg", env="dev", config={}, secret_ref="kv")
    pg = Connection(name="pg", type="postgres", env="dev", config={}, secret_ref="kv")
    assert pii_drift._target_kwargs(s3, table="landing/orders.csv", schema=None, catalog=None) == {
        "path": "landing/orders.csv"
    }
    assert pii_drift._target_kwargs(ice, table="sales.orders", schema=None, catalog=None) == {
        "table": "sales.orders"
    }
    assert pii_drift._target_kwargs(pg, table="orders", schema="public", catalog=None) == {
        "table": "orders",
        "schema": "public",
        "catalog": None,
    }


def test_every_stateful_kind_has_an_executor_and_nothing_else_does() -> None:
    """The registry says which kinds are stateful; the dispatcher must wire exactly those."""
    assert set(stateful_monitors._BUILDERS) == set(STATEFUL_MONITOR_KINDS)


# ── author time and dry run ──────────────────────────────────────────────────────────────────


def test_authoring_is_allowed_on_every_profilable_type_and_refused_elsewhere() -> None:
    from decimal import Decimal

    from backend.app.services.check_service import CheckConfigInvalidError, validate_monitor_check

    for conn_type in ("postgres", "snowflake", "unity_catalog", "s3", "adls_gen2", "iceberg"):
        validate_monitor_check(
            "pii_drift",
            {},
            expectation_type="monitor:pii_drift",
            connection_type=conn_type,
            warn_threshold=Decimal(1),
            fail_threshold=Decimal(1),
            critical_threshold=None,
        )
    with pytest.raises(CheckConfigInvalidError):
        validate_monitor_check(
            "pii_drift",
            {},
            expectation_type="monitor:pii_drift",
            connection_type="airflow",
            warn_threshold=None,
            fail_threshold=None,
            critical_threshold=None,
        )
    with pytest.raises(CheckConfigInvalidError):
        validate_monitor_check(
            "pii_drift",
            {"ignore_columns": "contact"},
            expectation_type="monitor:pii_drift",
            connection_type="postgres",
            warn_threshold=None,
            fail_threshold=None,
            critical_threshold=None,
        )


def test_dry_run_reports_the_raw_detector_view_and_persists_nothing(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.services import dryrun_service

    fake = _Fake(monkeypatch, _profile(contact=_EMAILS, status=_PLAIN, notes=_EMAILS))
    out = dryrun_service.dry_run_check(
        graph["conn"],
        session=graph["session"],
        kind="pii_drift",
        expectation_type="monitor:pii_drift",
        config={"ignore_columns": ["notes"]},
        warn_threshold=None,
        fail_threshold=None,
        critical_threshold=None,
        target={"table": "orders", "schema": "public"},
        secret_store=FakeSecretStore(default="s", raise_on_write=True),
    )
    assert out.status == "skip" and out.metric_value is None
    assert out.observed_value is not None
    assert out.observed_value["detected_columns"] == [{"column": "contact", "detector": "email"}]
    assert out.observed_value["columns_profiled"] == 3
    assert "persisted run" in out.observed_value["preview"]
    assert _rows(graph["session"], graph["asset"]) == []
    assert all(k.get("asset") is None for k in fake.seen)
