"""Tests for notification_service — per-suite alert config + webhook resolution.

DB-backed; a dict-backed fake SecretStore stands in for Key Vault. Skips without
TEST_DATABASE_URL.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from backend.app.core.secrets import SecretNotFoundError
from backend.app.db.models import Connection, Suite, SuiteNotification, User
from backend.app.services import notification_service as svc
from backend.app.services.notification_service import (
    InvalidAlertPolicyError,
    InvalidWebhookError,
)


class _FakeStore:
    def __init__(self) -> None:
        self.secrets: dict[str, str] = {}

    def get(self, name: str) -> str:
        try:
            return self.secrets[name]
        except KeyError as exc:
            raise SecretNotFoundError(name) from exc

    def set(self, name: str, value: str) -> None:
        self.secrets[name] = value


def _suite(db: Any) -> Suite:
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
    suite = Suite(name="s", connection_id=conn.id, created_by=owner.id, target={"table": "T"})
    db.add(suite)
    db.commit()
    return suite


def test_get_config_none_until_saved(db_session: Any) -> None:
    suite = _suite(db_session)
    assert svc.get_config(db_session, suite.id) is None


def test_upsert_creates_then_updates(db_session: Any) -> None:
    suite = _suite(db_session)
    store = _FakeStore()
    created = svc.upsert_config(
        db_session,
        suite_id=suite.id,
        enabled=True,
        alert_on="fail",
        webhook=None,
        secret_store=store,
    )
    assert created.alert_on == "fail"
    assert created.webhook_secret_ref is None

    updated = svc.upsert_config(
        db_session,
        suite_id=suite.id,
        enabled=False,
        alert_on="always",
        webhook=None,
        secret_store=store,
    )
    assert updated.id == created.id  # same row (upsert)
    assert updated.enabled is False
    assert updated.alert_on == "always"


def test_upsert_recovers_from_concurrent_first_write_race(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # #384: a concurrent first-write wins the unique (uq_suite_notifications_suite_id)
    # race. Simulate it — a stale read sees no row, the INSERT then hits the
    # constraint, and upsert must fall back to updating the winner's row, not raise
    # IntegrityError (→ 500).
    suite = _suite(db_session)
    winner = SuiteNotification(suite_id=suite.id, enabled=False, alert_on="warn")
    db_session.add(winner)
    db_session.flush()

    real_get = svc.get_config
    seen = {"n": 0}

    def stale_then_real(session: Any, suite_id: Any) -> Any:
        # First read (in upsert) returns None to drive the INSERT path; the
        # post-conflict re-fetch returns the real winner row.
        seen["n"] += 1
        return None if seen["n"] == 1 else real_get(session, suite_id)

    monkeypatch.setattr(svc, "get_config", stale_then_real)

    result = svc.upsert_config(
        db_session,
        suite_id=suite.id,
        enabled=True,
        alert_on="fail",
        webhook=None,
        secret_store=_FakeStore(),
    )

    assert result.enabled is True  # the winner's row was updated, no exception
    assert result.alert_on == "fail"
    rows = db_session.scalars(
        select(SuiteNotification).where(SuiteNotification.suite_id == suite.id)
    ).all()
    assert len(rows) == 1  # no duplicate row inserted


def test_upsert_writes_webhook_through_secret_store(db_session: Any) -> None:
    suite = _suite(db_session)
    store = _FakeStore()
    config = svc.upsert_config(
        db_session,
        suite_id=suite.id,
        enabled=True,
        alert_on="fail",
        webhook="https://contoso.webhook.office.com/hook",
        secret_store=store,
    )
    assert config.webhook_secret_ref == f"suite-notif-{config.id}"
    # The URL lives in the store, not the DB row.
    assert store.secrets[config.webhook_secret_ref] == "https://contoso.webhook.office.com/hook"


def test_upsert_blank_webhook_clears_ref(db_session: Any) -> None:
    suite = _suite(db_session)
    store = _FakeStore()
    svc.upsert_config(
        db_session,
        suite_id=suite.id,
        enabled=True,
        alert_on="fail",
        webhook="https://x.webhook.office.com/h",
        secret_store=store,
    )
    cleared = svc.upsert_config(
        db_session, suite_id=suite.id, enabled=True, alert_on="fail", webhook="", secret_store=store
    )
    assert cleared.webhook_secret_ref is None


def test_upsert_rejects_bad_policy(db_session: Any) -> None:
    suite = _suite(db_session)
    with pytest.raises(InvalidAlertPolicyError):
        svc.upsert_config(
            db_session,
            suite_id=suite.id,
            enabled=True,
            alert_on="nope",
            webhook=None,
            secret_store=_FakeStore(),
        )


def test_upsert_rejects_non_https_webhook(db_session: Any) -> None:
    suite = _suite(db_session)
    with pytest.raises(InvalidWebhookError):
        svc.upsert_config(
            db_session,
            suite_id=suite.id,
            enabled=True,
            alert_on="fail",
            webhook="http://insecure",
            secret_store=_FakeStore(),
        )


def test_upsert_rejects_non_allowlisted_host(db_session: Any) -> None:
    # https but a host outside the Teams/Power-Automate allowlist (SSRF guard).
    suite = _suite(db_session)
    with pytest.raises(InvalidWebhookError):
        svc.upsert_config(
            db_session,
            suite_id=suite.id,
            enabled=True,
            alert_on="fail",
            webhook="https://169.254.169.254/latest/meta-data",
            secret_store=_FakeStore(),
        )


def test_delete_config(db_session: Any) -> None:
    suite = _suite(db_session)
    svc.upsert_config(
        db_session,
        suite_id=suite.id,
        enabled=True,
        alert_on="fail",
        webhook=None,
        secret_store=_FakeStore(),
    )
    assert svc.delete_config(db_session, suite.id) is True
    assert svc.get_config(db_session, suite.id) is None
    assert svc.delete_config(db_session, suite.id) is False  # idempotent


def test_resolve_webhook_prefers_suite_then_workspace(db_session: Any) -> None:
    suite = _suite(db_session)
    store = _FakeStore()
    store.secrets["ws"] = "https://workspace"
    # No config → workspace fallback.
    assert (
        svc.resolve_webhook(None, secret_store=store, workspace_secret_name="ws")
        == "https://workspace"
    )

    config = svc.upsert_config(
        db_session,
        suite_id=suite.id,
        enabled=True,
        alert_on="fail",
        webhook="https://suite.webhook.office.com",
        secret_store=store,
    )
    assert (
        svc.resolve_webhook(config, secret_store=store, workspace_secret_name="ws")
        == "https://suite.webhook.office.com"
    )


def test_resolve_webhook_none_when_nothing_set(db_session: Any) -> None:
    assert svc.resolve_webhook(None, secret_store=_FakeStore(), workspace_secret_name=None) is None
