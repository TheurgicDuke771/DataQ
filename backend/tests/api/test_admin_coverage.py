"""`/admin/coverage` and the per-asset override — automatic-baseline sensitivity (#1661)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.db.models import Asset, AuditEvent
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import coverage_settings_service as svc

_DEFAULT = {"warn": 3.0, "fail": 4.0, "critical": 6.0, "source": "default"}
_LOOSE = {"warn": 5, "fail": 7, "critical": 9}
_SHAPE = {"window": 14, "min_points": 7, "seasonality": False}

Role = Callable[..., tuple[Any, dict[str, str]]]


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def asset(db_session: Any) -> Asset:
    row = Asset(namespace="postgres://h:5432", name=f"db.s.t_{uuid.uuid4().hex[:6]}")
    db_session.add(row)
    db_session.commit()
    return row


def test_read_reports_the_defaults_when_nothing_is_stored(client: TestClient) -> None:
    body = client.get("/api/v1/admin/coverage").json()

    defaults = {
        "sensitivity": dict.fromkeys(svc.BASELINES, _DEFAULT),
        "baseline": {
            "window": 8,
            "min_points": 4,
            "seasonality": True,
            "yearly": False,
            "source": "default",
        },
    }
    assert body == {
        "setting": {},
        "effective": defaults,
        "defaults": defaults,
        "is_default": True,
        "updated_by": None,
        "updated_at": None,
    }


def test_put_stores_one_group_and_leaves_the_rest_at_default(
    client: TestClient, db_session: Any
) -> None:
    resp = client.put("/api/v1/admin/coverage", json={"sensitivity": {"profile": _LOOSE}})

    assert resp.status_code == 200
    body = resp.json()
    assert body["setting"] == {"sensitivity": {"profile": {k: float(v) for k, v in _LOOSE.items()}}}
    assert body["effective"]["sensitivity"]["profile"] == {
        "warn": 5.0,
        "fail": 7.0,
        "critical": 9.0,
        "source": "workspace",
    }
    assert body["effective"]["sensitivity"]["volume"] == _DEFAULT
    assert body["effective"]["baseline"]["source"] == "default"
    assert body["defaults"]["sensitivity"]["profile"] == _DEFAULT
    assert (body["is_default"], bool(body["updated_by"])) == (False, True)
    events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "coverage_setting.update")
    ).all()
    assert len(events) == 1


def test_delete_resets_to_defaults(client: TestClient, db_session: Any) -> None:
    client.put("/api/v1/admin/coverage", json={"baseline": _SHAPE})

    resp = client.delete("/api/v1/admin/coverage")

    assert resp.status_code == 200
    assert resp.json()["is_default"] is True
    assert svc.get_row(db_session) is None


@pytest.mark.parametrize(
    "payload",
    [
        {"sensitivity": {"volume": {"warn": 5, "fail": 4, "critical": 9}}},
        {"sensitivity": {"volume": {"warn": 5, "fail": 7}}},
        {"sensitivity": {"schema": _LOOSE}},
        {"baseline": {**_SHAPE, "min_points": 20}},
        {"baseline": {"window": 14}},
        {"thresholds": _LOOSE},
        # Strict types: none of these may coerce into a saved setting.
        {"sensitivity": {"volume": {**_LOOSE, "warn": True}}},
        {"sensitivity": {"volume": {**_LOOSE, "fail": "7"}}},
        {"baseline": {**_SHAPE, "window": "14"}},
        {"baseline": {**_SHAPE, "seasonality": "yes"}},
    ],
)
def test_an_invalid_document_is_a_422_and_stores_nothing(
    client: TestClient, db_session: Any, payload: dict[str, Any]
) -> None:
    resp = client.put("/api/v1/admin/coverage", json=payload)

    assert resp.status_code == 422
    assert svc.get_row(db_session) is None


def test_an_unordered_group_names_the_service_error(client: TestClient) -> None:
    resp = client.put(
        "/api/v1/admin/coverage",
        json={"sensitivity": {"volume": {"warn": 5, "fail": 4, "critical": 9}}},
    )

    assert resp.json()["error"]["code"] == "coverage_settings_invalid"


def test_putting_a_document_that_sets_nothing_reads_as_default(client: TestClient) -> None:
    client.put("/api/v1/admin/coverage", json={"baseline": _SHAPE})

    body = client.put("/api/v1/admin/coverage", json={}).json()

    assert (body["is_default"], body["setting"], body["updated_by"]) == (True, {}, None)


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_non_admins_are_refused(client: TestClient, as_role: Role, role: str) -> None:
    _, headers = as_role(role)

    statuses = [
        client.get("/api/v1/admin/coverage", headers=headers).status_code,
        client.put(
            "/api/v1/admin/coverage", json={"baseline": _SHAPE}, headers=headers
        ).status_code,
        client.delete("/api/v1/admin/coverage", headers=headers).status_code,
    ]

    assert statuses == [403, 403, 403]


# ── the per-asset override ───────────────────────────────────────────────────


def _settings(client: TestClient, asset: Asset, headers: dict[str, str] | None = None) -> Any:
    return client.get(f"/api/v1/assets/{asset.id}/coverage-settings", headers=headers)


def test_an_asset_with_no_override_reads_the_workspace_setting(
    client: TestClient, asset: Asset
) -> None:
    client.put("/api/v1/admin/coverage", json={"sensitivity": {"profile": _LOOSE}})

    body = _settings(client, asset).json()

    assert body["override"] is None
    assert body["effective"]["sensitivity"]["profile"]["source"] == "workspace"
    assert body["effective"]["sensitivity"]["volume"]["source"] == "default"


def test_an_override_wins_for_its_group_only_and_is_audited(
    client: TestClient, db_session: Any, asset: Asset
) -> None:
    client.put(
        "/api/v1/admin/coverage", json={"sensitivity": {"profile": _LOOSE}, "baseline": _SHAPE}
    )

    resp = client.patch(
        f"/api/v1/assets/{asset.id}",
        json={
            "auto_coverage_config": {
                "sensitivity": {"profile": {"warn": 8, "fail": 9, "critical": 10}}
            }
        },
    )

    assert resp.status_code == 200
    body = _settings(client, asset).json()
    assert body["override"] == {
        "sensitivity": {"profile": {"warn": 8.0, "fail": 9.0, "critical": 10.0}}
    }
    assert body["effective"]["sensitivity"]["profile"] == {
        "warn": 8.0,
        "fail": 9.0,
        "critical": 10.0,
        "source": "asset",
    }
    assert body["effective"]["baseline"] == {**_SHAPE, "yearly": False, "source": "workspace"}
    event = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "asset.update")).one()
    assert event.before["auto_coverage_config"] is None
    assert event.after["auto_coverage_config"] == body["override"]


def test_null_removes_the_override_and_omitting_it_leaves_it(
    client: TestClient, db_session: Any, asset: Asset
) -> None:
    url = f"/api/v1/assets/{asset.id}"
    client.patch(url, json={"auto_coverage_config": {"baseline": _SHAPE}})

    client.patch(url, json={"description": "orders"})
    kept = _settings(client, asset).json()["override"]
    client.patch(url, json={"auto_coverage_config": None})
    db_session.refresh(asset)

    assert kept == {"baseline": _SHAPE}
    assert asset.auto_coverage_config is None
    # SQL NULL, not JSON null: "no override" is one state the database can ask about.
    assert db_session.scalar(
        select(Asset.id).where(Asset.id == asset.id, Asset.auto_coverage_config.is_(None))
    )


def test_an_override_that_sets_no_group_is_stored_as_none(
    client: TestClient, db_session: Any, asset: Asset
) -> None:
    client.patch(f"/api/v1/assets/{asset.id}", json={"auto_coverage_config": {"sensitivity": {}}})
    db_session.refresh(asset)

    assert asset.auto_coverage_config is None


def test_a_refused_override_changes_nothing_else_in_the_request(
    client: TestClient, db_session: Any, asset: Asset
) -> None:
    resp = client.patch(
        f"/api/v1/assets/{asset.id}",
        json={
            "description": "should not land",
            "auto_coverage_config": {"baseline": {**_SHAPE, "min_points": 50}},
        },
    )
    db_session.refresh(asset)

    assert resp.status_code == 422
    assert (asset.description, asset.auto_coverage_config) == (None, None)


def test_a_refused_override_leaves_the_session_clean(db_session: Any, asset: Asset) -> None:
    """A caller that catches the refusal and commits later must not persist half a request."""
    from backend.app.services import asset_view_service

    with pytest.raises(svc.CoverageSettingsInvalidError):
        asset_view_service.update_asset_metadata(
            db_session,
            asset.id,
            description="should not land",
            set_description=True,
            auto_coverage_config={"baseline": {"window": 1}},
            set_auto_coverage_config=True,
        )

    assert not db_session.dirty


def test_any_member_reads_an_assets_settings_but_only_an_admin_overrides(
    client: TestClient, as_role: Role, asset: Asset
) -> None:
    _, viewer = as_role("viewer")

    read = _settings(client, asset, viewer)
    write = client.patch(
        f"/api/v1/assets/{asset.id}",
        json={"auto_coverage_config": {"baseline": _SHAPE}},
        headers=viewer,
    )

    assert (read.status_code, write.status_code) == (200, 403)


def test_settings_of_an_unknown_asset_are_a_404(client: TestClient) -> None:
    resp = client.get(f"/api/v1/assets/{uuid.uuid4()}/coverage-settings")

    assert resp.status_code == 404


def test_yearly_is_set_with_the_baseline_shape_at_either_level(
    client: TestClient, asset: Asset
) -> None:
    yearly = {**_SHAPE, "yearly": True}

    resp = client.put("/api/v1/admin/coverage", json={"baseline": yearly})

    assert resp.status_code == 200
    body = resp.json()
    assert body["setting"] == {"baseline": yearly}
    assert body["effective"]["baseline"] == {**yearly, "source": "workspace"}
    # The asset sets the group whole: left out, yearly is off for this table.
    client.patch(
        f"/api/v1/assets/{asset.id}",
        json={"auto_coverage_config": {"baseline": _SHAPE}},
    )
    assert _settings(client, asset).json()["effective"]["baseline"] == {
        **_SHAPE,
        "yearly": False,
        "source": "asset",
    }


@pytest.mark.parametrize("value", ["yes", 1, None])
def test_yearly_must_be_a_boolean(client: TestClient, value: Any) -> None:
    resp = client.put("/api/v1/admin/coverage", json={"baseline": {**_SHAPE, "yearly": value}})

    assert resp.status_code == 422
