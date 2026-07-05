"""Tests for the auto-classify-on-suite-create task (#634).

DB-backed on the `_auto_classify_columns` helper (the derive step is monkeypatched,
so no live warehouse). Covers: persists a derived policy, never clobbers an existing
one, skips a targetless / batch-pattern suite, fails soft on introspection error,
and doesn't persist an empty suggestion.
"""

from __future__ import annotations

import uuid
from typing import Any

from backend.app.db.models import Connection, Suite, User
from backend.app.services import profile_service
from backend.app.worker import tasks


def _suite(
    db: Any, *, target: dict[str, Any] | None, column_policy: dict[str, Any] | None = None
) -> Suite:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@x.io")
    db.add(owner)
    db.flush()
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a"},
        secret_ref="kv",
        created_by=owner.id,
    )
    db.add(conn)
    db.flush()
    suite = Suite(
        name="s",
        connection_id=conn.id,
        created_by=owner.id,
        target=target,
        column_policy=column_policy,
    )
    db.add(suite)
    db.commit()
    return suite


def test_persists_derived_policy(db_session: Any, monkeypatch: Any) -> None:
    suite = _suite(db_session, target={"table": "ORDERS", "schema": "RETAIL"})
    monkeypatch.setattr(
        profile_service,
        "suggest_policy_for_target",
        lambda *a, **k: {"identifier_column": "ORDER_NUMBER", "pii_columns": ["EMAIL"]},
    )
    assert tasks._auto_classify_columns(db_session, suite_id=suite.id) == "classified"
    db_session.refresh(suite)
    assert suite.column_policy == {"identifier_column": "ORDER_NUMBER", "pii_columns": ["EMAIL"]}


def test_never_clobbers_an_existing_policy(db_session: Any, monkeypatch: Any) -> None:
    suite = _suite(db_session, target={"table": "ORDERS"}, column_policy={"pii_columns": ["EMAIL"]})
    called: list[int] = []

    def _record(*a: Any, **k: Any) -> dict[str, Any]:
        called.append(1)
        return {}

    monkeypatch.setattr(profile_service, "suggest_policy_for_target", _record)
    assert tasks._auto_classify_columns(db_session, suite_id=suite.id) == "skipped"
    assert called == []  # short-circuits before any introspection
    db_session.refresh(suite)
    assert suite.column_policy == {"pii_columns": ["EMAIL"]}  # untouched


def test_skips_batch_pattern_target(db_session: Any, monkeypatch: Any) -> None:
    suite = _suite(db_session, target={"pattern": "orders_*.csv"})
    called: list[int] = []

    def _record(*a: Any, **k: Any) -> dict[str, Any]:
        called.append(1)
        return {}

    monkeypatch.setattr(profile_service, "suggest_policy_for_target", _record)
    assert tasks._auto_classify_columns(db_session, suite_id=suite.id) == "skipped"
    assert called == []  # no fixed table/file to profile


def test_fail_soft_on_introspection_error(db_session: Any, monkeypatch: Any) -> None:
    suite = _suite(db_session, target={"table": "ORDERS"})

    def _boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("warehouse unreachable")

    monkeypatch.setattr(profile_service, "suggest_policy_for_target", _boom)
    assert tasks._auto_classify_columns(db_session, suite_id=suite.id) == "error"  # never raises
    db_session.refresh(suite)
    assert suite.column_policy is None  # left unset


def test_empty_suggestion_not_persisted(db_session: Any, monkeypatch: Any) -> None:
    suite = _suite(db_session, target={"table": "ORDERS"})
    monkeypatch.setattr(
        profile_service, "suggest_policy_for_target", lambda *a, **k: {"pii_columns": []}
    )
    assert tasks._auto_classify_columns(db_session, suite_id=suite.id) == "empty"
    db_session.refresh(suite)
    assert suite.column_policy is None


def test_missing_suite_is_a_noop(db_session: Any) -> None:
    assert tasks._auto_classify_columns(db_session, suite_id=uuid.uuid4()) == "skipped"
