"""Workspace-admin endpoint tests against a real Postgres via TestClient."""

import smtplib
import ssl
import time
import uuid
from collections.abc import Iterator
from email.message import EmailMessage
from typing import Any, ClassVar

import pytest
import structlog
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from backend.app.core.auth import DEV_BYPASS_EMAIL
from backend.app.core.config import Settings, get_settings
from backend.app.db.models import ORCHESTRATION_PROVIDERS, Check, Connection, Share, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import admin_service, otp_service
from backend.tests.support.fake_secret_store import FakeSecretStore, override_secret_store


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _grant_admin(monkeypatch: pytest.MonkeyPatch, *emails: str) -> None:
    """Make the given emails (default: the dev-bypass caller) workspace admins."""
    monkeypatch.setenv("WORKSPACE_ADMIN_EMAILS", ",".join(emails or (DEV_BYPASS_EMAIL,)))
    get_settings.cache_clear()


def _user(db_session: Any, email: str, display_name: str | None = None) -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=email, display_name=display_name)
    db_session.add(u)
    db_session.flush()
    return u


def _connection(db_session: Any, owner: User) -> Connection:
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ab12345.eu-west-1"},
        secret_ref="kv-sf",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    return conn


def _suite(db_session: Any, owner: User, conn: Connection, name: str) -> Suite:
    suite = Suite(name=name, connection_id=conn.id, created_by=owner.id)
    db_session.add(suite)
    db_session.flush()
    return suite


# ── authz gate ────────────────────────────────────────────────────────────────


def test_non_admin_gets_403(client: TestClient, as_role: Any) -> None:
    # A genuine MEMBER, not the ambient dev-bypass identity — which is itself a workspace admin
    # since #741 (dev bypass is a single-operator mode).
    get_settings.cache_clear()
    _, headers = as_role("member")
    for path in (
        "/api/v1/admin/suites",
        "/api/v1/admin/users",
        "/api/v1/admin/access",
        "/api/v1/admin/orchestration/webhooks",
    ):
        resp = client.get(path, headers=headers)
        assert resp.status_code == 403, path
    resp = client.post("/api/v1/admin/auth-email/test", headers=headers)
    assert resp.status_code == 403


