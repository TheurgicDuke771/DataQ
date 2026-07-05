import logging
import re
import sys
from contextvars import ContextVar
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from backend.app.core import otel
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
        "passphrase",
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


def _configure_otel_log_export(
    root: logging.Logger,
    level: int,
    formatter: logging.Formatter,
    service_name: str,
) -> None:
    """Bridge stdlib logging → OpenTelemetry → the configured exporter(s) (#524).

    Replaces the EOL opencensus ``AzureLogHandler`` (and its Py3.13 ``createLock``
    hardening, #393/#405). A **no-op** when no exporter is configured, matching the
    old connection-string gate. **Fork-safe**: the SDK's ``BatchLogRecordProcessor``
    re-inits its export thread in forked children (celery prefork) via
    ``os.register_at_fork``, so attaching here — exactly where the old handler
    attached — is correct in the worker as well as the API.

    The lazy imports (repo convention, ``secrets.py``) keep telemetry-off
    deployments from paying the OTel-logs import cost.
    """
    exporters = otel.build_log_exporters(settings=get_settings())
    if not exporters:
        return

    import warnings

    from opentelemetry._logs import set_logger_provider
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

    class _RedactingOTelLogHandler(LoggingHandler):  # type: ignore[misc]  # LoggingHandler is Any (follow_imports=skip)
        """OTel bridge that redacts the EXPORTED ATTRIBUTES, not just the body.

        The body is already scrubbed — ``LoggingHandler._translate`` renders it
        through our redacting ``ProcessorFormatter`` (``if self.formatter:``). But
        the base ``_get_attributes`` copies every non-reserved log-record var into
        the exported OTel attributes verbatim, *bypassing the formatter* — so a
        foreign record's ``extra=`` (or a library's custom record attribute) could
        ship a secret / PII to the backend un-redacted. Run those attributes
        through the same PII/secret scrubber the formatter applies to the body
        (#494/#536). This is stricter than the old opencensus handler, which only
        exported the formatted message."""

        @staticmethod
        def _get_attributes(record: logging.LogRecord) -> Any:
            return _redact_pii(None, "", dict(LoggingHandler._get_attributes(record)))

    provider = LoggerProvider(resource=otel.build_resource(service_name))
    for exporter in exporters:
        provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
    set_logger_provider(provider)

    with warnings.catch_warnings():
        # The sdk `LoggingHandler` carries a DeprecationWarning nudging toward
        # `opentelemetry-instrumentation-logging` — which only injects trace
        # context into records; it is NOT an export bridge. The sdk handler IS
        # the bridge (it's what the azure-monitor distro uses under the hood).
        # Suppress the nudge so it neither spams stdout nor trips `-W error`.
        warnings.simplefilter("ignore", DeprecationWarning)
        handler = _RedactingOTelLogHandler(level=level, logger_provider=provider)
    # Same redacting ProcessorFormatter as stdout, so records exported to the
    # backend pass through `_redact_pii` (incl. the secret-string scrubber) — a
    # foreign record carrying a secret in its message is scrubbed before export
    # (#494). App-level structlog records are already redacted upstream.
    handler.setFormatter(formatter)
    root.addHandler(handler)


def configure_logging(service_name: str = "dataq") -> None:
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

    # Export logs to the configured backend(s) via OpenTelemetry (#524, replacing
    # the EOL opencensus AzureLogHandler). No-op when telemetry is off.
    _configure_otel_log_export(root, level, formatter, service_name)

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
