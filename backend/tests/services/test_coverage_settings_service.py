"""Sensitivity for automatic baselines: validation and the three-level hierarchy (#1661)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from backend.app.db.models import AuditEvent, User
from backend.app.services import coverage_settings_service as svc

_LOOSE = {"warn": 5, "fail": 7, "critical": 9}
_SHAPE = {"window": 14, "min_points": 7, "seasonality": False}


def _admin(db: Any) -> User:
    user = User(aad_object_id="cov-admin", email="cov-admin@example.com", role="admin")
    db.add(user)
    db.flush()
    return user


def test_nothing_stored_resolves_to_the_built_in_defaults() -> None:
    effective = svc.resolve(None)

    assert {b: s.as_dict() for b, s in effective.sensitivity.items()} == {
        b: {"warn": 3.0, "fail": 4.0, "critical": 6.0} for b in svc.BASELINES
    }
    assert effective.shape.as_dict() == {"window": 8, "min_points": 4, "seasonality": True}
    assert set(effective.sensitivity_from.values()) == {"default"}
    assert effective.shape_from == "default"


def test_each_group_comes_from_the_nearest_level_that_sets_it() -> None:
    workspace = {"sensitivity": {"profile": _LOOSE, "volume": _LOOSE}, "baseline": _SHAPE}
    asset = {"sensitivity": {"volume": {"warn": 2, "fail": 3, "critical": 4}}}

    effective = svc.resolve(workspace, asset)

    assert effective.sensitivity_from == {
        "volume": "asset",
        "freshness": "default",
        "profile": "workspace",
    }
    assert effective.sensitivity["volume"].as_dict() == {"warn": 2.0, "fail": 3.0, "critical": 4.0}
    assert effective.sensitivity["profile"].critical == 9.0
    assert effective.sensitivity["freshness"] == svc.DEFAULT_SENSITIVITY
    assert (effective.shape.window, effective.shape_from) == (14, "workspace")


def test_an_asset_shape_overrides_the_workspace_shape_whole() -> None:
    asset = {"baseline": {"window": 3, "min_points": 3, "seasonality": True}}

    effective = svc.resolve({"baseline": _SHAPE}, asset)

    # min_points 3 came with window 3: never the workspace's 7 above an asset window of 3.
    assert effective.shape.as_dict() == {"window": 3, "min_points": 3, "seasonality": True}
    assert effective.shape_from == "asset"


@pytest.mark.parametrize(
    ("document", "names"),
    [
        ([], "must be an object"),
        ({"thresholds": {}}, "unknown keys: thresholds"),
        ({"sensitivity": {"schema": _LOOSE}}, "unknown keys: schema"),
        ({"sensitivity": {"volume": {"warn": 3, "fail": 4}}}, "warn, fail and critical together"),
        ({"sensitivity": {"volume": {**_LOOSE, "warn": 8}}}, "warn ≤ fail ≤ critical"),
        ({"sensitivity": {"volume": {**_LOOSE, "warn": 0}}}, "above 0"),
        ({"sensitivity": {"volume": {**_LOOSE, "critical": 100}}}, "at most 99"),
        ({"sensitivity": {"volume": {**_LOOSE, "fail": True}}}, "must be a number"),
        ({"sensitivity": {"volume": {**_LOOSE, "fail": "7"}}}, "must be a number"),
        ({"baseline": {"window": 14, "min_points": 7}}, "together"),
        ({"baseline": {**_SHAPE, "min_points": 15}}, "min_points must be between"),
        ({"baseline": {**_SHAPE, "window": 2}}, "window must be between"),
        ({"baseline": {**_SHAPE, "seasonality": "yes"}}, "seasonality must be true or false"),
    ],
)
def test_validate_refuses_and_names_the_problem(document: Any, names: str) -> None:
    with pytest.raises(svc.CoverageSettingsInvalidError, match=names):
        svc.validate(document)


def test_validate_normalises_and_drops_an_empty_sensitivity() -> None:
    assert svc.validate({"sensitivity": {}, "baseline": {**_SHAPE, "window": 14.0}}) == {
        "baseline": _SHAPE
    }
    assert svc.validate({}) == {}


def test_a_stored_group_that_no_longer_parses_falls_to_the_level_below() -> None:
    broken_asset = {"sensitivity": {"volume": {"warn": 9, "fail": 1, "critical": 1}}}

    effective = svc.resolve({"sensitivity": {"volume": _LOOSE}}, broken_asset)

    assert effective.sensitivity_from["volume"] == "workspace"
    assert effective.sensitivity["volume"].warn == 5.0


def test_set_then_reset_round_trips_and_audits_both(db_session: Any) -> None:
    admin = _admin(db_session)

    svc.set_workspace(db_session, {"sensitivity": {"profile": _LOOSE}}, actor=admin)
    stored = svc.workspace_document(db_session)
    svc.reset_workspace(db_session, actor=admin)

    assert stored == {"sensitivity": {"profile": {"warn": 5.0, "fail": 7.0, "critical": 9.0}}}
    assert svc.get_row(db_session) is None
    assert svc.workspace_document(db_session) == {}
    events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "coverage_setting.update")
    ).all()
    assert sorted((e.before is None, e.after is None) for e in events) == [
        (False, True),
        (True, False),
    ]
    assert next(e.after for e in events if e.after)["config"] == stored


def test_saving_a_document_that_sets_nothing_is_a_reset(db_session: Any) -> None:
    admin = _admin(db_session)
    svc.set_workspace(db_session, {"baseline": _SHAPE}, actor=admin)

    returned = svc.set_workspace(db_session, {"sensitivity": {}}, actor=admin)

    assert returned is None
    assert svc.get_row(db_session) is None


def test_an_empty_first_save_stores_no_row_and_no_audit_event(db_session: Any) -> None:
    svc.set_workspace(db_session, {}, actor=_admin(db_session))

    assert svc.get_row(db_session) is None
    assert db_session.scalars(select(AuditEvent.id)).all() == []


def test_a_second_save_records_the_first_as_its_before(db_session: Any) -> None:
    admin = _admin(db_session)
    svc.set_workspace(db_session, {"baseline": _SHAPE}, actor=admin)

    svc.set_workspace(db_session, {"sensitivity": {"volume": _LOOSE}}, actor=admin)

    # Both events share the transaction's timestamp, so pick the update by its shape.
    befores = [
        e.before
        for e in db_session.scalars(
            select(AuditEvent).where(AuditEvent.action == "coverage_setting.update")
        )
    ]
    assert sorted(befores, key=lambda b: b is not None) == [
        None,
        {"id": 1, "config": {"baseline": _SHAPE}, "updated_by": str(admin.id)},
    ]
    assert svc.workspace_document(db_session) == {
        "sensitivity": {"volume": {"warn": 5.0, "fail": 7.0, "critical": 9.0}}
    }


def test_the_ceiling_is_the_deviation_a_never_varying_value_reports() -> None:
    at_ceiling = {"warn": 3, "fail": 4, "critical": 99}

    assert (
        svc.validate({"sensitivity": {"volume": at_ceiling}})["sensitivity"]["volume"]["critical"]
        == 99.0
    )
    with pytest.raises(svc.CoverageSettingsInvalidError, match="never varied"):
        svc.validate({"sensitivity": {"volume": {**at_ceiling, "critical": 99.5}}})


def test_a_refused_save_stores_nothing(db_session: Any) -> None:
    admin = _admin(db_session)

    with pytest.raises(svc.CoverageSettingsInvalidError):
        svc.set_workspace(db_session, {"baseline": {"window": 1}}, actor=admin)

    assert svc.get_row(db_session) is None


def test_reset_with_nothing_stored_writes_no_audit_event(db_session: Any) -> None:
    svc.reset_workspace(db_session, actor=_admin(db_session))

    assert (
        db_session.scalars(
            select(AuditEvent).where(AuditEvent.action == "coverage_setting.update")
        ).all()
        == []
    )
