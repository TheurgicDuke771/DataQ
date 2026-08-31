"""Tests for channel_service — reusable notification channels (#1514)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import insert

from backend.app.core.secrets import SecretNotFoundError
from backend.app.db.models import (
    Connection,
    NotificationChannel,
    Suite,
    SuiteNotificationChannel,
    User,
)
from backend.app.services import channel_service as svc
from backend.app.services.channel_service import (
    ChannelFieldMismatchError,
    ChannelInUseError,
    ChannelNotFoundError,
    ChannelTypeInvalidError,
)
from backend.app.services.notification_service import InvalidRecipientsError, InvalidWebhookError
from backend.tests.support.fake_secret_store import FakeSecretStore

_TEAMS_URL = "https://contoso.webhook.office.com/x"
_SLACK_URL = "https://hooks.slack.com/services/T00/B00/xyz"


def _user(db: Any) -> User:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@x.io")
    db.add(user)
    db.flush()
    return user


def _suite(db: Any) -> Suite:
    owner = _user(db)
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


def test_create_channel_mints_and_stores_a_secret_ref(db_session: Any) -> None:
    store = FakeSecretStore()
    channel, _ = svc.create_channel(
        db_session, name="Platform Teams", type="teams", webhook=_TEAMS_URL, secret_store=store
    )
    assert channel.webhook_secret_ref is not None
    assert store.get(channel.webhook_secret_ref) == _TEAMS_URL


def test_create_email_channel_stores_recipients_inline_not_as_a_secret(db_session: Any) -> None:
    channel, _ = svc.create_channel(
        db_session,
        name="Data team",
        type="email",
        email_recipients="a@x.io,b@x.io",
        secret_store=FakeSecretStore(),
    )
    assert channel.email_recipients == "a@x.io,b@x.io"
    assert channel.webhook_secret_ref is None


def test_create_channel_rejects_an_invalid_type(db_session: Any) -> None:
    with pytest.raises(ChannelTypeInvalidError):
        svc.create_channel(db_session, name="x", type="pagerduty", secret_store=FakeSecretStore())


def test_create_email_channel_rejects_a_webhook_field(db_session: Any) -> None:
    """A field that doesn't apply to the type is a 422, not a silent no-op —
    without this, POST {type: email, webhook: ...} returns 201 with has_webhook:
    false and the caller has no idea their webhook was ignored.
    """
    with pytest.raises(ChannelFieldMismatchError):
        svc.create_channel(
            db_session, name="x", type="email", webhook=_TEAMS_URL, secret_store=FakeSecretStore()
        )


def test_create_teams_channel_rejects_an_email_recipients_field(db_session: Any) -> None:
    with pytest.raises(ChannelFieldMismatchError):
        svc.create_channel(
            db_session,
            name="x",
            type="teams",
            email_recipients="a@x.io",
            secret_store=FakeSecretStore(),
        )


def test_update_channel_rejects_a_mismatched_field(db_session: Any) -> None:
    store = FakeSecretStore()
    channel, _ = svc.create_channel(
        db_session, name="x", type="email", email_recipients="a@x.io", secret_store=store
    )
    with pytest.raises(ChannelFieldMismatchError):
        svc.update_channel(db_session, channel.id, webhook=_TEAMS_URL, secret_store=store)


def test_create_teams_channel_rejects_a_non_allowlisted_webhook(db_session: Any) -> None:
    with pytest.raises(InvalidWebhookError):
        svc.create_channel(
            db_session,
            name="x",
            type="teams",
            webhook="https://evil.example.com/hook",
            secret_store=FakeSecretStore(),
        )


def test_create_email_channel_rejects_malformed_recipients(db_session: Any) -> None:
    with pytest.raises(InvalidRecipientsError):
        svc.create_channel(
            db_session,
            name="x",
            type="email",
            email_recipients="not-an-email",
            secret_store=FakeSecretStore(),
        )


def test_get_channel_not_found(db_session: Any) -> None:
    with pytest.raises(ChannelNotFoundError):
        svc.get_channel(db_session, uuid.uuid4())


def test_list_channels_sorted_by_name(db_session: Any) -> None:
    store = FakeSecretStore()
    svc.create_channel(
        db_session, name="Zeta", type="email", email_recipients="a@x.io", secret_store=store
    )
    svc.create_channel(
        db_session, name="Alpha", type="email", email_recipients="a@x.io", secret_store=store
    )
    names = [c.name for c in svc.list_channels(db_session)]
    assert names == ["Alpha", "Zeta"]


def test_update_channel_rotates_the_webhook_value_in_place(db_session: Any) -> None:
    """Rotation is the acceptance test (#1514): the ref itself stays stable — only
    the value at that ref changes — which is exactly why every suite resolving
    this same ref picks up the new webhook with no per-suite edit at all.
    """
    store = FakeSecretStore()
    channel, _ = svc.create_channel(
        db_session, name="x", type="teams", webhook=_TEAMS_URL, secret_store=store
    )
    ref = channel.webhook_secret_ref
    assert ref is not None

    new_url = "https://contoso.webhook.office.com/rotated"
    updated, _ = svc.update_channel(
        db_session, channel.id, webhook=new_url, secret_store=store, actor_id=None
    )
    assert updated.webhook_secret_ref == ref
    assert store.get(ref) == new_url


def test_update_channel_webhook_none_leaves_it_unchanged(db_session: Any) -> None:
    store = FakeSecretStore()
    channel, _ = svc.create_channel(
        db_session, name="x", type="teams", webhook=_TEAMS_URL, secret_store=store
    )
    ref_before = channel.webhook_secret_ref
    svc.update_channel(db_session, channel.id, name="renamed", secret_store=store)
    db_session.refresh(channel)
    assert channel.webhook_secret_ref == ref_before
    assert channel.name == "renamed"


def test_update_channel_webhook_empty_string_clears_it(db_session: Any) -> None:
    store = FakeSecretStore()
    channel, _ = svc.create_channel(
        db_session, name="x", type="teams", webhook=_TEAMS_URL, secret_store=store
    )
    ref = channel.webhook_secret_ref
    assert ref is not None
    svc.update_channel(db_session, channel.id, webhook="", secret_store=store)
    db_session.refresh(channel)
    assert channel.webhook_secret_ref is None
    with pytest.raises(SecretNotFoundError):
        store.get(ref)


def test_delete_channel_removes_its_secret(db_session: Any) -> None:
    store = FakeSecretStore()
    channel, _ = svc.create_channel(
        db_session, name="x", type="teams", webhook=_TEAMS_URL, secret_store=store
    )
    ref = channel.webhook_secret_ref
    assert ref is not None
    svc.delete_channel(db_session, channel.id, secret_store=store)
    assert db_session.get(NotificationChannel, channel.id) is None
    with pytest.raises(SecretNotFoundError):
        store.get(ref)


def test_delete_channel_refuses_while_linked(db_session: Any) -> None:
    store = FakeSecretStore()
    channel, _ = svc.create_channel(
        db_session, name="x", type="teams", webhook=_TEAMS_URL, secret_store=store
    )
    suite = _suite(db_session)
    svc.link_suite(db_session, suite.id, channel.id)
    with pytest.raises(ChannelInUseError) as exc:
        svc.delete_channel(db_session, channel.id, secret_store=store)
    assert exc.value.detail["total"] == 1
    assert exc.value.detail["suites"][0]["id"] == str(suite.id)
    # The channel must still exist — a refused delete is not a partial delete.
    assert db_session.get(NotificationChannel, channel.id) is not None


def test_link_suite_is_idempotent(db_session: Any) -> None:
    store = FakeSecretStore()
    channel, _ = svc.create_channel(
        db_session, name="x", type="teams", webhook=_TEAMS_URL, secret_store=store
    )
    suite = _suite(db_session)
    svc.link_suite(db_session, suite.id, channel.id)
    svc.link_suite(db_session, suite.id, channel.id)  # second call is a no-op, not a conflict
    assert [c.id for c in svc.list_channels_for_suite(db_session, suite.id)] == [channel.id]


def test_link_suite_to_a_missing_channel_404s(db_session: Any) -> None:
    suite = _suite(db_session)
    with pytest.raises(ChannelNotFoundError):
        svc.link_suite(db_session, suite.id, uuid.uuid4())


def test_link_suite_recovers_from_a_concurrent_link_race(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two overlapping link requests for the same (suite, channel) pair both pass
    the existing-is-None check before either commits; the loser's insert must hit
    the composite-PK conflict and come back as the idempotent no-op the caller
    asked for, not an unhandled IntegrityError — same race class notification_
    service.upsert_config was already fixed for (#384).
    """
    store = FakeSecretStore()
    channel, _ = svc.create_channel(
        db_session, name="x", type="teams", webhook=_TEAMS_URL, secret_store=store
    )
    suite = _suite(db_session)
    # The "concurrent winner" row, inserted via Core so it never touches the ORM
    # identity map — session.get() below must go all the way to a real DB read
    # (which the monkeypatch then forces to lie once), and link_suite's own
    # session.add()+flush() must reach a genuine Postgres constraint violation
    # rather than an ORM-side identity conflict on a duplicate Python object.
    db_session.execute(
        insert(SuiteNotificationChannel).values(suite_id=suite.id, channel_id=channel.id)
    )

    # link_suite calls session.get() twice for two DIFFERENT models
    # (get_channel's existence check, then its own existing-link check) — the
    # lie must target only the SECOND kind's first call, not just "call #1
    # overall", or it swallows the unrelated get_channel() lookup instead.
    real_get = db_session.get
    lied_once = {"done": False}

    def stale_then_real(model: Any, pk: Any, *a: Any, **kw: Any) -> Any:
        if model is SuiteNotificationChannel and not lied_once["done"]:
            lied_once["done"] = True
            return None  # the stale read that drove our own insert attempt
        return real_get(model, pk, *a, **kw)

    monkeypatch.setattr(db_session, "get", stale_then_real)

    svc.link_suite(db_session, suite.id, channel.id)  # must not raise
    assert [c.id for c in svc.list_channels_for_suite(db_session, suite.id)] == [channel.id]


def test_delete_channel_recovers_from_a_concurrent_link_race(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The linked-suites pre-check runs clean (no links yet); a link then commits
    before the delete itself does. The RESTRICT FK must turn that into the same
    clean 409 the pre-check gives for the non-concurrent case, not an unhandled
    IntegrityError.
    """
    store = FakeSecretStore()
    channel, _ = svc.create_channel(
        db_session, name="x", type="teams", webhook=_TEAMS_URL, secret_store=store
    )
    suite = _suite(db_session)
    real_detail = svc._linked_suites_detail
    lied_once = {"done": False}

    def stale_then_real(session: Any, channel_id: Any) -> Any:
        if not lied_once["done"]:
            lied_once["done"] = True
            return None  # the pre-check's stale "no links" read
        return real_detail(session, channel_id)

    monkeypatch.setattr(svc, "_linked_suites_detail", stale_then_real)

    # The "concurrent winner": committed after the (mocked) stale pre-check ran.
    db_session.execute(
        insert(SuiteNotificationChannel).values(suite_id=suite.id, channel_id=channel.id)
    )

    with pytest.raises(ChannelInUseError) as exc:
        svc.delete_channel(db_session, channel.id, secret_store=store)
    assert exc.value.detail["total"] == 1
    # Refused, not partially applied — the channel and its link both survive.
    assert db_session.get(NotificationChannel, channel.id) is not None
    assert [c.id for c in svc.list_channels_for_suite(db_session, suite.id)] == [channel.id]


def test_unlink_suite_removes_the_link_and_unblocks_delete(db_session: Any) -> None:
    store = FakeSecretStore()
    channel, _ = svc.create_channel(
        db_session, name="x", type="teams", webhook=_TEAMS_URL, secret_store=store
    )
    suite = _suite(db_session)
    svc.link_suite(db_session, suite.id, channel.id)
    assert svc.unlink_suite(db_session, suite.id, channel.id) is True
    assert svc.list_channels_for_suite(db_session, suite.id) == []
    svc.delete_channel(db_session, channel.id, secret_store=store)  # no longer refused


def test_unlink_suite_not_linked_returns_false(db_session: Any) -> None:
    store = FakeSecretStore()
    channel, _ = svc.create_channel(
        db_session, name="x", type="teams", webhook=_TEAMS_URL, secret_store=store
    )
    suite = _suite(db_session)
    assert svc.unlink_suite(db_session, suite.id, channel.id) is False


def test_resolve_channel_webhooks_dedupes_across_channels(db_session: Any) -> None:
    """Two channels that happen to point at the same webhook resolve to one URL —
    the alerting layer's own dedup against the legacy field relies on this.
    """
    store = FakeSecretStore()
    a, _ = svc.create_channel(
        db_session, name="A", type="teams", webhook=_TEAMS_URL, secret_store=store
    )
    b, _ = svc.create_channel(
        db_session, name="B", type="teams", webhook=_TEAMS_URL, secret_store=store
    )
    suite = _suite(db_session)
    svc.link_suite(db_session, suite.id, a.id)
    svc.link_suite(db_session, suite.id, b.id)
    urls = svc.resolve_channel_webhooks(
        db_session, suite.id, channel_type="teams", secret_store=store
    )
    assert urls == [_TEAMS_URL]


def test_resolve_channel_webhooks_filters_by_type(db_session: Any) -> None:
    store = FakeSecretStore()
    teams, _ = svc.create_channel(
        db_session, name="T", type="teams", webhook=_TEAMS_URL, secret_store=store
    )
    slack, _ = svc.create_channel(
        db_session, name="S", type="slack", webhook=_SLACK_URL, secret_store=store
    )
    suite = _suite(db_session)
    svc.link_suite(db_session, suite.id, teams.id)
    svc.link_suite(db_session, suite.id, slack.id)
    assert svc.resolve_channel_webhooks(
        db_session, suite.id, channel_type="teams", secret_store=store
    ) == [_TEAMS_URL]
    assert svc.resolve_channel_webhooks(
        db_session, suite.id, channel_type="slack", secret_store=store
    ) == [_SLACK_URL]


def test_resolve_channel_webhooks_skips_a_missing_secret(db_session: Any) -> None:
    """Fail-soft, same posture as the per-suite/workspace resolvers: a channel
    whose secret has gone missing is skipped, not a crash.
    """
    store = FakeSecretStore()
    channel, _ = svc.create_channel(
        db_session, name="x", type="teams", webhook=_TEAMS_URL, secret_store=store
    )
    suite = _suite(db_session)
    svc.link_suite(db_session, suite.id, channel.id)
    assert channel.webhook_secret_ref is not None
    store.delete(channel.webhook_secret_ref)  # simulate the secret vanishing
    assert (
        svc.resolve_channel_webhooks(db_session, suite.id, channel_type="teams", secret_store=store)
        == []
    )


def test_resolve_channel_email_recipients_unions_and_dedupes(db_session: Any) -> None:
    store = FakeSecretStore()
    a, _ = svc.create_channel(
        db_session, name="A", type="email", email_recipients="x@a.io,y@a.io", secret_store=store
    )
    b, _ = svc.create_channel(
        db_session, name="B", type="email", email_recipients="y@a.io,z@a.io", secret_store=store
    )
    suite = _suite(db_session)
    svc.link_suite(db_session, suite.id, a.id)
    svc.link_suite(db_session, suite.id, b.id)
    assert svc.resolve_channel_email_recipients(db_session, suite.id) == (
        "x@a.io",
        "y@a.io",
        "z@a.io",
    )


# ── generic webhook channels (#1662) ─────────────────────────────────────────
# 8.8.8.8 is a stable, unambiguously public IP literal — SSRF-guard-safe and
# DNS-free, matching the notification_service SSRF-guard test convention.
_WEBHOOK_URL = "https://8.8.8.8/hook"
_UNSAFE_URL = "https://127.0.0.1/hook"


def test_create_webhook_channel_mints_and_returns_the_hmac_secret_once(db_session: Any) -> None:
    store = FakeSecretStore()
    channel, secret = svc.create_channel(
        db_session, name="Ops", type="webhook", webhook_url=_WEBHOOK_URL, secret_store=store
    )
    assert channel.webhook_url == _WEBHOOK_URL
    assert channel.hmac_secret_ref is not None
    assert secret is not None
    assert store.get(channel.hmac_secret_ref) == secret


def test_create_webhook_channel_without_a_url_mints_no_secret(db_session: Any) -> None:
    """A channel shell with no URL yet has nothing to sign for — matches the
    teams/slack pattern where a webhook is optional at create time.
    """
    channel, secret = svc.create_channel(
        db_session, name="Ops", type="webhook", secret_store=FakeSecretStore()
    )
    assert channel.webhook_url is None
    assert channel.hmac_secret_ref is None
    assert secret is None


def test_create_webhook_channel_rejects_an_unsafe_url(db_session: Any) -> None:
    with pytest.raises(InvalidWebhookError):
        svc.create_channel(
            db_session,
            name="Ops",
            type="webhook",
            webhook_url=_UNSAFE_URL,
            secret_store=FakeSecretStore(),
        )


def test_create_channel_rejects_a_webhook_url_field_on_the_wrong_type(db_session: Any) -> None:
    with pytest.raises(ChannelFieldMismatchError):
        svc.create_channel(
            db_session,
            name="x",
            type="teams",
            webhook_url=_WEBHOOK_URL,
            secret_store=FakeSecretStore(),
        )


def test_update_webhook_channel_setting_the_url_mints_a_secret_on_first_set(
    db_session: Any,
) -> None:
    store = FakeSecretStore()
    channel, minted = svc.create_channel(db_session, name="Ops", type="webhook", secret_store=store)
    assert minted is None
    channel, secret = svc.update_channel(
        db_session, channel.id, webhook_url=_WEBHOOK_URL, secret_store=store
    )
    assert channel.webhook_url == _WEBHOOK_URL
    assert channel.hmac_secret_ref is not None
    assert secret is not None
    assert store.get(channel.hmac_secret_ref) == secret


def test_update_webhook_channel_regenerate_rotates_the_secret_in_place(db_session: Any) -> None:
    """Same acceptance shape as the webhook-URL rotation test: the ref stays
    stable, only the value at it changes — every suite resolving the channel
    picks up the new key with no per-suite edit.
    """
    store = FakeSecretStore()
    channel, first_secret = svc.create_channel(
        db_session, name="Ops", type="webhook", webhook_url=_WEBHOOK_URL, secret_store=store
    )
    ref = channel.hmac_secret_ref
    assert ref is not None

    channel, second_secret = svc.update_channel(
        db_session, channel.id, regenerate_hmac_secret=True, secret_store=store
    )
    assert channel.hmac_secret_ref == ref
    assert second_secret is not None
    assert second_secret != first_secret
    assert store.get(ref) == second_secret


def test_update_webhook_channel_url_alone_does_not_regenerate_the_secret(db_session: Any) -> None:
    store = FakeSecretStore()
    channel, first_secret = svc.create_channel(
        db_session, name="Ops", type="webhook", webhook_url=_WEBHOOK_URL, secret_store=store
    )
    ref = channel.hmac_secret_ref
    assert ref is not None

    new_url = "https://1.1.1.1/hook"
    channel, secret = svc.update_channel(
        db_session, channel.id, webhook_url=new_url, secret_store=store
    )
    assert channel.webhook_url == new_url
    assert channel.hmac_secret_ref == ref
    assert secret is None  # not re-minted — the existing key still works
    assert store.get(ref) == first_secret


def test_delete_webhook_channel_removes_the_hmac_secret(db_session: Any) -> None:
    store = FakeSecretStore()
    channel, _secret = svc.create_channel(
        db_session, name="Ops", type="webhook", webhook_url=_WEBHOOK_URL, secret_store=store
    )
    ref = channel.hmac_secret_ref
    assert ref is not None
    svc.delete_channel(db_session, channel.id, secret_store=store)
    with pytest.raises(SecretNotFoundError):
        store.get(ref)


def test_resolve_webhook_channels_returns_url_and_secret(db_session: Any) -> None:
    store = FakeSecretStore()
    channel, secret = svc.create_channel(
        db_session, name="Ops", type="webhook", webhook_url=_WEBHOOK_URL, secret_store=store
    )
    suite = _suite(db_session)
    svc.link_suite(db_session, suite.id, channel.id)
    assert svc.resolve_webhook_channels(db_session, suite.id, secret_store=store) == [
        (_WEBHOOK_URL, secret)
    ]


def test_resolve_webhook_channels_skips_an_unconfigured_channel(db_session: Any) -> None:
    """A webhook channel with no URL yet (or no secret yet) is not a
    destination — resolving it must not crash or return a half-formed pair.
    """
    store = FakeSecretStore()
    channel, _secret = svc.create_channel(
        db_session, name="Ops", type="webhook", secret_store=store
    )
    suite = _suite(db_session)
    svc.link_suite(db_session, suite.id, channel.id)
    assert svc.resolve_webhook_channels(db_session, suite.id, secret_store=store) == []


def test_resolve_webhook_channels_skips_a_missing_secret(db_session: Any) -> None:
    store = FakeSecretStore()
    channel, _secret = svc.create_channel(
        db_session, name="Ops", type="webhook", webhook_url=_WEBHOOK_URL, secret_store=store
    )
    suite = _suite(db_session)
    svc.link_suite(db_session, suite.id, channel.id)
    assert channel.hmac_secret_ref is not None
    store.delete(channel.hmac_secret_ref)
    assert svc.resolve_webhook_channels(db_session, suite.id, secret_store=store) == []
