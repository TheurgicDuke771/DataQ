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

import io
import logging
import uuid
from typing import Any

import pytest

from backend.app.core.logging import _scrub_secret_strings, configure_logging
from backend.app.db.models import User
from backend.app.services import api_key_service

# The SHAPE of the credential that leaked — never the value. The real token is revoked,
# but a revoked credential is still a credential: CLAUDE.md forbids one in any git-tracked
# file, and a scanner cannot tell "expired" from "live" (nor should it have to).
_PAT = "dq_live_" + "0" * 40
_JWT = "eyJ" + "A" * 12 + "." + "B" * 12 + "." + "C" * 12


def test_a_REAL_minted_token_is_scrubbed_whole(db_session: Any) -> None:
    """Pin the regex to what we ACTUALLY issue, not to a literal someone remembered to
    update (#849 review).

    Asserting `TOKEN_PREFIX == "dq_live_"` would catch a renamed prefix but NOT a changed
    token *alphabet*: swap `secrets.token_urlsafe` for a generator emitting characters
    outside `[A-Za-z0-9_-]` and the regex would match only a **prefix** of the token,
    leaving the tail in the log — a partial credential — with a literal-comparison test
    still green. So mint one the real way, through `create_key`, and assert nothing of it
    survives.
    """
    user = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@ex.com")
    db_session.add(user)
    db_session.flush()
    _, token = api_key_service.create_key(db_session, user, name="redaction-probe")

    scrubbed = _scrub_secret_strings(f"Malformed token received. {token}. Error: x")

    assert token not in scrubbed
    # …and no fragment of the secret half survives either — a truncated credential is
    # still a disclosure, and is exactly what an alphabet change would produce.
    secret_half = token[len(api_key_service.TOKEN_PREFIX) :]
    assert secret_half not in scrubbed
    assert secret_half[:12] not in scrubbed
    assert "Malformed token received" in scrubbed  # still diagnosable


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


class TestTheRealLoggingPipeline:
    """The layer the credential actually crossed (#849 review).

    Every other test here calls `_scrub_secret_strings` directly — but the leak did not
    happen because that helper was wrong; it happened because the **pipeline** carried a
    message the helper never saw. Drop `_redact_pii` from the `foreign_pre_chain`, stop
    applying the string scrub to the `event` key, or reorder the ProcessorFormatter, and
    every unit test above still passes while the token ships again.

    So assert on the bytes the handler actually writes.
    """

    def test_the_librarys_warning_reaches_the_handler_with_no_token_in_it(
        self, db_session: Any, capsys: Any
    ) -> None:
        user = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@ex.com")
        db_session.add(user)
        db_session.flush()
        _, token = api_key_service.create_key(db_session, user, name="pipeline-probe")

        configure_logging()  # the REAL stack: structlog processors + ProcessorFormatter
        buffer = io.StringIO()
        handler = logging.getLogger().handlers[0]
        original_stream = handler.stream  # type: ignore[attr-defined]  # StreamHandler
        handler.stream = buffer  # type: ignore[attr-defined]  # StreamHandler
        try:
            # Verbatim the call fastapi_azure_auth makes (auth.py:171), exc_info and all —
            # the traceback is rendered into the record too, and must be clean as well.
            try:
                raise ValueError("Not enough segments")
            except ValueError as exc:
                logging.getLogger("fastapi_azure_auth").warning(
                    "Malformed token received. %s. Error: %s", token, exc, exc_info=True
                )
        finally:
            handler.stream = original_stream  # type: ignore[attr-defined]  # StreamHandler

        emitted = buffer.getvalue()
        assert emitted, "nothing was emitted — the assertion below would be vacuous"
        assert token not in emitted
        assert token[len(api_key_service.TOKEN_PREFIX) :][:12] not in emitted
        # The line is still useful to an operator.
        assert "Malformed token received" in emitted
        assert "Not enough segments" in emitted


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


class TestTheExporterDoesNotAmplifyItsOwnLogs:
    """`azure.core`'s HTTP policy logs a request AND a response line for every call the
    SDK makes — including the exporter's own uploads to App Insights. Those records reach
    root, re-enter the OTel bridge, are exported, and generate more uploads: a
    self-sustaining amplifier (#852).

    Measured in prod at ~10 records/second — 19,000 in half an hour — which drowned the
    application's real logs (a poll event became unfindable in the noise) and burned
    ingestion quota. It is silenced at INFO, not detached, so a genuine transport WARNING
    still reaches stdout and the backend.
    """

    def test_the_http_policy_chatter_is_silenced_at_info(self) -> None:
        configure_logging()
        policy_log = logging.getLogger("azure.core.pipeline.policies.http_logging_policy")
        assert not policy_log.isEnabledFor(logging.INFO)
        # …but a real problem still gets through.
        assert policy_log.isEnabledFor(logging.WARNING)

    def test_the_export_bridge_drops_azure_core_records(self) -> None:
        """Level-setting alone did NOT hold in the Celery worker (it works in the API), so
        the loop is broken at the bridge as well: an `azure.core` record must never be
        handed to the exporter, whatever else resets logging levels (#852)."""
        import logging as _logging

        class _Bridge(_logging.Handler):
            def __init__(self) -> None:
                super().__init__()
                self.exported: list[str] = []

            def emit(self, record: _logging.LogRecord) -> None:
                self.exported.append(record.name)

        bridge = _Bridge()
        bridge.addFilter(lambda record: not record.name.startswith("azure.core"))

        for name in (
            "azure.core.pipeline.policies.http_logging_policy",
            "backend.app.worker.tasks",
        ):
            record = _logging.LogRecord(name, _logging.INFO, __file__, 1, "x", None, None)
            if bridge.filter(record):
                bridge.emit(record)

        assert bridge.exported == [
            "backend.app.worker.tasks"
        ], "the exporter's own HTTP chatter reached the export bridge — that is the loop"
