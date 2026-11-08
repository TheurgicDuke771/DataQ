"""Data test for `d9d6d88d6642_rekey_flatfile_batch_assets` (#2481)."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from backend.app.services.asset_identity import resolve_asset_identity
from backend.tests.support.migrations import load_migration, run_migration

_FILE = "d9d6d88d6642_rekey_flatfile_batch_assets.py"
_NS = "s3://landing"


@pytest.fixture
def conn(db_session: Any) -> uuid.UUID:
    user_id, conn_id = uuid.uuid4(), uuid.uuid4()
    db_session.execute(
        text("INSERT INTO users (id, email) VALUES (:id, :email)"),
        {"id": user_id, "email": f"u-{user_id.hex[:8]}@example.com"},
    )
    db_session.execute(
        text(
            "INSERT INTO connections (id, name, type, env, config, secret_ref, created_by)"
            " VALUES (:id, :name, 's3', 'dev', '{\"bucket\": \"landing\"}'::jsonb, 'kv', :uid)"
        ),
        {"id": conn_id, "name": f"c-{conn_id.hex[:8]}", "uid": user_id},
    )
    return conn_id


def _asset(db: Any, name: str) -> uuid.UUID:
    asset_id = uuid.uuid4()
    db.execute(
        text("INSERT INTO assets (id, namespace, name, env) VALUES (:id, :ns, :name, 'dev')"),
        {"id": asset_id, "ns": _NS, "name": name},
    )
    return asset_id


def _suite(db: Any, conn: uuid.UUID, asset_id: uuid.UUID, target: dict[str, Any]) -> uuid.UUID:
    suite_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO suites (id, name, connection_id, target, asset_id)"
            " VALUES (:id, :name, :conn, CAST(:target AS jsonb), :asset)"
        ),
        {
            "id": suite_id,
            "name": f"s-{suite_id.hex[:8]}",
            "conn": conn,
            "target": json.dumps(target),
            "asset": asset_id,
        },
    )
    return suite_id


def _run(db: Any, suite_id: uuid.UUID, asset_id: uuid.UUID) -> uuid.UUID:
    run_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO runs (id, suite_id, status, asset_id)"
            " VALUES (:id, :suite, 'succeeded', :asset)"
        ),
        {"id": run_id, "suite": suite_id, "asset": asset_id},
    )
    return run_id


def _asset_of(db: Any, table: str, row_id: uuid.UUID) -> tuple[uuid.UUID, str]:
    asset_id, name = db.execute(
        text(
            f"SELECT a.id, a.name FROM {table} t JOIN assets a ON a.id = t.asset_id"  # nosec B608
            " WHERE t.id = :id"
        ),
        {"id": row_id},
    ).one()
    return asset_id, name


def _upgrade(db: Any) -> None:
    run_migration(db, _FILE, "upgrade")


_ORDERS = {"prefix": "orders/", "pattern": r"orders_(\d+)\.csv"}
_SALES = {"prefix": "sales/", "pattern": r"orders_(\d+)\.csv"}


def test_an_asset_with_one_new_name_is_renamed_in_place(db_session: Any, conn: uuid.UUID) -> None:
    asset_id = _asset(db_session, "orders_")
    suite = _suite(db_session, conn, asset_id, _ORDERS)

    _upgrade(db_session)

    assert _asset_of(db_session, "suites", suite) == (asset_id, "orders/")


def test_a_merged_asset_is_split_and_each_suite_takes_its_runs_along(
    db_session: Any, conn: uuid.UUID
) -> None:
    merged = _asset(db_session, "orders_")
    orders, sales = _suite(db_session, conn, merged, _ORDERS), _suite(
        db_session, conn, merged, _SALES
    )
    orders_run, sales_run = _run(db_session, orders, merged), _run(db_session, sales, merged)

    _upgrade(db_session)

    orders_asset = _asset_of(db_session, "suites", orders)
    sales_asset = _asset_of(db_session, "suites", sales)
    assert {orders_asset[1], sales_asset[1]} == {"orders/", "sales/"}
    assert orders_asset[0] != sales_asset[0]
    assert merged not in {orders_asset[0], sales_asset[0]}
    assert _asset_of(db_session, "runs", orders_run) == orders_asset
    assert _asset_of(db_session, "runs", sales_run) == sales_asset


def test_a_new_name_already_held_moves_the_suite_to_that_asset(
    db_session: Any, conn: uuid.UUID
) -> None:
    old, existing = _asset(db_session, "orders_"), _asset(db_session, "orders/")
    suite = _suite(db_session, conn, old, _ORDERS)

    _upgrade(db_session)

    assert _asset_of(db_session, "suites", suite)[0] == existing


@pytest.mark.parametrize(
    ("target", "name"),
    [
        ({"pattern": r"orders_(\d+)\.csv"}, "orders_"),
        ({"prefix": "retail/", "pattern": r"retail/orders_(\d+)\.csv"}, "retail/"),
        ({"path": "retail/orders.csv"}, "retail/orders.csv"),
    ],
)
def test_a_target_whose_name_does_not_change_is_left_alone(
    db_session: Any, conn: uuid.UUID, target: dict[str, Any], name: str
) -> None:
    asset_id = _asset(db_session, name)
    suite = _suite(db_session, conn, asset_id, target)

    _upgrade(db_session)

    assert _asset_of(db_session, "suites", suite) == (asset_id, name)


@pytest.mark.parametrize(
    "target",
    [
        _ORDERS,
        _SALES,
        {"pattern": r"orders_(\d+)\.csv"},
        {"prefix": "retail/", "pattern": r"retail/orders_(\d+)\.csv"},
        {"prefix": "retail/", "pattern": r"(\d+)\.csv"},
        {"prefix": "a/b/", "pattern": r"c/d_(\d+)"},
        {"prefix": "/lead/", "pattern": r"x_(\d+)"},
        {"pattern": r"(\d+)\.csv"},
    ],
)
def test_the_frozen_naming_matches_the_app_at_this_revision(target: dict[str, Any]) -> None:
    frozen = load_migration(_FILE).batch_asset_name(target)
    assert frozen == resolve_asset_identity("s3", {"bucket": "landing"}, target).name
