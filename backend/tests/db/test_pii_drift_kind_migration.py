"""Up/down test for `d4e8f1a27b63_widen_check_kind_for_pii_drift` (ADR 0049 phase 2)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.app.db.models import CHECK_KINDS
from backend.tests.support.migrations import load_migration, run_migration

_FILE = "d4e8f1a27b63_widen_check_kind_for_pii_drift.py"


def _suite(db: Any) -> uuid.UUID:
    user_id, conn_id, suite_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db.execute(
        text("INSERT INTO users (id, aad_object_id, email) VALUES (:id, :oid, :email)"),
        {"id": user_id, "oid": uuid.uuid4().hex, "email": f"m-{user_id.hex[:8]}@acme.io"},
    )
    db.execute(
        text(
            "INSERT INTO connections (id, name, type, env, config, secret_ref, created_by) "
            "VALUES (:id, :name, 'postgres', 'dev', '{}'::jsonb, 'kv', :uid)"
        ),
        {"id": conn_id, "name": f"c-{conn_id.hex[:8]}", "uid": user_id},
    )
    db.execute(
        text(
            "INSERT INTO suites (id, name, connection_id, created_by) "
            "VALUES (:id, :name, :cid, :uid)"
        ),
        {"id": suite_id, "name": f"s-{suite_id.hex[:8]}", "cid": conn_id, "uid": user_id},
    )
    return suite_id


def _insert_check(db: Any, suite_id: uuid.UUID, kind: str) -> None:
    db.execute(
        text(
            "INSERT INTO checks (id, suite_id, name, kind, expectation_type, config) "
            "VALUES (:id, :sid, :name, :kind, :et, '{}'::jsonb)"
        ),
        {
            "id": uuid.uuid4(),
            "sid": suite_id,
            "name": f"{kind}-{uuid.uuid4().hex[:6]}",
            "kind": kind,
            "et": f"monitor:{kind}",
        },
    )


def test_downgrade_refuses_pii_drift_and_upgrade_admits_it(db_session: Any) -> None:
    suite_id = _suite(db_session)
    run_migration(db_session, _FILE, "downgrade")  # the fixture schema already has the wide set
    with pytest.raises(IntegrityError), db_session.begin_nested():
        _insert_check(db_session, suite_id, "pii_drift")
    _insert_check(db_session, suite_id, "aggregate")  # the previous set still holds

    run_migration(db_session, _FILE, "upgrade")
    _insert_check(db_session, suite_id, "pii_drift")
    with pytest.raises(IntegrityError), db_session.begin_nested():
        _insert_check(db_session, suite_id, "not_a_kind")


def test_the_widened_set_matches_the_model(db_session: Any) -> None:
    """The migration is a frozen copy and must never import the model; this is the one place
    the two are compared."""
    module = load_migration(_FILE)
    assert module._AFTER == ", ".join(f"'{k}'" for k in CHECK_KINDS)
    assert module._BEFORE == ", ".join(f"'{k}'" for k in CHECK_KINDS if k != "pii_drift")
