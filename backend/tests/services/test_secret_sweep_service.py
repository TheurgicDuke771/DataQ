"""Tests for the orphan-secret sweep (#1059).

The unit under test decides whether a live warehouse credential gets deleted, so the
emphasis here is on the cases where it must REFUSE — an unreadable age, a
still-referenced secret, a store that cannot enumerate itself. A sweep that
over-deletes is unrecoverable once the purge is a KV metadata delete (ADR 0039 §7).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from backend.app.core.secrets import SecretInfo
from backend.app.db.models import Connection, Suite, SuiteNotification, User
from backend.app.services import secret_sweep_service
from backend.app.services.secret_sweep_service import (
    _OWNER_COLUMNS,
    _OWNER_JSON_PATHS,
    find_orphan_secrets,
    sweep_orphan_secrets,
)

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=90)
RECENT = NOW - timedelta(days=1)
GRACE = timedelta(days=30)


class _FakeStore:
    """A store that can enumerate itself; records deletes."""

    def __init__(self, secrets: list[SecretInfo]) -> None:
        self._secrets = secrets
        self.deleted: list[str] = []

    def get(self, name: str) -> str:  # pragma: no cover - not exercised
        raise AssertionError("the sweep must never read a secret VALUE")

    def set(self, name: str, value: str) -> None:  # pragma: no cover
        raise AssertionError("the sweep must never write")

    def delete(self, name: str) -> None:
        self.deleted.append(name)

    def list_secrets(self) -> list[SecretInfo]:
        return list(self._secrets)


class _UnlistableStore(_FakeStore):
    """Mirrors EnvSecretStore and every test double: no `list_secrets`."""

    list_secrets = None  # type: ignore[assignment]


def _user(session: Session) -> User:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@ex")
    session.add(user)
    session.flush()
    return user


def _connection(
    session: Session, *, secret_ref: str | None, config: dict[str, object] | None = None
) -> Connection:
    conn = Connection(
        id=uuid.uuid4(),
        name=f"c-{uuid.uuid4().hex[:6]}",
        type="snowflake",
        env="dev",
        config=config or {},
        secret_ref=secret_ref,
        created_by=_user(session).id,
    )
    session.add(conn)
    session.flush()
    return conn


# ── the decision itself ───────────────────────────────────────────────────────


def test_unreferenced_and_old_secret_is_an_orphan(db_session: Session) -> None:
    orphans, too_young = find_orphan_secrets(
        db_session,
        secrets=[SecretInfo("conn-snowflake-dead-dev-abc123", OLD)],
        grace=GRACE,
        now=NOW,
    )
    assert orphans == ["conn-snowflake-dead-dev-abc123"]
    assert too_young == []


def test_referenced_secret_is_never_an_orphan(db_session: Session) -> None:
    _connection(db_session, secret_ref="conn-snowflake-live-dev-abc123")
    orphans, too_young = find_orphan_secrets(
        db_session,
        secrets=[SecretInfo("conn-snowflake-live-dev-abc123", OLD)],
        grace=GRACE,
        now=NOW,
    )
    assert orphans == []
    assert too_young == []


def test_secret_inside_the_grace_period_is_held_back(db_session: Session) -> None:
    """The race this guards: a connection-create writes its secret, then its commit
    fails or is still in flight. Purging on age alone would delete the credential of
    a connection being created right now."""
    orphans, too_young = find_orphan_secrets(
        db_session, secrets=[SecretInfo("conn-x-dev-abc123", RECENT)], grace=GRACE, now=NOW
    )
    assert orphans == []
    assert too_young == ["conn-x-dev-abc123"]


def test_unknown_age_is_treated_as_too_young(db_session: Session) -> None:
    """A store that cannot date a secret must never have it purged — the safe
    direction for a destructive action. `created_at=None` is what OpenBao returns
    when the metadata read fails, so this is a live path, not a theoretical one."""
    orphans, too_young = find_orphan_secrets(
        db_session, secrets=[SecretInfo("conn-x-dev-abc123", None)], grace=GRACE, now=NOW
    )
    assert orphans == []
    assert too_young == ["conn-x-dev-abc123"]


def test_foreign_secrets_are_out_of_scope(db_session: Session) -> None:
    """A vault may be shared. Anything DataQ did not mint is never a candidate,
    however old and however unreferenced."""
    orphans, too_young = find_orphan_secrets(
        db_session,
        secrets=[
            SecretInfo("someone-elses-api-key", OLD),
            SecretInfo("snowflake-password-harness", OLD),
        ],
        grace=GRACE,
        now=NOW,
    )
    assert orphans == []
    assert too_young == []


# ── the ownership registry — the part that arms the delete ────────────────────


def test_slack_webhook_ref_is_registered(db_session: Session) -> None:
    """Slack and Teams are SEPARATE refs on one row (#633). Registering only
    `webhook_secret_ref` would make every Slack webhook secret look unowned — the
    exact bug this registry exists to prevent, and one this change originally had."""
    conn = _connection(db_session, secret_ref=None)
    suite = Suite(id=uuid.uuid4(), name="s", connection_id=conn.id, created_by=conn.created_by)
    db_session.add(suite)
    db_session.flush()
    db_session.add(
        SuiteNotification(
            id=uuid.uuid4(),
            suite_id=suite.id,
            webhook_secret_ref="suite-notif-teams-1",
            slack_webhook_secret_ref="suite-notif-slack-1",
        )
    )
    db_session.flush()
    orphans, _ = find_orphan_secrets(
        db_session,
        secrets=[
            SecretInfo("suite-notif-teams-1", OLD),
            SecretInfo("suite-notif-slack-1", OLD),
        ],
        grace=GRACE,
        now=NOW,
    )
    assert orphans == []


def test_iceberg_catalog_secret_in_jsonb_is_registered(db_session: Session) -> None:
    """`catalog_secret_name` names a SecretStore entry but lives in `Connection.config`
    JSONB, so no column-level audit can see it — and it is provisioned out of band,
    so no "what did we write" audit sees it either. Prefix scoping would not save it:
    an operator may name it `conn-…`."""
    _connection(
        db_session,
        secret_ref=None,
        config={"catalog_type": "sql", "catalog_secret_name": "conn-iceberg-catalog-pw"},
    )
    orphans, _ = find_orphan_secrets(
        db_session,
        secrets=[SecretInfo("conn-iceberg-catalog-pw", OLD)],
        grace=GRACE,
        now=NOW,
    )
    assert orphans == []


def test_every_secret_ref_column_is_registered() -> None:
    """Introspection guard: a new `*secret_ref*` column must join the registry.

    A missing entry does not fail loudly — it makes live credentials look unowned,
    i.e. it silently arms a delete. So the schema, not a checklist, is the source of
    truth. Mirrors `test_every_asset_fk_has_a_sweep_guard` (#770).
    """
    registered = {(c.parent.class_.__tablename__, c.key) for c in _OWNER_COLUMNS}
    for model in (Connection, SuiteNotification):
        for column in model.__table__.columns:
            if "secret_ref" in column.key:
                assert (model.__tablename__, column.key) in registered, (
                    f"{model.__tablename__}.{column.key} holds a SecretStore name but is "
                    "not in _OWNER_COLUMNS — the sweep would treat its secrets as orphans"
                )


def test_json_registry_is_not_empty() -> None:
    """Pins the JSONB half explicitly: it is the half no schema introspection can
    check, so losing it would be silent in a way the column test above is not."""
    assert (Connection.config, "catalog_secret_name") in _OWNER_JSON_PATHS


# ── the sweep wrapper ─────────────────────────────────────────────────────────


def test_sweep_reports_without_deleting_by_default(db_session: Session) -> None:
    store = _FakeStore([SecretInfo("conn-dead-dev-abc123", OLD)])
    result = sweep_orphan_secrets(db_session, store=store, grace_days=30, purge=False, now=NOW)
    assert result.orphans == ["conn-dead-dev-abc123"]
    assert result.purged == []
    assert store.deleted == []


def test_sweep_purges_only_when_enabled(db_session: Session) -> None:
    store = _FakeStore([SecretInfo("conn-dead-dev-abc123", OLD)])
    result = sweep_orphan_secrets(db_session, store=store, grace_days=30, purge=True, now=NOW)
    assert result.purged == ["conn-dead-dev-abc123"]
    assert store.deleted == ["conn-dead-dev-abc123"]


def test_sweep_disabled_by_non_positive_grace(db_session: Session) -> None:
    store = _FakeStore([SecretInfo("conn-dead-dev-abc123", OLD)])
    result = sweep_orphan_secrets(db_session, store=store, grace_days=0, purge=True, now=NOW)
    assert result == secret_sweep_service.OrphanSweepResult(0, [], [], [])
    assert store.deleted == []


def test_sweep_skips_a_store_that_cannot_enumerate(db_session: Session) -> None:
    """Absence of `list_secrets` must not be read as an empty vault — an empty
    listing means "every secret is an orphan"."""
    store = _UnlistableStore([SecretInfo("conn-dead-dev-abc123", OLD)])
    result = sweep_orphan_secrets(db_session, store=store, grace_days=30, purge=True, now=NOW)
    assert result.orphans == []
    assert store.deleted == []


def test_sweep_propagates_a_store_outage_instead_of_reporting_zero(db_session: Session) -> None:
    """A vault that cannot be listed must fail the task, never look like a clean
    vault — the #954 masquerade applied to a destructive path."""

    class _BrokenStore(_FakeStore):
        def list_secrets(self) -> list[SecretInfo]:
            raise RuntimeError("vault sealed")

    with pytest.raises(RuntimeError, match="vault sealed"):
        sweep_orphan_secrets(db_session, store=_BrokenStore([]), grace_days=30, purge=True, now=NOW)
