"""Automatic baselines inherit their thresholds and shape at run time (ADR 0047, #1661)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from backend.app.datasources.base import CheckOutcome
from backend.app.datasources.monitors import anomaly_params
from backend.app.db.models import Asset, Check, Connection, Suite, User
from backend.app.services import anomaly, check_service, run_service, suggestion_service
from backend.app.services import coverage_service as cov
from backend.app.services import coverage_settings_service as svc
from backend.app.services.asset_identity import resolve_asset_identity
from backend.app.services.monitor_baseline import get_baseline
from backend.tests.support.fake_secret_store import FakeSecretStore

NOW = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)
_CONN = {"host": "wh.example.com", "database": "shop", "user": "reader", "auto_coverage": True}
_LEGACY_SHAPE = {"window": 8, "min_points": 4, "seasonality": True}
_LOOSE = {"warn": 5, "fail": 7, "critical": 9}
KEY = svc.INHERIT_KEY


def _flag(value: bool) -> dict[str, Any]:
    return {KEY: value}


def _check(
    *,
    origin: str = "auto",
    metric: str = "row_count",
    thresholds: tuple[int, int, int] = (3, 4, 6),
    kind: str = "anomaly",
    **config: Any,
) -> Check:
    return Check(
        id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        name="c",
        kind=kind,
        origin=origin,
        expectation_type=f"monitor:{kind}",
        config={"target_metric": metric, **_LEGACY_SHAPE, **config},
        warn_threshold=Decimal(thresholds[0]),
        fail_threshold=Decimal(thresholds[1]),
        critical_threshold=Decimal(thresholds[2]),
    )


def _coverage(workspace: dict[str, Any] | None = None) -> svc.SuiteCoverage:
    return svc.SuiteCoverage(svc.resolve(workspace))


_WORKSPACE = {
    "sensitivity": {"volume": _LOOSE},
    "baseline": {"window": 14, "min_points": 7, "seasonality": False},
}


# ── which checks inherit ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("check", "expected"),
    [
        (_check(**_flag(True)), True),
        (_check(**_flag(False)), False),
        # No flag: created before it existed. Inherits while it still holds what was written.
        (_check(), True),
        (_check(thresholds=(3, 5, 6)), False),
        (_check(window=10), False),
        # 1 == True in Python; a tuned `seasonality: 1` must not pass for the legacy `true`.
        (_check(seasonality=1), False),
        # A flagged check inherits whatever its stored snapshot says.
        (_check(thresholds=(5, 7, 9), window=14, **_flag(True)), True),
        # Only an automatic baseline can.
        (_check(origin="user", **_flag(True)), False),
        (_check(origin="suggestion"), False),
        (_check(kind="schema_drift"), False),
        (_check(metric="something_else", **_flag(True)), False),
    ],
)
def test_inherits(check: Check, expected: bool) -> None:
    assert svc.inherits(check) is expected


def test_each_baseline_takes_its_own_sensitivity_and_the_shared_shape() -> None:
    coverage = _coverage(_WORKSPACE)

    volume = coverage.for_check(_check())
    profile = coverage.for_check(_check(metric="column_profile"))

    assert volume is not None and profile is not None
    assert (volume.sensitivity.as_dict(), volume.sensitivity_from) == (
        {"warn": 5.0, "fail": 7.0, "critical": 9.0},
        "workspace",
    )
    assert (profile.sensitivity, profile.sensitivity_from) == (svc.DEFAULT_SENSITIVITY, "default")
    assert volume.shape == profile.shape == svc.Shape(14, 7, False)
    assert coverage.for_check(_check(**_flag(False))) is None


# ── an edit settles the flag ─────────────────────────────────────────────────


def _settle(
    check: Check,
    *,
    config: dict[str, Any] | None = None,
    workspace: dict[str, Any] | None = _WORKSPACE,
    **thresholds: Decimal | None,
) -> tuple[dict[str, Any], dict[str, Decimal | None]]:
    return svc.settle_edit(
        check, _coverage(workspace), sent_config=config, sent_thresholds=thresholds
    )


_STORED: dict[str, Any] = {"warn": Decimal(3), "fail": Decimal(4), "critical": Decimal(6)}


def test_an_edit_that_names_no_threshold_or_shape_keeps_it_inheriting() -> None:
    config, thresholds = _settle(_check())

    # The stored snapshot is untouched; a check with no flag gains it.
    assert config == {"target_metric": "row_count", **_LEGACY_SHAPE, KEY: True}
    assert thresholds == _STORED


def test_echoing_the_effective_values_back_is_not_an_edit() -> None:
    shown = {"target_metric": "row_count", "window": 14, "min_points": 7, "seasonality": False}

    config, thresholds = _settle(
        _check(**_flag(True)), config=shown, warn=Decimal(5), fail=Decimal(7), critical=Decimal(9)
    )

    assert config[KEY] is True
    assert thresholds == _STORED
    assert {k: config[k] for k in _LEGACY_SHAPE} == _LEGACY_SHAPE


def test_changing_one_threshold_pins_all_of_them_at_what_was_in_effect() -> None:
    config, thresholds = _settle(_check(**_flag(True)), warn=Decimal(6))

    assert config[KEY] is False
    # fail and critical come from the workspace setting in effect, not the stored 4 and 6.
    assert thresholds == {"warn": Decimal(6), "fail": Decimal("7.0"), "critical": Decimal("9.0")}
    assert {k: config[k] for k in _LEGACY_SHAPE} == _WORKSPACE["baseline"]


def test_a_value_equal_to_the_stored_snapshot_but_not_in_effect_is_an_edit() -> None:
    """The read surfaces show 7 as the fail threshold; someone sending 4 means 4."""
    config, thresholds = _settle(_check(**_flag(True)), fail=Decimal(4))

    assert config[KEY] is False
    assert thresholds["fail"] == Decimal(4)


def test_changing_the_window_pins_the_thresholds_too() -> None:
    sent = {"target_metric": "row_count", "window": 20, "min_points": 7, "seasonality": False}

    config, thresholds = _settle(_check(**_flag(True)), config=sent)

    assert (config["window"], config["min_points"], config[KEY]) == (20, 7, False)
    assert thresholds == {k: Decimal(str(float(v))) for k, v in _LOOSE.items()}


def test_clearing_a_threshold_is_an_edit() -> None:
    config, thresholds = _settle(_check(**_flag(True)), warn=None)

    assert (config[KEY], thresholds["warn"]) == (False, None)


def test_a_replacement_config_that_omits_the_flag_and_shape_keeps_both() -> None:
    """`config` replaces wholesale: a client that sends only what it knows must not strip
    the server-owned flag or leave the check with no stored shape."""
    config, _ = _settle(
        _check(metric="freshness_age_hours", column="loaded_at", **_flag(True)),
        config={"target_metric": "freshness_age_hours", "column": "updated_at"},
    )

    assert config == {
        "target_metric": "freshness_age_hours",
        "column": "updated_at",
        **_LEGACY_SHAPE,
        KEY: True,
    }


def test_the_numbers_beside_an_explicit_inherit_are_a_snapshot_not_an_edit() -> None:
    """A document that says "inherit" carries numbers from whenever it was written."""
    check = _check(**_flag(True))

    config, thresholds = _settle(
        check, config={**check.config, "window": 30}, warn=Decimal(8), fail=Decimal(8)
    )

    assert (config[KEY], config["window"]) == (True, 8)
    assert thresholds == _STORED


def test_an_explicit_false_pins_even_with_nothing_changed() -> None:
    check = _check(**_flag(True))

    config, thresholds = _settle(check, config={**check.config, KEY: False})

    assert config[KEY] is False
    assert thresholds["critical"] == Decimal("9.0")
    # The stored shape was sent back unchanged from the snapshot, so it is what was asked for.
    assert config["window"] == 8


def test_a_pinned_check_resumes_only_on_an_explicit_true() -> None:
    pinned = _check(thresholds=(2, 3, 4), **_flag(False))

    stays, _ = _settle(pinned, warn=Decimal(1))
    resumes, thresholds = _settle(pinned, config={**pinned.config, KEY: True})

    assert (stays[KEY], resumes[KEY]) == (False, True)
    assert thresholds["warn"] == Decimal(2)


def test_a_tuned_check_from_before_the_flag_is_marked_as_not_inheriting() -> None:
    config, _ = _settle(_check(thresholds=(3, 5, 6)), warn=Decimal(2))

    assert config[KEY] is False


def test_a_check_that_cannot_inherit_never_gains_the_flag_and_refuses_it() -> None:
    user = _check(origin="user")

    config, thresholds = _settle(user, warn=Decimal(1))

    assert KEY not in config and thresholds["warn"] == Decimal(1)
    with pytest.raises(svc.InheritEditError, match="only to an automatic baseline"):
        _settle(user, config={**user.config, KEY: True})
    # No settings read is made for a check that cannot inherit.
    assert svc.settle_edit(user, None, sent_config=None, sent_thresholds={})[0] == dict(user.config)


@pytest.mark.parametrize("flag", ["true", 1, None])
def test_a_flag_that_is_not_a_boolean_is_refused(flag: Any) -> None:
    check = _check(**_flag(True))

    if flag is None:
        # JSON null reads as "not sent": the stored flag carries over.
        settled, _ = _settle(check, config={**check.config, KEY: None}, workspace=None)
        assert settled[KEY] is True
        return
    with pytest.raises(svc.InheritEditError, match="must be true or false"):
        _settle(check, config={**check.config, KEY: flag})


def test_only_the_coverage_loop_may_create_a_check_with_the_flag() -> None:
    baseline = {"target_metric": "row_count", KEY: True}

    svc.validate_new_check(baseline, kind="anomaly", origin="auto")
    svc.validate_new_check({"target_metric": "row_count"}, kind="anomaly", origin="user")
    for kind, origin, config in (
        ("anomaly", "user", baseline),
        ("schema_drift", "auto", {KEY: True}),
        ("anomaly", "auto", {"target_metric": "row_count", KEY: "yes"}),
    ):
        with pytest.raises(svc.InheritEditError):
            svc.validate_new_check(config, kind=kind, origin=origin)


def test_strip_flag_leaves_everything_else() -> None:
    assert svc.strip_flag({"target_metric": "row_count", KEY: True}) == {
        "target_metric": "row_count"
    }


# ── retention ────────────────────────────────────────────────────────────────


def test_an_inheriting_check_keeps_the_seasonal_horizon_with_seasonality_off() -> None:
    from dataclasses import replace

    plain = anomaly_params(
        {"target_metric": "row_count", "window": 8, "min_points": 4, "seasonality": False}
    )

    assert plain.retained_observations == 8
    assert replace(plain, retain_seasonal_horizon=True).retained_observations == 56


# ── against the database: reconciler, run path, edits ────────────────────────


@pytest.fixture(autouse=True)
def no_profiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(suggestion_service, "refresh_from_profile", lambda *a, **k: 0)


@pytest.fixture
def admin(db_session: Any) -> User:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@x.io", role="admin")
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def conn(db_session: Any, admin: User) -> Connection:
    connection = Connection(
        name=f"pg-{uuid.uuid4().hex[:6]}",
        type="postgres",
        env="dev",
        config=dict(_CONN),
        secret_ref="kv-pg",
        created_by=admin.id,
    )
    db_session.add(connection)
    db_session.commit()
    return connection


def _covered(db_session: Any, conn: Connection, table: str = "orders") -> tuple[Asset, Suite]:
    ident = resolve_asset_identity("postgres", _CONN, {"schema": "public", "table": table})
    asset = Asset(
        namespace=ident.namespace,
        name=ident.name,
        env="dev",
        connection_id=conn.id,
        first_seen=NOW,
        last_seen=NOW,
    )
    db_session.add(asset)
    db_session.commit()
    cov.reconcile_connection(db_session, conn, now=NOW)
    suite = db_session.scalar(select(Suite).where(Suite.asset_id == asset.id))
    assert suite is not None
    return asset, suite


def _thresholds(check: Check) -> tuple[float | None, ...]:
    return tuple(
        None if v is None else float(v)
        for v in (check.warn_threshold, check.fail_threshold, check.critical_threshold)
    )


def _named(db_session: Any, suite: Suite, name: str) -> Check:
    check: Check | None = db_session.scalar(
        select(Check).where(Check.suite_id == suite.id, Check.name == name)
    )
    assert check is not None
    return check


def test_the_reconciler_creates_inheriting_checks_holding_the_values_in_effect(
    db_session: Any, conn: Connection, admin: User
) -> None:
    svc.set_workspace(
        db_session,
        {"sensitivity": {"profile": _LOOSE}, "baseline": _WORKSPACE["baseline"]},
        actor=admin,
    )
    db_session.commit()

    _, suite = _covered(db_session, conn)

    volume = _named(db_session, suite, cov.VOLUME_CHECK)
    profile = _named(db_session, suite, cov.PROFILE_CHECK)
    assert volume.config == {
        "target_metric": "row_count",
        "window": 14,
        "min_points": 7,
        "seasonality": False,
        "yearly": False,
        KEY: True,
    }
    assert "yearly" not in profile.config  # the column profile takes none
    assert _thresholds(volume) == (3.0, 4.0, 6.0)
    assert _thresholds(profile) == (5.0, 7.0, 9.0)
    assert _named(db_session, suite, cov.SCHEMA_CHECK).config == {}


def test_a_setting_changed_after_creation_reaches_the_check_without_touching_it(
    db_session: Any, conn: Connection, admin: User
) -> None:
    asset, suite = _covered(db_session, conn)
    volume = _named(db_session, suite, cov.VOLUME_CHECK)
    stored = (dict(volume.config), volume.warn_threshold, volume.updated_at)

    svc.set_workspace(db_session, {"sensitivity": {"volume": _LOOSE}}, actor=admin)
    asset.auto_coverage_config = {"baseline": {"window": 3, "min_points": 3, "seasonality": False}}
    db_session.commit()
    settings = svc.for_suite(db_session, suite).for_check(volume)

    assert settings is not None
    assert (settings.sensitivity.fail, settings.sensitivity_from) == (7.0, "workspace")
    assert (settings.shape.window, settings.shape_from) == (3, "asset")
    assert (dict(volume.config), volume.warn_threshold, volume.updated_at) == stored


def test_a_suite_with_no_asset_resolves_to_the_workspace_setting(
    db_session: Any, conn: Connection, admin: User
) -> None:
    svc.set_workspace(db_session, {"sensitivity": {"volume": _LOOSE}}, actor=admin)
    suite = Suite(name="manual", connection_id=conn.id, created_by=admin.id)
    db_session.add(suite)
    db_session.commit()

    effective = svc.for_suite(db_session, suite).effective

    assert effective.sensitivity_from["volume"] == "workspace"


@pytest.mark.parametrize(
    ("workspace", "expected"),
    [(None, "fail"), ({"sensitivity": {"volume": _LOOSE}}, "pass")],
)
def test_a_result_is_banded_against_the_inherited_thresholds(
    workspace: dict[str, Any] | None, expected: str
) -> None:
    outcome = CheckOutcome(expectation_type="monitor:anomaly", success=True, metric_value=4.5)

    result = run_service._build_result(
        uuid.uuid4(),
        _check(**_flag(True)),
        outcome,
        zero_sample=False,
        coverage=_coverage(workspace),
    )

    assert result.status == expected


def test_a_pinned_check_is_banded_against_its_own_thresholds() -> None:
    outcome = CheckOutcome(expectation_type="monitor:anomaly", success=True, metric_value=4.5)

    result = run_service._build_result(
        uuid.uuid4(),
        _check(**_flag(False)),
        outcome,
        zero_sample=False,
        coverage=_coverage({"sensitivity": {"volume": _LOOSE}}),
    )

    assert result.status == "fail"


def _executor(
    db_session: Any, conn: Connection, suite: Suite, value: float, monkeypatch: Any
) -> Any:
    monkeypatch.setattr(anomaly, "measure_metric", lambda *a, **k: float(value))
    return anomaly.build_anomaly_executor(
        db_session,
        connection=conn,
        target_table="orders",
        target_schema="public",
        target_catalog=None,
        secret_store=FakeSecretStore(default="secret", raise_on_write=True),
        coverage=svc.for_suite(db_session, suite),
    )


def _stored_values(db_session: Any, check: Check) -> list[float]:
    db_session.flush()
    row = get_baseline(db_session, check.id)
    assert row is not None
    return [o["value"] for o in row.baseline["observations"]]


def test_a_run_scores_on_the_inherited_shape_and_records_what_it_used(
    db_session: Any, conn: Connection, admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    asset, suite = _covered(db_session, conn)
    volume = _named(db_session, suite, cov.VOLUME_CHECK)
    # Stored: window 8, minimum 4, weekday-seasonal. In effect: three points, any weekday.
    asset.auto_coverage_config = {"baseline": {"window": 3, "min_points": 3, "seasonality": False}}
    svc.set_workspace(db_session, {"sensitivity": {"volume": _LOOSE}}, actor=admin)
    db_session.commit()

    for value in (100, 101, 99):
        skipped = _executor(db_session, conn, suite, value, monkeypatch)(volume)
        db_session.flush()
    scored = _executor(db_session, conn, suite, 100, monkeypatch)(volume)

    assert skipped.skipped is True
    # Under the stored shape (4 same-weekday points) this run would still be a cold start.
    assert scored.skipped is False
    assert scored.expected_value == {
        "monitor": "anomaly",
        "target_metric": "row_count",
        "window": 3,
        "min_points": 3,
        "seasonality": False,
        "thresholds": {"warn": 5.0, "fail": 7.0, "critical": 9.0},
        "inherited_from": {"sensitivity": "workspace", "baseline": "asset"},
    }


def test_switching_seasonality_off_does_not_trim_an_inheriting_checks_history(
    db_session: Any, conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    asset, suite = _covered(db_session, conn)
    volume = _named(db_session, suite, cov.VOLUME_CHECK)
    asset.auto_coverage_config = {"baseline": {"window": 3, "min_points": 3, "seasonality": False}}
    db_session.commit()

    for value in range(10):
        _executor(db_session, conn, suite, value, monkeypatch)(volume)
        db_session.flush()

    # window 3 without the horizon would keep 3; the seasonal horizon keeps up to 21.
    assert len(_stored_values(db_session, volume)) == 10


def test_a_pinned_check_runs_on_its_own_shape_and_records_no_provenance(
    db_session: Any, conn: Connection, admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    asset, suite = _covered(db_session, conn)
    volume = _named(db_session, suite, cov.VOLUME_CHECK)
    check_service.update_check(
        db_session,
        suite.id,
        volume.id,
        config={"target_metric": "row_count", "window": 3, "min_points": 3, "seasonality": False},
        actor_id=admin.id,
    )
    asset.auto_coverage_config = {"baseline": {"window": 30, "min_points": 20, "seasonality": True}}
    db_session.commit()

    for value in range(6):
        outcome = _executor(db_session, conn, suite, value, monkeypatch)(volume)
        db_session.flush()

    assert outcome.expected_value is not None
    assert outcome.expected_value["window"] == 3
    assert "inherited_from" not in outcome.expected_value
    assert len(_stored_values(db_session, volume)) == 3


def test_editing_a_threshold_through_the_service_pins_the_check(
    db_session: Any, conn: Connection, admin: User
) -> None:
    _, suite = _covered(db_session, conn)
    volume = _named(db_session, suite, cov.VOLUME_CHECK)
    svc.set_workspace(db_session, {"sensitivity": {"volume": _LOOSE}}, actor=admin)
    db_session.commit()

    renamed = check_service.update_check(
        db_session, suite.id, volume.id, name="Orders volume", actor_id=admin.id
    )
    still_inheriting = svc.inherits(renamed)
    edited = check_service.update_check(
        db_session, suite.id, volume.id, warn_threshold=Decimal(6), actor_id=admin.id
    )

    assert still_inheriting is True
    assert edited.config[KEY] is False
    assert _thresholds(edited) == (6.0, 7.0, 9.0)
    assert svc.for_suite(db_session, suite).for_check(edited) is None


def test_a_flag_that_is_not_a_boolean_is_a_422_and_changes_nothing(
    db_session: Any, conn: Connection, admin: User
) -> None:
    _, suite = _covered(db_session, conn)
    volume = _named(db_session, suite, cov.VOLUME_CHECK)
    before = (dict(volume.config), volume.warn_threshold)

    with pytest.raises(check_service.CheckConfigInvalidError, match="must be true or false"):
        check_service.update_check(
            db_session,
            suite.id,
            volume.id,
            config={**volume.config, KEY: "yes"},
            warn_threshold=Decimal(8),
            actor_id=admin.id,
        )

    db_session.refresh(volume)
    assert (dict(volume.config), volume.warn_threshold) == before


def test_the_reconciler_reads_the_settings_only_when_it_adds_a_baseline(
    db_session: Any, conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _covered(db_session, conn)
    reads: list[Any] = []
    real = svc.for_suite

    def counting(*args: Any, **kwargs: Any) -> svc.SuiteCoverage:
        reads.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(svc, "for_suite", counting)

    cov.reconcile_connection(db_session, conn, now=NOW)

    assert reads == []


def test_setting_thresholds_in_bulk_pins_each_inheriting_check(
    db_session: Any, conn: Connection, admin: User
) -> None:
    _, suite = _covered(db_session, conn)
    ids = [_named(db_session, suite, name).id for name in (cov.VOLUME_CHECK, cov.PROFILE_CHECK)]

    changed = check_service.bulk_set_thresholds(
        db_session, suite.id, ids, critical_threshold=Decimal(10), actor_id=admin.id
    )

    assert [c.config[KEY] for c in changed] == [False, False]
    assert [_thresholds(c) for c in changed] == [(3.0, 4.0, 10.0), (3.0, 4.0, 10.0)]


def test_restoring_a_version_restores_its_flag(
    db_session: Any, conn: Connection, admin: User
) -> None:
    _, suite = _covered(db_session, conn)
    volume = _named(db_session, suite, cov.VOLUME_CHECK)
    check_service.update_check(
        db_session, suite.id, volume.id, warn_threshold=Decimal(2), actor_id=admin.id
    )

    restored = check_service.restore_check_version(
        db_session, suite.id, volume.id, 1, actor_id=admin.id
    )

    assert restored.config[KEY] is True
    assert svc.inherits(restored) is True


def test_a_person_cannot_author_a_check_that_claims_to_inherit(
    db_session: Any, conn: Connection, admin: User
) -> None:
    suite = Suite(
        name="manual",
        connection_id=conn.id,
        created_by=admin.id,
        target={"schema": "public", "table": "orders"},
    )
    db_session.add(suite)
    db_session.commit()

    with pytest.raises(check_service.CheckConfigInvalidError, match="automatic baseline"):
        check_service.create_check(
            db_session,
            suite_id=suite.id,
            name="vol",
            kind="anomaly",
            expectation_type="monitor:anomaly",
            config={"target_metric": "row_count", KEY: True},
            warn_threshold=None,
            fail_threshold=Decimal(4),
            critical_threshold=None,
            actor_id=admin.id,
        )


# ── yearly (ADR 0048 phase 7) ────────────────────────────────────────────────

_YEARLY_SHAPE = {"window": 14, "min_points": 7, "seasonality": False, "yearly": True}
_NOT_YEARLY = {"window": 14, "min_points": 7, "seasonality": False}


def test_yearly_is_part_of_the_shape_group_and_off_when_left_out() -> None:
    assert svc.validate({"baseline": _YEARLY_SHAPE}) == {"baseline": _YEARLY_SHAPE}
    # A document that sets the group without it is the group with yearly off; one saved
    # before the switch existed reads the same.
    assert svc.validate({"baseline": _NOT_YEARLY}) == {"baseline": _NOT_YEARLY}
    assert svc.resolve({"baseline": _NOT_YEARLY}).shape.yearly is False
    with pytest.raises(svc.CoverageSettingsInvalidError, match="yearly must be true or false"):
        svc.validate({"baseline": {**_YEARLY_SHAPE, "yearly": "yes"}})


@pytest.mark.parametrize(
    ("workspace", "asset", "yearly", "level"),
    [
        (None, None, False, "default"),
        ({"baseline": _YEARLY_SHAPE}, None, True, "workspace"),
        # A level sets the group whole: an asset shape without yearly switches it off there.
        ({"baseline": _YEARLY_SHAPE}, {"baseline": _NOT_YEARLY}, False, "asset"),
        (None, {"baseline": _YEARLY_SHAPE}, True, "asset"),
    ],
)
def test_yearly_resolves_asset_then_workspace_then_default(
    workspace: dict[str, Any] | None,
    asset: dict[str, Any] | None,
    yearly: bool,
    level: str,
) -> None:
    effective = svc.resolve(workspace, asset)

    assert (effective.shape.yearly, effective.shape_from) == (yearly, level)


def test_yearly_reaches_the_row_count_and_freshness_baselines_only() -> None:
    coverage = _coverage({"baseline": _YEARLY_SHAPE})
    volume = _check()
    freshness = _check(metric="freshness_age_hours", column="loaded_at")
    profile = _check(metric="column_profile")

    configs = {}
    for check in (volume, freshness, profile):
        settings = coverage.for_check(check)
        assert settings is not None
        configs[check.config["target_metric"]] = settings.config(dict(check.config))

    assert anomaly_params(configs["row_count"]).yearly is True
    assert anomaly_params(configs["freshness_age_hours"]).yearly is True
    assert "yearly" not in configs["column_profile"]
    assert anomaly_params(configs["column_profile"]).yearly is False


def test_a_stored_yearly_does_not_outlive_the_setting() -> None:
    """The snapshot an inheriting check was created with says yearly on; the setting now says
    off. The run uses the setting."""
    check = _check(yearly=True, **_flag(True))

    settings = _coverage(None).for_check(check)

    assert settings is not None
    assert settings.config(dict(check.config))["yearly"] is False


def test_switching_yearly_on_an_inheriting_check_is_an_edit() -> None:
    check = _check(**_flag(True))

    echoed, _ = _settle(
        check, config={**svc.strip_flag(check.config), "yearly": False}, workspace=None
    )
    switched, _ = _settle(
        check, config={**svc.strip_flag(check.config), "yearly": True}, workspace=None
    )

    assert echoed[KEY] is True
    assert (switched[KEY], switched["yearly"]) == (False, True)


def test_an_inheriting_check_runs_yearly_when_the_setting_turns_it_on_and_keeps_its_days_when_off(
    db_session: Any, conn: Connection, admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    asset, suite = _covered(db_session, conn)
    volume = _named(db_session, suite, cov.VOLUME_CHECK)
    svc.set_workspace(db_session, {"baseline": _YEARLY_SHAPE}, actor=admin)
    db_session.commit()

    on = _executor(db_session, conn, suite, 100, monkeypatch)(volume)
    db_session.flush()

    assert on.observed_value is not None and on.expected_value is not None
    assert on.observed_value["yearly"] == {
        "applied": False,
        "reason": "no_earlier_year",
    }
    assert on.expected_value["yearly"] is True
    assert on.expected_value["inherited_from"]["baseline"] == "workspace"
    row = get_baseline(db_session, volume.id)
    assert row is not None
    days = row.baseline["daily"]
    assert len(days["points"]) == 1

    # An asset override without yearly switches it off for this table only.
    asset.auto_coverage_config = {"baseline": _NOT_YEARLY}
    db_session.commit()
    off = _executor(db_session, conn, suite, 101, monkeypatch)(volume)
    db_session.flush()
    db_session.refresh(row)

    assert off.observed_value is not None and "yearly" not in off.observed_value
    assert row.baseline["daily"] == days
    assert len(row.baseline["observations"]) == 2
