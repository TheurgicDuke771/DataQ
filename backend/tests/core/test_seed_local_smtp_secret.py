"""The bundled mail catcher's SMTP-password seeder (#1150).

`backend/scripts/seed_local_smtp_secret.py` is the compose one-shot that makes
email-OTP sign-in the local stack's default: the OTP mailer resolves its password
from the SecretStore by name and has no "no password" path, so *something* has to
put a value there before the api serves a sign-in. The failure modes worth
covering are all about what it does to values it did NOT create.
"""

from __future__ import annotations

import pytest

from backend.app.core.config import Settings
from backend.app.core.secrets import (
    SecretNotFoundError,
    SecretStoreUnavailableError,
    SecretWriteError,
)
from backend.scripts import seed_local_smtp_secret as script


class _FakeStore:
    """Minimal SecretStore double. `raise_on_get` / `raise_on_set` let a test make
    the store fail the way a sealed or unreachable vault does."""

    def __init__(
        self,
        values: dict[str, str] | None = None,
        raise_on_get: Exception | None = None,
        raise_on_set: Exception | None = None,
    ) -> None:
        self.values = dict(values or {})
        self.raise_on_get = raise_on_get
        self.raise_on_set = raise_on_set
        self.writes: list[str] = []

    def get(self, name: str) -> str:
        if self.raise_on_get is not None:
            raise self.raise_on_get
        try:
            return self.values[name]
        except KeyError as exc:
            raise SecretNotFoundError(name) from exc

    def set(self, name: str, value: str) -> None:
        if self.raise_on_set is not None:
            raise self.raise_on_set
        self.writes.append(value)
        self.values[name] = value

    def delete(self, name: str) -> None:  # pragma: no cover - unused by the script
        self.values.pop(name, None)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    from backend.app.core.config import get_settings

    get_settings.cache_clear()


def _install(
    monkeypatch: pytest.MonkeyPatch, store: _FakeStore, *, secret_name: str | None
) -> None:
    """Wire the script against a real `Settings` — a complete OTP block when a
    secret name is given, an untouched one otherwise. Real Settings rather than a
    stub because a name only ever reaches this script alongside the rest of the
    mailer block (the startup validator refuses every partial state), and a stub
    that could hold a name in isolation would test a configuration that cannot
    exist."""
    if secret_name and secret_name.strip():
        settings = Settings(
            auth_email_smtp_host="mailpit",
            auth_email_username="dataq-local",
            auth_email_from="dataq@dataq.local",
            auth_email_password_secret_name=secret_name,
            auth_otp_allowed_emails="operator@example.com",
        )
    else:
        settings = Settings(auth_email_password_secret_name=secret_name)
    monkeypatch.setattr(script, "get_settings", lambda: settings)
    monkeypatch.setattr(script, "get_secret_store", lambda: store)


def test_provisions_a_password_when_the_name_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _FakeStore()
    _install(monkeypatch, store, secret_name="dataq-local-smtp")
    assert script.main() == 0
    assert len(store.writes) == 1
    assert len(store.writes[0]) >= 32  # token_urlsafe(32), not a placeholder


def test_never_overwrites_an_existing_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """`AUTH_EMAIL_PASSWORD_SECRET_NAME` may point at a REAL relay's password —
    an operator who aimed the compose stack at their own SMTP server. Clobbering
    it on every `docker compose up` would break their sign-in mailer, and seeding
    a throwaway test value is never worth that."""
    store = _FakeStore({"dataq-local-smtp": "the-operators-real-password"})
    _install(monkeypatch, store, secret_name="dataq-local-smtp")
    assert script.main() == 0
    assert store.writes == []
    assert store.values["dataq-local-smtp"] == "the-operators-real-password"


def test_no_ops_when_otp_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The service runs unconditionally so ONE compose file serves both sign-in
    modes; with the OTP block blanked there is simply nothing to provision."""
    store = _FakeStore()
    _install(monkeypatch, store, secret_name=None)
    assert script.main() == 0
    assert store.writes == []


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_whitespace_only_name_is_no_name(monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
    store = _FakeStore()
    _install(monkeypatch, store, secret_name=blank)
    assert script.main() == 0
    assert store.writes == []


def test_an_unreachable_store_fails_LOUDLY_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An outage must never be mistaken for "not set" (ADR 0039 decision 6) —
    writing on top of a store we could not read risks clobbering a live
    credential. Non-zero exit stops the stack here, where the cause is legible,
    rather than three steps later as a 503 at somebody's first sign-in."""
    store = _FakeStore(raise_on_get=SecretStoreUnavailableError("sealed"))
    _install(monkeypatch, store, secret_name="dataq-local-smtp")
    assert script.main() == 1
    assert store.writes == []


def test_a_failed_write_exits_non_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _FakeStore(raise_on_set=SecretWriteError("permission denied"))
    _install(monkeypatch, store, secret_name="dataq-local-smtp")
    assert script.main() == 1


def test_the_generated_password_is_never_printed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """It is a credential like any other: it goes vault → api → SMTP AUTH and is
    never rendered anywhere a log scraper or a terminal scrollback could keep it."""
    store = _FakeStore()
    _install(monkeypatch, store, secret_name="dataq-local-smtp")
    assert script.main() == 0
    output = capsys.readouterr()
    assert store.writes[0] not in output.out
    assert store.writes[0] not in output.err
