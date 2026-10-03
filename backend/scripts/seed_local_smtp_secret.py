"""Provision the bundled mail catcher's SMTP password into the SecretStore (#1150).

Run as a one-shot compose service *before* the api starts, so the local/eval stack
boots straight into email-OTP sign-in with nothing for the operator to configure:

    python -m backend.scripts.seed_local_smtp_secret

## Why this exists at all

`OtpMailer` always performs a real SMTP `AUTH` exchange, and it resolves the
password **from the SecretStore by name** (`AUTH_EMAIL_PASSWORD_SECRET_NAME`) —
there is no "no password" path, deliberately, because a sign-in mailer that
silently skips authentication is a sign-in mailer nobody has verified. The
bundled catcher (Mailpit, `MP_SMTP_AUTH_ACCEPT_ANY`) accepts *any* credentials, so
the value is irrelevant to it — but a value has to EXIST, or every sign-in returns
503 `otp_email_not_configured`. The compose stack's vault is OpenBao in dev mode
(in-memory), so the value cannot simply be written once at install time: it has to
be re-provisioned on every `up`. Hence a service, not a `setup.sh` step.

The password is **generated here and never leaves the vault** — nothing prints it,
no tracked file carries it (CLAUDE.md §11), and the api reads it back through the
same `SecretStore` seam every other credential uses.

## Never overwrites

If the name already holds a value, this exits 0 without touching it. That matters
because `AUTH_EMAIL_PASSWORD_SECRET_NAME` may legitimately point at a REAL relay's
password (an operator who pointed the compose stack at their own SMTP server):
clobbering it on every `docker compose up` would break their sign-in mailer, and
"seed a local test value" is never worth that risk. Absent-only writes also make
re-runs idempotent, which is what the compose gate needs.

Exits non-zero on a store failure — the api service gates on
`service_completed_successfully`, so a vault that cannot be written stops the stack
with this script's error instead of surfacing three steps later as a 503 the first
time somebody tries to sign in.

## A real relay

Point the mailer at your own relay and hand its password over once, in the one-shot's
environment, as `DATAQ_SMTP_PASSWORD`: it is written to the vault under the configured name
(replacing what is there) and never reaches the api's environment. With no password supplied
and none stored, a random one is generated **only for the bundled catcher**; for any other
host this says what is missing instead of storing a password the relay would reject.
`DATAQ_ALERT_SMTP_PASSWORD` does the same for the alert mailer's
`EMAIL_PASSWORD_SECRET_NAME`.
"""

from __future__ import annotations

import os
import secrets
import sys

from backend.app.core.config import get_settings
from backend.app.core.secrets import (
    SecretNotFoundError,
    SecretStore,
    SecretStoreUnavailableError,
    SecretWriteError,
    get_secret_store,
)

#: Bytes of entropy for the generated password. Generous because nothing has to
#: type it — it goes vault → api process → SMTP AUTH and nowhere else.
_PASSWORD_BYTES = 32

#: Hosts that are the bundled mail catcher, which accepts any password.
_CATCHER_HOSTS = frozenset({"mailpit", "localhost", "127.0.0.1"})


def _store_supplied(store: SecretStore, name: str, supplied: str, what: str) -> int:
    try:
        store.set(name, supplied)
    except (SecretWriteError, SecretStoreUnavailableError) as exc:
        print(f"could not write the {what} SMTP password: {exc}", file=sys.stderr)
        return 1
    print(f"the supplied {what} SMTP password is stored.")
    return 0


def _signin(store: SecretStore) -> int:
    settings = get_settings()
    name = (settings.auth_email_password_secret_name or "").strip()
    if not name:
        # The OTP block is off (dev-bypass or OIDC stack). Not an error: this
        # service runs unconditionally so the same compose file serves both modes.
        print("AUTH_EMAIL_PASSWORD_SECRET_NAME is unset — email OTP sign-in is off; nothing to do.")
        return 0
    supplied = os.environ.get("DATAQ_SMTP_PASSWORD", "")
    if supplied:
        return _store_supplied(store, name, supplied, "sign-in")

    # NOTE: the lines below name the ENV VAR, never `name` itself. The value is
    # only a lookup key, not a credential — but echoing your secret keyspace into
    # container logs buys nothing, and a value that flows out of
    # `auth_email_password_secret_name` is `py/clear-text-logging-sensitive-data`
    # by taint. The store's own log lines still carry the key where a diagnosis needs it.
    try:
        store.get(name)
    except SecretNotFoundError:
        pass
    except SecretStoreUnavailableError as exc:
        # An outage must never be reportable as "not set" (ADR 0039 decision 6) —
        # writing on top of an unreadable store could clobber a real credential.
        print(f"secret store unavailable while reading the SMTP secret: {exc}", file=sys.stderr)
        return 1
    else:
        print(
            "the secret named by AUTH_EMAIL_PASSWORD_SECRET_NAME is already set "
            "— left untouched."
        )
        return 0

    host = (settings.auth_email_smtp_host or "").strip().lower()
    if host not in _CATCHER_HOSTS:
        # A made-up password would be rejected by a real relay, and would then look "set".
        print(
            "AUTH_EMAIL_SMTP_HOST is not the bundled mail catcher and no SMTP password is "
            "stored: sign-in mail cannot be sent. Start once with DATAQ_SMTP_PASSWORD set to "
            "the relay's password.",
            file=sys.stderr,
        )
        return 0
    try:
        store.set(name, secrets.token_urlsafe(_PASSWORD_BYTES))
    except (SecretWriteError, SecretStoreUnavailableError) as exc:
        print(f"could not write the SMTP secret: {exc}", file=sys.stderr)
        return 1
    print("SMTP secret provisioned for the bundled mail catcher.")
    return 0


def _alerts(store: SecretStore) -> int:
    name = (get_settings().email_password_secret_name or "").strip()
    supplied = os.environ.get("DATAQ_ALERT_SMTP_PASSWORD", "")
    if not supplied:
        return 0
    if not name:
        print(
            "DATAQ_ALERT_SMTP_PASSWORD is set but EMAIL_PASSWORD_SECRET_NAME is not, so there is "
            "nowhere to store it (set EMAIL_USERNAME to turn alert email on).",
            file=sys.stderr,
        )
        return 1
    return _store_supplied(store, name, supplied, "alert")


def main() -> int:
    store = get_secret_store()
    return _signin(store) or _alerts(store)


if __name__ == "__main__":
    raise SystemExit(main())
