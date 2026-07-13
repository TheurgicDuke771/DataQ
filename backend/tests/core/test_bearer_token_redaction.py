"""A bearer credential must never reach the logs — not even via a dependency (#849).

Found in production. App Insights held, in plaintext:

    DecodeError: Malformed token received. dq_live_<the caller's actual PAT>. Error: …

`fastapi_azure_auth` logs the raw token when a JWT decode fails
(``log.warning('Malformed token received. %s. …', access_token, …)``), and a DataQ PAT is
not a JWT — so **every PAT-authenticated request** shipped a live admin bearer credential
into telemetry, where anyone with read access to App Insights could lift it and
authenticate as its owner.

Two defences, both tested here:

1. **The PAT never reaches the JWT validator** (`_PatAwareAzureScheme`) — the log line is
   not produced in the first place.
2. **The logger-level redactor scrubs bare tokens anyway** — because we do not control
   what a third-party library logs, and the next one to echo a credential will not
   announce itself either. This is CLAUDE.md §10's rule ("PII redaction at the logger
   level, not at every call site") applied to the case that actually bit us: the call site
   *wasn't ours*.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from backend.app.core.logging import _scrub_secret_strings
from backend.app.services import api_key_service

# The SHAPE of the credential that leaked — never the value. The real token is revoked,
# but a revoked credential is still a credential: CLAUDE.md forbids one in any git-tracked
# file, and a scanner cannot tell "expired" from "live" (nor should it have to).
_PAT = "dq_live_" + "0" * 40
_JWT = "eyJ" + "A" * 12 + "." + "B" * 12 + "." + "C" * 12


def test_the_prefix_the_redactor_matches_is_the_prefix_we_actually_issue() -> None:
    """Drift guard. `core.logging` cannot import `api_key_service` (import cycle), so the
    `dq_live_` prefix is duplicated in the regex. If the issued prefix is ever renamed,
    this fails loudly instead of the redactor silently ceasing to match real tokens."""
    assert api_key_service.TOKEN_PREFIX == "dq_live_"


class TestTheLoggerLevelBackstop:
    """What a *dependency* logs is not under our control — so the scrubbing is."""

    def test_the_real_library_message_no_longer_carries_the_token(self) -> None:
        # Verbatim the message `fastapi_azure_auth.auth` emits (auth.py:171).
        message = f"Malformed token received. {_PAT}. Error: Not enough segments"
        scrubbed = _scrub_secret_strings(message)

        assert _PAT not in scrubbed
        assert "dq_live_" not in scrubbed
        # Still diagnosable — the operator needs to know a malformed token arrived.
        assert "Malformed token received" in scrubbed
        assert "Not enough segments" in scrubbed

    def test_an_azure_access_token_is_scrubbed_too(self) -> None:
        """The same library line fires for a genuinely malformed *AAD* token, which is
        also a live credential."""
        scrubbed = _scrub_secret_strings(f"Malformed token received. {_JWT}. Error: x")
        assert _JWT not in scrubbed
        assert "eyJ" not in scrubbed

    @pytest.mark.parametrize(
        "message",
        [
            f"Authorization: Bearer {_PAT}",
            f"headers={{'authorization': 'Bearer {_PAT}'}}",
            f"request failed for token {_PAT}",
            f"{_PAT}",  # the bare token, no context at all
        ],
    )
    def test_the_token_cannot_survive_in_any_shape(self, message: str) -> None:
        assert _PAT not in _scrub_secret_strings(message)

    def test_ordinary_text_is_left_alone(self) -> None:
        """The scrubber must not maul normal logs — an over-eager regex that redacts
        everything gets disabled, and then nothing is redacted."""
        message = "run 1a2b3c succeeded: 4 checks passed in 2.1s"
        assert _scrub_secret_strings(message) == message

    def test_it_applies_to_a_foreign_stdlib_record(self, caplog: Any) -> None:
        """The leak came through the stdlib logging bridge, not structlog — so assert the
        scrub on the path a third-party library actually takes."""
        logger = logging.getLogger("fastapi_azure_auth")
        with caplog.at_level(logging.WARNING):
            logger.warning("Malformed token received. %s. Error: %s", _PAT, "Not enough segments")
        # The record's *rendered* message is what the formatter (and thus the exporter)
        # ships; that is what must be clean.
        rendered = _scrub_secret_strings(caplog.records[0].getMessage())
        assert _PAT not in rendered


class TestThePatNeverReachesTheJwtValidator:
    """Defence 1: remove the cause, not just the symptom.

    The assertions here intercept the **library's own** `__call__` — the thing that logs
    the token. A test that merely checked our subclass returns `None` would pass on the
    bug too, since the buggy version also returned `None` (after logging the credential).
    What must be proven is that the validator is *never entered* for a PAT.
    """

    @pytest.mark.asyncio
    async def test_a_pat_never_enters_the_jwt_validator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.security import SecurityScopes
        from fastapi_azure_auth import SingleTenantAzureAuthorizationCodeBearer

        from backend.app.core.auth import _PatAwareAzureScheme

        seen: list[str] = []

        async def _spy(self: Any, request: Any, security_scopes: Any) -> None:
            # This is where `log.warning('Malformed token received. %s' …)` lives.
            seen.append(request.headers["Authorization"])
            return None

        monkeypatch.setattr(SingleTenantAzureAuthorizationCodeBearer, "__call__", _spy)
        scheme = _PatAwareAzureScheme.__new__(_PatAwareAzureScheme)  # skip network-y __init__

        class _Req:
            def __init__(self, token: str) -> None:
                self.headers = {"Authorization": f"Bearer {token}"}

        # A PAT: short-circuited — the validator (and its logging) is never reached.
        assert await scheme(_Req(_PAT), SecurityScopes()) is None  # type: ignore[arg-type]  # duck-typed request
        assert seen == [], "the PAT was handed to the JWT validator — it will be logged"

        # An AAD token: still validated exactly as before (we broke nothing).
        await scheme(_Req(_JWT), SecurityScopes())  # type: ignore[arg-type]  # duck-typed request
        assert seen == [f"Bearer {_JWT}"]
