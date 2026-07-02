import logging
import re
import sys
import threading
from contextvars import ContextVar
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from backend.app.core.config import get_settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_PII_KEYS: frozenset[str] = frozenset(
    {
        # Credentials
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "auth",
        "access_key",
        "private_key",
        # Personal contact
        "email",
        "phone",
        "ssn",
        "credit_card",
        "card_number",
        # Azure AD claims (per 2026-05-28 security audit) — AAD object IDs
        # and identifiers are GDPR-grade personal data under Article 4(1).
        "oid",
        "aad_oid",
        "aad_object_id",
        "upn",
        "preferred_username",
        "user_id",
        "name",
        "display_name",
    }
)
_REDACTED = "<redacted>"

# Secret-bearing query params / `key=value` pairs embedded in *string* values
# (the key-based redaction below only catches dict KEYS). The prime case is the
# ADF webhook URL `…/events/adf?token=<secret>` (ADR 0006) surfacing inside a log
# message string — e.g. an access line or an error that interpolated the URL —
# where it would otherwise slip past the key redactor (#494).
_SECRET_QS_RE = re.compile(
    r"(?i)\b(token|sig|signature|secret|api[_-]?key|access[_-]?key|password)=[^&\s\"']+"
)
# URL-userinfo credentials (`scheme://user:secret@host`, e.g. a SQLAlchemy engine
# URL `databricks://token:<PAT>@host/…`) — a different shape than the query-param
# scrub above, missed by it until #536.
_URL_USERINFO_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@\"']+):[^@/\s\"']+@")


def _scrub_secret_strings(text: str) -> str:
    text = _SECRET_QS_RE.sub(lambda m: f"{m.group(1)}={_REDACTED}", text)
    return _URL_USERINFO_RE.sub(lambda m: f"{m.group(1)}:{_REDACTED}@", text)


def _redact_pii(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: (_REDACTED if k.lower() in _PII_KEYS else walk(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, str):
            return _scrub_secret_strings(value)
        return value

    result: EventDict = walk(event_dict)
    return result


def _add_request_id(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    rid = request_id_var.get()
    if rid is not None:
        event_dict["request_id"] = rid
    return event_dict


# Traceback → dict WITHOUT frame locals (#536): `dict_tracebacks`' default
# transformer captures every frame's locals, which can carry anything in scope —
# connection URLs with embedded credentials (the live-smoke leak: a SQLAlchemy
# `…://token:<PAT>@host` engine URL), sample rows, PII — and are unredactable in
# general. Frame files/lines/names remain; locals are debugging sugar we forgo.
_dict_tracebacks_no_locals = structlog.processors.ExceptionRenderer(
    structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
)


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_request_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_pii,
    ]

    # Bridge stdlib logging (uvicorn.access, uvicorn.error, etc.) through the
    # same processor chain so every line out of the app is JSON with a
    # request_id when available. Without this the uvicorn access log emits
    # human-readable text that App Insights can't correlate (#50).
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _dict_tracebacks_no_locals,
            # Re-run the redactor AFTER the traceback is rendered to a dict —
            # the pre-chain pass ran before the exception existed as strings, so
            # exception messages/frames never met the scrubber (#536). Idempotent
            # on everything else.
            _redact_pii,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Detach uvicorn's pre-configured handlers; let logs propagate to root so
    # they hit the structlog ProcessorFormatter above.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True

    # uvicorn.access is SILENCED, not propagated: its access line includes the raw
    # query string (get_path_with_query_string), so it would log the ADF webhook
    # `?token=<secret>` (ADR 0006) to stdout AND straight to App Insights (#494).
    # The request middleware (main.py) emits a structured, path-only access log
    # (method/path/status/duration/client/request_id) for every request that reaches
    # the app — so app-level access logging is unaffected; only server-layer-only
    # lines (e.g. malformed requests rejected before ASGI dispatch) go unlogged, an
    # accepted tradeoff for not leaking the secret.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers = []
    access_logger.propagate = False

    conn = settings.applicationinsights_connection_string
    if conn:
        from opencensus.ext.azure.log_exporter import AzureLogHandler

        ai_handler = AzureLogHandler(connection_string=conn)

        # opencensus-ext-azure (unmaintained, not tested on Python 3.13 — ADR 0017)
        # overrides createLock() to set `self.lock = None` (it does its own
        # queue-based thread-safety). That was fine on older Python, but 3.13's
        # logging.Handler.handle() does `with self.lock` with no None-check, so the
        # first emitted record crashes app startup. Assigning the lock once isn't
        # enough: Celery's embedded beat (`worker -B`) re-initialises logging in
        # its forked process and calls createLock() AGAIN, re-nulling the lock and
        # killing beat on its first log line — so every periodic task (orchestration
        # polling, scheduled dispatch, gap recovery) silently stops (#405, a #393
        # recurrence). Replace createLock with an idempotent version that only ever
        # CREATES a missing lock and never nulls or swaps an existing one — so no
        # caller can re-null it, and a live lock is preserved rather than replaced
        # (avoids losing mutual exclusion if a re-init ever raced an in-flight
        # emit). (#393 — proper fix: migrate to azure-monitor-opentelemetry;
        # opencensus is EOL.)
        def _ensure_handler_lock() -> None:
            if getattr(ai_handler, "lock", None) is None:
                ai_handler.lock = threading.RLock()

        ai_handler.createLock = _ensure_handler_lock
        ai_handler.createLock()
        ai_handler.setLevel(level)
        # Use the SAME ProcessorFormatter as stdout so records sent to App Insights
        # also pass through `_redact_pii` (incl. the secret-string scrubber). Without
        # a formatter, AzureLogHandler ships the raw record message, so a foreign
        # (non-structlog) record carrying a secret in its message would reach App
        # Insights un-redacted (#494). App-level structlog records are already
        # redacted in the wrapper chain; this closes the foreign-record path too.
        ai_handler.setFormatter(formatter)
        root.addHandler(ai_handler)

    structlog.configure(
        processors=[
            *shared_processors,
            _dict_tracebacks_no_locals,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
