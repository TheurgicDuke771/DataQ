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

from backend.app.core.config import get_settings
from backend.app.core.secrets import SecretInfo
from backend.app.db.models import Base, Connection, Suite, SuiteNotification, User
from backend.app.services import secret_sweep_service
from backend.app.services.secret_sweep_service import (
    _OWNER_COLUMNS,
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
    orphans, too_young, _unknown = find_orphan_secrets(
        db_session,
        secrets=[SecretInfo("conn-snowflake-dead-dev-abc123", OLD)],
        grace=GRACE,
        now=NOW,
    )
    assert orphans == ["conn-snowflake-dead-dev-abc123"]
    assert too_young == []


def test_referenced_secret_is_never_an_orphan(db_session: Session) -> None:
    _connection(db_session, secret_ref="conn-snowflake-live-dev-abc123")
    orphans, too_young, _unknown = find_orphan_secrets(
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
    orphans, too_young, _unknown = find_orphan_secrets(
        db_session, secrets=[SecretInfo("conn-x-dev-abc123", RECENT)], grace=GRACE, now=NOW
    )
    assert orphans == []
    assert too_young == ["conn-x-dev-abc123"]


def test_foreign_secrets_are_out_of_scope(db_session: Session) -> None:
    """A vault may be shared. Anything DataQ did not mint is never a candidate,
    however old and however unreferenced."""
    orphans, too_young, _unknown = find_orphan_secrets(
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
    orphans, _, _ = find_orphan_secrets(
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
    JSONB, so no COLUMN-level audit can see it — `connection_service` writes and
    rotates it exactly like the primary `secret_ref` (#1181), but that alone doesn't
    make it visible to `test_every_secret_ref_column_is_registered`'s introspection,
    since it is a value inside a JSON blob, not a column. Prefix scoping would not
    save it either: an operator may name it `conn-…`."""
    _connection(
        db_session,
        secret_ref=None,
        config={"catalog_type": "sql", "catalog_secret_name": "conn-iceberg-catalog-pw"},
    )
    orphans, _, _ = find_orphan_secrets(
        db_session,
        secrets=[SecretInfo("conn-iceberg-catalog-pw", OLD)],
        grace=GRACE,
        now=NOW,
    )
    assert orphans == []


# Column-name fragments that mean "this holds a SecretStore name". Broader than
# `secret_ref` on purpose: the registry must not be spelling-bound to today's
# conventions, since a `credential_ref` or `signing_key_name` column would hold one
# just as much and would be just as purgeable.
_SECRET_NAME_COLUMN_HINTS = ("secret_ref", "secret_name", "credential_ref", "key_name")


def test_every_secret_name_column_in_the_schema_is_registered() -> None:
    """Introspection guard over EVERY mapped table (#1059).

    The first version of this test iterated `(Connection, SuiteNotification)` — which
    is exactly the set already in `_OWNER_COLUMNS`, making it a tautology: a NEW model
    with a secret-name column would leave it green and its credentials purgeable. It
    could not express the failure it existed to catch, which is the one test-quality
    mistake this project keeps relearning.

    So it walks `Base.metadata.tables` like its sibling
    `test_every_asset_fk_has_a_sweep_guard` (#770) does, and matches on a set of name
    fragments rather than one spelling.
    """
    registered = {(c.parent.class_.__tablename__, c.key) for c in _OWNER_COLUMNS}
    unregistered = [
        f"{table.name}.{column.key}"
        for table in Base.metadata.tables.values()
        for column in table.columns
        if any(hint in column.key for hint in _SECRET_NAME_COLUMN_HINTS)
        and (table.name, column.key) not in registered
        # `connection_versions.config` snapshots a historical `*_secret_name`, but a
        # version row is an audit record, not an owner: there is no restore endpoint,
        # and the value it names was provisioned by an operator, never written by
        # DataQ. Superseded catalog secrets being purgeable is the intended reading —
        # recorded here rather than left as an unexamined exclusion (CONTRIBUTING 3a).
        and table.name != "connection_versions"
    ]
    assert not unregistered, (
        f"{unregistered} hold SecretStore names but are not in _OWNER_COLUMNS — "
        "the sweep would treat their secrets as orphans and, with purge on, delete them"
    )


def test_any_secret_name_config_key_is_owned_not_just_the_iceberg_one(
    db_session: Session,
) -> None:
    """The JSONB scan follows the CONVENTION, not one key.

    `connection_service._extra_secrets` resolves any `*_secret_name` key generically
    and says so ("no branching on connection.type … no matter how many credentials a
    future type needs"). A registry naming `catalog_secret_name` alone would leave the
    next connection type that adds one silently purgeable — so this uses a key that
    does not exist today on purpose.
    """
    _connection(
        db_session,
        secret_ref=None,
        config={"some_future_secret_name": "conn-future-thing-dev-abc123"},
    )
    orphans, _, _ = find_orphan_secrets(
        db_session,
        secrets=[SecretInfo("conn-future-thing-dev-abc123", OLD)],
        grace=GRACE,
        now=NOW,
    )
    assert orphans == []


def test_workspace_secret_names_from_settings_are_owned(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Six SecretStore names live in `Settings` and are referenced by NO row.

    Three are operator-named with blank defaults, so naming the workspace Teams
    webhook `suite-notif-teams-workspace` — natural, since DataQ mints `suite-notif-*`
    itself — would put a live credential inside the prefix filter with nothing else
    standing between it and a purge.
    """
    monkeypatch.setenv("TEAMS_WEBHOOK_SECRET_NAME", "suite-notif-teams-workspace")
    get_settings.cache_clear()
    try:
        orphans, _, _ = find_orphan_secrets(
            db_session,
            secrets=[SecretInfo("suite-notif-teams-workspace", OLD)],
            grace=GRACE,
            now=NOW,
        )
    finally:
        get_settings.cache_clear()
    assert orphans == []


def test_naive_created_at_from_a_driver_does_not_break_the_sweep(
    db_session: Session,
) -> None:
    """Whether `created_at` is tz-aware is the DRIVER's choice. A naive value would
    raise TypeError on the subtraction, which the task's blanket except swallows into
    a silent "0 orphans" — the janitor stops without saying so."""
    naive_old = OLD.replace(tzinfo=None)
    orphans, _, unknown = find_orphan_secrets(
        db_session,
        secrets=[SecretInfo("conn-naive-dev-abc123", naive_old)],
        grace=GRACE,
        now=NOW,
    )
    assert orphans == ["conn-naive-dev-abc123"]
    assert unknown == []


def test_unknown_age_is_its_own_bucket_not_folded_into_too_young(
    db_session: Session,
) -> None:
    """For OpenBao a None age is what a DENIED metadata read looks like. Folding it
    into `too_young` would report a permissions gap as "everything here is recent" —
    an outage rendered as a state."""
    orphans, too_young, unknown = find_orphan_secrets(
        db_session,
        secrets=[
            SecretInfo("conn-undated-dev-abc123", None),
            SecretInfo("conn-recent-dev-abc123", RECENT),
        ],
        grace=GRACE,
        now=NOW,
    )
    assert orphans == []
    assert too_young == ["conn-recent-dev-abc123"]
    assert unknown == ["conn-undated-dev-abc123"]


def test_future_dated_secret_is_never_purged(db_session: Session) -> None:
    """Clock skew on the store side must fail towards not deleting."""
    orphans, too_young, _ = find_orphan_secrets(
        db_session,
        secrets=[SecretInfo("conn-future-dev-abc123", NOW + timedelta(days=5))],
        grace=GRACE,
        now=NOW,
    )
    assert orphans == []
    assert too_young == ["conn-future-dev-abc123"]


# ── the sweep wrapper ─────────────────────────────────────────────────────────


def test_sweep_reports_without_deleting_by_default(db_session: Session) -> None:
    store = _FakeStore([SecretInfo("conn-dead-dev-abc123", OLD)])
    result = sweep_orphan_secrets(db_session, store=store, grace_days=30, purge=False, now=NOW)
    assert result.orphans == ["conn-dead-dev-abc123"]
    assert result.purge_attempted == []
    assert store.deleted == []


def test_sweep_purges_only_when_enabled(db_session: Session) -> None:
    store = _FakeStore([SecretInfo("conn-dead-dev-abc123", OLD)])
    result = sweep_orphan_secrets(db_session, store=store, grace_days=30, purge=True, now=NOW)
    assert result.purge_attempted == ["conn-dead-dev-abc123"]
    assert store.deleted == ["conn-dead-dev-abc123"]


def test_purge_attempted_records_the_attempt_not_the_outcome(db_session: Session) -> None:
    """`delete` is fail-soft BY CONTRACT — it swallows and logs — so the store cannot
    report success and this field must not claim to. Pinned so the name and the
    behaviour cannot drift apart again (the first version's comment claimed it
    reflected what was actually deleted)."""

    class _FailingDeleteStore(_FakeStore):
        def delete(self, name: str) -> None:
            self.deleted.append(name)  # the store logs and swallows; no signal out

    store = _FailingDeleteStore([SecretInfo("conn-dead-dev-abc123", OLD)])
    result = sweep_orphan_secrets(db_session, store=store, grace_days=30, purge=True, now=NOW)
    assert result.purge_attempted == ["conn-dead-dev-abc123"]


def test_sweep_disabled_by_non_positive_grace(db_session: Session) -> None:
    store = _FakeStore([SecretInfo("conn-dead-dev-abc123", OLD)])
    result = sweep_orphan_secrets(db_session, store=store, grace_days=0, purge=True, now=NOW)
    assert result == secret_sweep_service.OrphanSweepResult(0, [], [], [], [])
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