def test_admin_email_match_is_case_insensitive(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _grant_admin(monkeypatch, DEV_BYPASS_EMAIL.upper())
    resp = client.get("/api/v1/admin/suites")
    assert resp.status_code == 200


# ── all suites ────────────────────────────────────────────────────────────────


def test_admin_lists_suites_it_does_not_own(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A suite owned by someone else, with two checks and one share — the admin
    # neither owns nor is shared on it, yet must see it (scoping is bypassed).
    other = _user(db_session, "owner@x.io", "Olive Owner")
    conn = _connection(db_session, other)
    suite = _suite(db_session, other, conn, "Finance DQ")
    db_session.add_all(
        [
            Check(
                suite_id=suite.id,
                name="c1",
                expectation_type="expect_column_values_to_not_be_null",
                config={"column": "id"},
            ),
            Check(
                suite_id=suite.id,
                name="c2",
                expectation_type="expect_column_values_to_not_be_null",
                config={"column": "amt"},
            ),
        ]
    )
    viewer = _user(db_session, "viewer@x.io")
    db_session.add(Share(suite_id=suite.id, user_id=viewer.id, permission="view"))
    db_session.commit()

    _grant_admin(monkeypatch)
    resp = client.get("/api/v1/admin/suites")
    assert resp.status_code == 200
    [row] = [r for r in resp.json() if r["id"] == str(suite.id)]
    assert row["name"] == "Finance DQ"
    assert row["owner_email"] == "owner@x.io"
    assert row["owner_name"] == "Olive Owner"
    assert row["connection_type"] == "snowflake"
    assert row["env"] == "dev"
    assert row["check_count"] == 2
    assert row["share_count"] == 1


# ── all users ─────────────────────────────────────────────────────────────────


def test_admin_lists_users_with_counts(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _user(db_session, "alice@x.io", "Alice")
    conn = _connection(db_session, owner)
    _suite(db_session, owner, conn, "S1")
    _suite(db_session, owner, conn, "S2")
    bob = _user(db_session, "bob@x.io")
    s3 = _suite(db_session, owner, conn, "S3")
    db_session.add(Share(suite_id=s3.id, user_id=bob.id, permission="edit"))
    db_session.commit()

    _grant_admin(monkeypatch)
    rows = {r["email"]: r for r in client.get("/api/v1/admin/users").json()}
    assert rows["alice@x.io"]["owned_suite_count"] == 3
    assert rows["alice@x.io"]["shared_suite_count"] == 0
    assert rows["bob@x.io"]["owned_suite_count"] == 0
    assert rows["bob@x.io"]["shared_suite_count"] == 1


# ── access overview ───────────────────────────────────────────────────────────


def test_admin_access_overview_lists_owner_and_shares(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _user(db_session, "owner@x.io")
    conn = _connection(db_session, owner)
    suite = _suite(db_session, owner, conn, "Shared Suite")
    editor = _user(db_session, "editor@x.io")
    db_session.add(Share(suite_id=suite.id, user_id=editor.id, permission="edit"))
    db_session.commit()

    _grant_admin(monkeypatch)
    rows = [r for r in client.get("/api/v1/admin/access").json() if r["suite_id"] == str(suite.id)]
    grants = {(r["user_email"], r["permission"]) for r in rows}
    assert ("owner@x.io", "owner") in grants
    assert ("editor@x.io", "edit") in grants


# ── inbound webhook config (#490) ─────────────────────────────────────────────── These tests are
# read-only by design (webhook URL construction never writes a secret).


def _orch_connection(db_session: Any, owner: User, *, ctype: str, name: str) -> Connection:
    configs: dict[str, dict[str, Any]] = {
        "adf": {"factory_name": name},
        "airflow": {"base_url": f"https://{name}.example.com", "auth_type": "token"},
        "dbt": {"project_name": name, "artifacts_uri": f"file:///tmp/{name}", "jobs": ["nightly"]},
    }
    config = configs[ctype]
    conn = Connection(
        name=name, type=ctype, env="dev", config=config, secret_ref="kv", created_by=owner.id
    )
    db_session.add(conn)
    db_session.flush()
    return conn


def _with_store(client: TestClient, store: FakeSecretStore) -> TestClient:
    override_secret_store(app, store)
    return client


def test_admin_webhooks_adf_url_embeds_token(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _user(db_session, "owner@x.io")
    _orch_connection(db_session, owner, ctype="adf", name="prod-factory")
    db_session.commit()
    _grant_admin(monkeypatch)
    _with_store(client, FakeSecretStore(default="secret-tok", raise_on_write=True))

    rows = {r["provider"]: r for r in client.get("/api/v1/admin/orchestration/webhooks").json()}
    adf = rows["adf"]
    assert adf["inbound_url"].endswith("/api/v1/orchestration/events/adf?token=secret-tok")
    assert adf["token_configured"] is True
    assert adf["signing_secret_name"] is None
    assert "prod-factory" in adf["connection_names"]


def test_admin_webhooks_url_encodes_token(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A secret with URL-significant chars must be percent-encoded so the pasted URL
    # decodes back to the exact secret the receiver compares against (ADR 0006).
    owner = _user(db_session, "owner@x.io")
    _orch_connection(db_session, owner, ctype="adf", name="prod-factory")
    db_session.commit()
    _grant_admin(monkeypatch)
    _with_store(client, FakeSecretStore(default="a+b&c=d", raise_on_write=True))

    [adf] = [
        r
        for r in client.get("/api/v1/admin/orchestration/webhooks").json()
        if r["provider"] == "adf"
    ]
    assert adf["inbound_url"].endswith("?token=a%2Bb%26c%3Dd")


def test_admin_webhooks_airflow_carries_no_url_token(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _user(db_session, "owner@x.io")
    _orch_connection(db_session, owner, ctype="airflow", name="airflow-prod")
    db_session.commit()
    _grant_admin(monkeypatch)
    _with_store(client, FakeSecretStore(default="wh-tok-123", raise_on_write=True))

    rows = {r["provider"]: r for r in client.get("/api/v1/admin/orchestration/webhooks").json()}
    airflow = rows["airflow"]
    assert airflow["inbound_url"].endswith("/api/v1/orchestration/events/airflow")
    assert "token=" not in airflow["inbound_url"]
    assert airflow["signing_secret_name"] == "airflow-webhook-secret"
    assert airflow["token_configured"] is True  # signing key provisioned in the store


def test_admin_webhooks_dbt_row_is_not_mislabeled_as_airflow(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # #647: the two-provider if/else dropped dbt connections into the airflow
    # branch — wrong provider label, wrong inbound endpoint, wrong signing key.
    owner = _user(db_session, "owner@x.io")
    _orch_connection(db_session, owner, ctype="dbt", name="analytics-dbt")
    db_session.commit()
    _grant_admin(monkeypatch)
    _with_store(client, FakeSecretStore(default="wh-tok-123", raise_on_write=True))

    rows = {r["provider"]: r for r in client.get("/api/v1/admin/orchestration/webhooks").json()}
    assert set(rows) == {"dbt"}
    dbt = rows["dbt"]
    assert dbt["inbound_url"].endswith("/api/v1/orchestration/events/dbt")
    assert "token=" not in dbt["inbound_url"]
    assert dbt["signing_secret_name"] == "dbt-webhook-secret"
    assert "HMAC-SHA256" in dbt["auth"]
    assert "analytics-dbt" in dbt["connection_names"]


@pytest.mark.parametrize("ctype", ORCHESTRATION_PROVIDERS)
def test_admin_webhooks_every_provider_yields_its_own_row(
    ctype: str, client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Guards the next provider addition: a connection of each registered provider must surface as a
    # row of the SAME provider with its own events endpoint.
    owner = _user(db_session, "owner@x.io")
    _orch_connection(db_session, owner, ctype=ctype, name=f"{ctype}-conn")
    db_session.commit()
    _grant_admin(monkeypatch)
    _with_store(client, FakeSecretStore(default="wh-tok-123", raise_on_write=True))

    rows = client.get("/api/v1/admin/orchestration/webhooks").json()
    assert [r["provider"] for r in rows] == [ctype]
    assert f"/api/v1/orchestration/events/{ctype}" in rows[0]["inbound_url"]


@pytest.mark.parametrize("ctype", ["airflow", "dbt"])
def test_admin_webhooks_hmac_rows_mark_missing_signing_secret(
    ctype: str, client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # token_configured must reflect the signing key's actual presence in the
    # store — a hardcoded True hid the misconfiguration until callbacks 401'd.
    owner = _user(db_session, "owner@x.io")
    _orch_connection(db_session, owner, ctype=ctype, name=f"{ctype}-conn")
    db_session.commit()
    _grant_admin(monkeypatch)
    _with_store(client, FakeSecretStore(raise_on_write=True))  # signing key not provisioned

    [row] = client.get("/api/v1/admin/orchestration/webhooks").json()
    assert row["token_configured"] is False


def test_admin_webhooks_marks_missing_secret(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _user(db_session, "owner@x.io")
    _orch_connection(db_session, owner, ctype="adf", name="prod-factory")
    db_session.commit()
    _grant_admin(monkeypatch)
    _with_store(client, FakeSecretStore(raise_on_write=True))  # secret not provisioned

    [adf] = [
        r
        for r in client.get("/api/v1/admin/orchestration/webhooks").json()
        if r["provider"] == "adf"
    ]
    assert adf["token_configured"] is False
    assert "token=secret" not in adf["inbound_url"]  # no real token leaked
    assert "set adf-webhook-secret" in adf["inbound_url"]


def test_admin_webhooks_omits_providers_without_connections(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Only an ADF connection exists → no airflow row.
    owner = _user(db_session, "owner@x.io")
    _orch_connection(db_session, owner, ctype="adf", name="only-adf")
    db_session.commit()
    _grant_admin(monkeypatch)
    _with_store(client, FakeSecretStore(default="wh-tok-123", raise_on_write=True))

    providers = {r["provider"] for r in client.get("/api/v1/admin/orchestration/webhooks").json()}
    assert providers == {"adf"}


# ── SMTP pre-flight test (#737, ADR 0032 decision 7) ───────────────────────── Real endpoint → real
# OtpMailer; only `smtplib.SMTP` is mocked, at the transport boundary.


def _set_auth_email_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_EMAIL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("AUTH_EMAIL_SMTP_PORT", "587")
    monkeypatch.setenv("AUTH_EMAIL_USERNAME", "dataq@example.com")
    monkeypatch.setenv("AUTH_EMAIL_FROM", "DataQ <dataq@example.com>")
    monkeypatch.setenv("AUTH_EMAIL_PASSWORD_SECRET_NAME", "auth-email-password")
    # `_validate_otp_auth` refuses to boot a "touched" AUTH_EMAIL_* block without a signup allowlist
    # (ADR 0032 decision 2) — unrelated to this endpoint.
    monkeypatch.setenv("AUTH_OTP_ALLOWED_DOMAINS", "dataq.local")
    get_settings.cache_clear()


class _RecordingSMTP:
    """Records the submission sequence — the one boundary these tests mock."""

    instances: ClassVar[list["_RecordingSMTP"]] = []

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.calls: list[str] = []
        self.messages: list[EmailMessage] = []
        _RecordingSMTP.instances.append(self)

    def starttls(self, context: ssl.SSLContext | None = None) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append("login")

    def send_message(self, message: EmailMessage) -> None:
        self.calls.append("send")
        self.messages.append(message)

    def quit(self) -> None:
        # `_deliver` closes explicitly via `.quit()` in a `finally`, never via
        # `with`/`__exit__` (#737 review — see `otp_mailer._deliver`'s docstring).
        self.calls.append("close")


@pytest.fixture(autouse=True)
def _reset_recording_smtp() -> Iterator[None]:
    _RecordingSMTP.instances = []
    yield
    _RecordingSMTP.instances = []


@pytest.fixture(autouse=True)
def _preflight_counter() -> Iterator[otp_service.InMemoryOtpCounterStore]:
    """A process-local pre-flight counter for every test in this file (#1147)."""
    store = otp_service.InMemoryOtpCounterStore()
    admin_service.set_preflight_counter_store_for_testing(store)
    yield store
    admin_service.reset_preflight_counter_state()


def test_auth_email_preflight_succeeds_and_emails_the_caller(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_auth_email_env(monkeypatch)
    _grant_admin(monkeypatch)
    monkeypatch.setattr(smtplib, "SMTP", _RecordingSMTP)
    _with_store(client, FakeSecretStore(default="app-password", raise_on_write=True))

    resp = client.post("/api/v1/admin/auth-email/test")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "to": DEV_BYPASS_EMAIL}

    smtp = _RecordingSMTP.instances[-1]
    assert smtp.calls == ["starttls", "login", "send", "close"]
    assert smtp.messages[0]["To"] == DEV_BYPASS_EMAIL


def test_auth_email_preflight_not_configured_returns_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AUTH_EMAIL_* left unset (the conftest default) — the mailer's own
    # defence-in-depth check fires before smtplib is ever touched.
    _grant_admin(monkeypatch)
    monkeypatch.setattr(smtplib, "SMTP", _RecordingSMTP)
    _with_store(client, FakeSecretStore(default="app-password", raise_on_write=True))

    resp = client.post("/api/v1/admin/auth-email/test")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "otp_email_not_configured"
    assert _RecordingSMTP.instances == []


class _ConnectFailSMTP(_RecordingSMTP):
    """Bad host / DNS failure / refused connection — smtplib.SMTP.__init__ itself
    connects and raises before there is a context-managed object.
    """

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        raise OSError("[Errno -2] Name or service not known")


class _ConnectTimeoutSMTP(_RecordingSMTP):
    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        raise TimeoutError("timed out")


class _TlsFailSMTP(_RecordingSMTP):
    def starttls(self, context: ssl.SSLContext | None = None) -> None:
        raise ssl.SSLError("handshake failed")


class _AuthFailSMTP(_RecordingSMTP):
    def login(self, user: str, password: str) -> None:
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")


@pytest.mark.parametrize(
    ("fake_cls", "expected_stage"),
    [
        (_ConnectFailSMTP, "connect"),  # bad host
        (_ConnectTimeoutSMTP, "connect"),  # timeout
        (_TlsFailSMTP, "tls"),  # TLS failure
        (_AuthFailSMTP, "auth"),  # refused auth
    ],
)
def test_auth_email_preflight_reports_the_failing_stage(
    fake_cls: type[_RecordingSMTP],
    expected_stage: str,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_auth_email_env(monkeypatch)
    _grant_admin(monkeypatch)
    monkeypatch.setattr(smtplib, "SMTP", fake_cls)
    _with_store(client, FakeSecretStore(default="app-password", raise_on_write=True))

    resp = client.post("/api/v1/admin/auth-email/test")
    assert resp.status_code == 502
    error = resp.json()["error"]
    assert error["code"] == "otp_email_preflight_failed"
    assert error["detail"]["stage"] == expected_stage
    # The password must never reach the caller, even on the error path.
    assert "app-password" not in resp.text


# ── SMTP pre-flight throttle (#1147) ───────────────────────────────────────── Every call to this
# endpoint is a real connection to the operator's mail relay.


class _DownCounterStore:
    """Redis unreachable — the store's fail-open signal is a `None` return."""

    def incr_window(self, key: str, ttl_seconds: int) -> int | None:
        return None


def _wire_working_preflight(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_auth_email_env(monkeypatch)
    _grant_admin(monkeypatch)
    monkeypatch.setattr(smtplib, "SMTP", _RecordingSMTP)
    _with_store(client, FakeSecretStore(default="app-password", raise_on_write=True))


def test_auth_email_preflight_429s_past_the_per_admin_cap(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap itself. A real 429 with the standard envelope — there is no
    anti-enumeration reason to soften it, unlike `otp/request`.
    """
    _wire_working_preflight(client, monkeypatch)
    monkeypatch.setenv("ADMIN_EMAIL_PREFLIGHT_PER_10MIN", "2")
    get_settings.cache_clear()

    first = client.post("/api/v1/admin/auth-email/test")
    second = client.post("/api/v1/admin/auth-email/test")
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    blocked = client.post("/api/v1/admin/auth-email/test")
    assert blocked.status_code == 429, blocked.text
    error = blocked.json()["error"]
    assert error["code"] == "preflight_rate_limited"
    assert error["detail"]["retry_after_seconds"] >= 1
    # The whole point: the relay was never dialled for the blocked call.
    assert len(_RecordingSMTP.instances) == 2


def test_a_failed_send_still_spends_a_preflight_slot(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Counted before the send, deliberately: what is bounded is connections opened
    at the relay, and a relay already refusing us is exactly when a retry loop does
    the most damage. Counting only successes would leave the abusive case uncapped.
    """
    _set_auth_email_env(monkeypatch)
    _grant_admin(monkeypatch)
    monkeypatch.setattr(smtplib, "SMTP", _ConnectFailSMTP)
    _with_store(client, FakeSecretStore(default="app-password", raise_on_write=True))
    monkeypatch.setenv("ADMIN_EMAIL_PREFLIGHT_PER_10MIN", "1")
    get_settings.cache_clear()

    failed = client.post("/api/v1/admin/auth-email/test")
    assert failed.status_code == 502, failed.text
    blocked = client.post("/api/v1/admin/auth-email/test")

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "preflight_rate_limited"


def test_a_preflight_and_a_signin_never_touch_each_others_counters(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, _preflight_counter: Any
) -> None:
    """Shared mechanism, separate budgets — asserted by driving BOTH paths."""
    _wire_working_preflight(client, monkeypatch)
    monkeypatch.setenv("ADMIN_EMAIL_PREFLIGHT_PER_10MIN", "5")
    # The #1137 floor sleeps a real second on every uniform request response.
    monkeypatch.setenv("AUTH_OTP_REQUEST_MIN_SECONDS", "0")
    get_settings.cache_clear()
    signin_counter = otp_service.InMemoryOtpCounterStore()
    otp_service.set_counter_store_for_testing(signin_counter)

    preflight = client.post("/api/v1/admin/auth-email/test")
    assert preflight.status_code == 200, preflight.text
    # `_set_auth_email_env` allow-lists dataq.local, so this address is eligible and
    # actually reaches the per-email counter rather than short-circuiting.
    signin = client.post("/api/v1/auth/otp/request", json={"email": "someone@dataq.local"})
    assert signin.status_code == 200, signin.text

    preflight_keys = list(_preflight_counter._counts)
    signin_keys = list(signin_counter._counts)
    assert preflight_keys, "the throttle did not count the pre-flight"
    assert signin_keys, "the sign-in did not reach its own counter"
    assert all(k.startswith("preflight:") for k in preflight_keys), preflight_keys
    assert all(k.startswith("otp:req:") for k in signin_keys), signin_keys
    # Neither budget saw the other's traffic at all — the property, stated directly.
    assert not set(preflight_keys) & set(signin_keys)
    # …and no address appears in either key, only digests.
    assert not any(DEV_BYPASS_EMAIL in k for k in preflight_keys), preflight_keys
    assert not any("someone@dataq.local" in k for k in signin_keys), signin_keys


def test_the_preflight_throttle_uses_its_OWN_store_instance(
    _preflight_counter: Any,
) -> None:
    """Same class, separate instance — and the instance is what carries the circuit breaker."""
    signin_store = otp_service.InMemoryOtpCounterStore()
    otp_service.set_counter_store_for_testing(signin_store)

    assert admin_service.get_preflight_counter_store() is _preflight_counter
    assert admin_service.get_preflight_counter_store() is not signin_store
    assert otp_service.get_counter_store() is signin_store


def test_the_preflight_throttle_fails_OPEN_when_the_counter_store_is_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0035's bias, inherited: availability over enforcement. A Redis outage must
    not take away the operator's only mail-configuration diagnostic at the moment
    they are most likely to need it.
    """
    _wire_working_preflight(client, monkeypatch)
    monkeypatch.setenv("ADMIN_EMAIL_PREFLIGHT_PER_10MIN", "1")
    get_settings.cache_clear()
    admin_service.set_preflight_counter_store_for_testing(_DownCounterStore())

    statuses = [client.post("/api/v1/admin/auth-email/test").status_code for _ in range(4)]
    assert statuses == [200, 200, 200, 200]
    assert len(_RecordingSMTP.instances) == 4


def test_the_fail_open_warning_is_logged_ONCE_not_per_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-open means the cap is enforcing nothing — so the very scenario this throttle exists for
    (a scripted token in a loop) is also the one where an unsuppressed warning becomes a log
    line PER REQUEST.
    """
    settings = Settings(admin_email_preflight_per_10min=1)
    admin_service.set_preflight_counter_store_for_testing(_DownCounterStore())

    with capture_logs() as logs:
        monkeypatch.setattr(
            admin_service, "log", structlog.get_logger("backend.app.services.admin_service")
        )
        for _ in range(5):
            admin_service.enforce_preflight_quota(uuid.uuid4(), settings)

    events = [e["event"] for e in logs]
    assert events.count("admin_preflight_counter_store_unavailable") == 1, events


def test_the_preflight_cap_can_be_switched_off(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, _preflight_counter: Any
) -> None:
    """0 disables it — and must not merely raise the ceiling: nothing is counted at
    all, so the documented cost (falling back to the generic 300/min class) is what
    an operator actually gets.
    """
    _wire_working_preflight(client, monkeypatch)
    monkeypatch.setenv("ADMIN_EMAIL_PREFLIGHT_PER_10MIN", "0")
    get_settings.cache_clear()

    statuses = [client.post("/api/v1/admin/auth-email/test").status_code for _ in range(6)]
    assert statuses == [200] * 6
    assert _preflight_counter._counts == {}


def test_two_admins_do_not_share_one_preflight_budget(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keyed per admin, so one admin's diagnostics cannot lock another's out. The
    key is a digest of the user id, and different ids must land in different keys.
    """
    now = 1_000_000.0
    a, b = uuid.uuid4(), uuid.uuid4()

    assert admin_service._preflight_key(a, now=now) != admin_service._preflight_key(b, now=now)
    # Same admin, same window → the same bucket (otherwise nothing is ever capped).
    assert admin_service._preflight_key(a, now=now) == admin_service._preflight_key(a, now=now + 1)
    # …and the next window is a different bucket, which is what makes the cap reset.
    assert admin_service._preflight_key(a, now=now) != admin_service._preflight_key(
        a, now=now + admin_service.PREFLIGHT_WINDOW_SECONDS
    )


def test_the_preflight_cap_resets_in_the_NEXT_window(
    monkeypatch: pytest.MonkeyPatch, _preflight_counter: Any
) -> None:
    """A cap that never resets is an outage, not a throttle."""
    settings = Settings(admin_email_preflight_per_10min=1)
    admin = uuid.uuid4()
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(time, "time", lambda: clock["now"])

    admin_service.enforce_preflight_quota(admin, settings)  # spends the only slot
    with pytest.raises(admin_service.PreflightThrottledError) as exhausted:
        admin_service.enforce_preflight_quota(admin, settings)
    # The wait it advertises must land inside the window it is waiting out.
    retry_after = exhausted.value.detail["retry_after_seconds"]
    assert 1 <= retry_after <= admin_service.PREFLIGHT_WINDOW_SECONDS

    clock["now"] += admin_service.PREFLIGHT_WINDOW_SECONDS
    admin_service.enforce_preflight_quota(admin, settings)  # a fresh budget, no raise


def test_the_shipped_preflight_cap_default_is_three_per_ten_minutes() -> None:
    """The value that protects a deployment is the DEFAULT — every test above overrides it."""
    assert Settings().admin_email_preflight_per_10min == 3
    assert admin_service.PREFLIGHT_WINDOW_SECONDS == 600
