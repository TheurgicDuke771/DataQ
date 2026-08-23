import copy
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
        # Iceberg catalog's SECOND credential (#1181) — exact-match key; a bare
        # "secret" entry does not catch "catalog_secret".
        "catalog_secret",
        # Vault/OpenBao (ADR 0039, #1054): an OpenBao token matches none of the
        # bare-token regexes, and key-based redaction can't be fooled by shape.
        "x-vault-token",
        "client_token",
        "secret_id",
        "role_id",
        # Personal contact
        "email",
        "phone",
        "ssn",
        "credit_card",
        "card_number",
        # Azure AD claims — identifiers are GDPR Art 4(1) personal data.
        "oid",
        "aad_oid",
        "aad_object_id",
        "upn",
        "preferred_username",
        # Credential-bearing headers in their HYPHENATED spelling (#849 review) —
        # headers dicts are keyed `x-api-key`, and the key match is exact.
        "api-key",
        "x-api-key",
        "cookie",
        "set-cookie",
        "user_id",
        "name",
        "display_name",
    }
)
_REDACTED = "<redacted>"

# Secret-bearing `key=value` pairs inside STRING values (key redaction only covers
# dict keys) — e.g. the ADF webhook `?token=<secret>` in a message string (#494).
_SECRET_QS_RE = re.compile(
    r"(?i)\b(token|sig|signature|secret|api[_-]?key|access[_-]?key|password)=[^&\s\"']+"
)
# URL-userinfo credentials (`scheme://user:secret@host`, e.g. a SQLAlchemy engine
# URL) — a shape the query-param scrub misses (#536).
_URL_USERINFO_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@\"']+):[^@/\s\"']+@")

# BARE credentials — no `key=` prefix, no URL (#849: fastapi_azure_auth logged the raw PAT of every
# PAT-authenticated request via its own warning line).
_BEARER_TOKEN_RE = re.compile(
    r"(?i)(?:"
    r"dq_live_[A-Za-z0-9_\-]{6,}"  # a DataQ PAT (ADR 0026)
    r"|dq_sess_[A-Za-z0-9_\-]{6,}"  # an OTP session token (ADR 0032)
    r"|eyJ[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]*"  # a JWT (AAD access token)
    # A `Bearer <token>` echo — the value must LOOK like a token (≥16 chars with a digit or symbol):
    # an over-eager redactor hides diagnostics and gets disabled.
    r"|\bbearer\s+(?=[A-Za-z0-9._~+/\-]*[0-9\-._~+/])[A-Za-z0-9._~+/\-]{16,}=*"
    r")"
)


def _scrub_secret_strings(text: str) -> str:
    text = _SECRET_QS_RE.sub(lambda m: f"{m.group(1)}={_REDACTED}", text)
    text = _URL_USERINFO_RE.sub(lambda m: f"{m.group(1)}:{_REDACTED}@", text)
    return _BEARER_TOKEN_RE.sub(_REDACTED, text)


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


def _extract_exc_info_exception(raw: Any) -> BaseException | None:
    """Pull the exception out of an ``exc_info`` value in any of its shapes —
    ``True``, a bare exception, or a ``(type, value, tb)`` tuple. Mirrors structlog's
    private ``processors._figure_out_exc_info`` rather than importing it.
    """
    if isinstance(raw, BaseException):
        return raw
    if isinstance(raw, tuple) and len(raw) == 3 and isinstance(raw[1], BaseException):
        return raw[1]
    if raw:
        # Truthy sentinel = the stdlib `exc_info=True` convention.
        return sys.exc_info()[1]
    return None


def _already_logged_exception(raw_exc_info: Any) -> BaseException | None:
    """Extract the exception from *raw_exc_info* and return it only if
    ``alerting.base.was_already_logged`` marks it as already fully reported —
    the ONE check shared by the structlog processor and the OTel handler, so the
    two can't drift the way #1261 found the per-caller copies had.
    """
    exc = _extract_exc_info_exception(raw_exc_info)
    if exc is None:
        return None

    # Lazy import: core.logging is foundational and must not pay for (or ever
    # cycle with) the higher-level alerting domain import at module load.
    from backend.app.alerting.base import was_already_logged

    if not was_already_logged(exc):
        return None
    return exc


def _record_marks_already_logged_exception(record: logging.LogRecord) -> bool:
    """True if the raw stdlib ``LogRecord`` should be downgraded before OTel export
    (the raw-record counterpart of ``_downgrade_already_logged_exceptions``).
    """
    if isinstance(record.msg, dict):
        return record.msg.get("level") == "warning" and record.levelno >= logging.ERROR
    return _already_logged_exception(record.exc_info) is not None


def _downgrade_already_logged_exceptions(
    _logger: Any, _name: str, event_dict: EventDict
) -> EventDict:
    """Downgrade a record whose exception was already reported with a full traceback
    elsewhere (#1226/#1260/#1261) — in the processor chain, not per caller, same
    shape as PII redaction: a caller that forgets must not reintroduce the bug.
    """
    exc = _already_logged_exception(event_dict.get("exc_info"))
    if exc is None:
        return event_dict

    event_dict["level"] = "warning"
    event_dict.setdefault("error_type", type(exc).__name__)
    event_dict.pop("exc_info", None)
    return event_dict


# Traceback → dict WITHOUT frame locals (#536): the default transformer captures every frame's
# locals — credential-bearing URLs, sample rows, PII — which are unredactable in general.
_dict_tracebacks_no_locals = structlog.processors.ExceptionRenderer(
    structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
)


def _configure_otel_log_export(
    root: logging.Logger,
    level: int,
    formatter: logging.Formatter,
    service_name: str,
) -> None:
    """Bridge stdlib logging → OpenTelemetry → configured exporter(s) (#524). No-op
    when no exporter is configured; fork-safe (BatchLogRecordProcessor re-inits in
    prefork children). Best-effort by design: a telemetry misconfig degrades to
    stdout-only logging, never takes the process down (the #405 blast radius).
    """
    settings = get_settings()
    exporters = otel.build_log_exporters(settings=settings)
    if not exporters:
        return

    try:
        import warnings

        from opentelemetry._logs import set_logger_provider
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

        class _RedactingOTelLogHandler(LoggingHandler):  # type: ignore[misc]  # LoggingHandler is Any (follow_imports=skip)
            """OTel bridge that redacts the EXPORTED ATTRIBUTES, not just the body: the base
            ``_get_attributes`` copies record vars verbatim, bypassing the redacting formatter,
            so a foreign record's ``extra=`` could ship a secret un-redacted (#494/#536).
            """

            @staticmethod
            def _get_attributes(record: logging.LogRecord) -> Any:
                return _redact_pii(None, "", dict(LoggingHandler._get_attributes(record)))

            def emit(self, record: logging.LogRecord) -> None:
                if _record_marks_already_logged_exception(record):
                    # Shallow-copy, don't mutate: every handler on root receives the SAME record
                    # instance.
                    record = copy.copy(record)
                    record.exc_info = None
                    record.exc_text = None
                    record.levelno = logging.WARNING
                    record.levelname = logging.getLevelName(logging.WARNING)
                super().emit(record)

        provider = LoggerProvider(resource=otel.build_resource(service_name))
        for exporter in exporters:
            provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
        set_logger_provider(provider)

        with warnings.catch_warnings():
            # The sdk LoggingHandler's DeprecationWarning points at an instrumentation
            # package that is NOT an export bridge; suppress just that message.
            warnings.filterwarnings(
                "ignore", message=r".*LoggingHandler.*", category=DeprecationWarning
            )
            handler = _RedactingOTelLogHandler(level=level, logger_provider=provider)
        # Same redacting ProcessorFormatter as stdout, so exported records pass
        # through `_redact_pii` (#494).
        handler.setFormatter(formatter)
        # Break the exporter's feedback loop AT THE BRIDGE (#852): azure.core logs every SDK HTTP
        # call — including the exporter's own uploads (~10/sec in prod).
        handler.addFilter(lambda record: not record.name.startswith("azure.core"))
        root.addHandler(handler)

        # Break the feedback loop: the SDK/exporter's own "failed to export" warnings must not re-
        # enter the bridge.
        for noisy in ("opentelemetry", "azure.monitor.opentelemetry"):
            logging.getLogger(noisy).propagate = False

        # Positive confirmation — misconfig otherwise looks identical to
        # telemetry-off. Endpoint is infra config; the connection string is NOT logged.
        logging.getLogger(__name__).info(
            "otel_log_export_configured service=%s exporters=%d azure=%s otlp=%s",
            service_name,
            len(exporters),
            bool(settings.applicationinsights_connection_string),
            settings.otel_exporter_otlp_endpoint or "off",
        )
    except Exception:
        # Degrade to stdout-only rather than take the process down (#405).
        logging.getLogger(__name__).exception("otel_log_export_setup_failed")


def configure_logging(service_name: str = "dataq") -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_request_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_pii,
        # Must run after `add_log_level` (there's a `level` to overwrite) and
        # before `_dict_tracebacks_no_locals` below (exc_info must still be raw).
        _downgrade_already_logged_exceptions,
    ]

    # Bridge stdlib logging (uvicorn.*) through the same chain so every line is
    # JSON with a request_id App Insights can correlate (#50).
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _dict_tracebacks_no_locals,
            # Re-run the redactor AFTER the traceback is rendered — the pre-chain
            # pass ran before exception strings existed (#536). Idempotent otherwise.
            _redact_pii,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # azure.core logs every SDK HTTP call, incl. the exporter's own uploads — a self-sustaining
    # amplifier measured at ~10 records/sec in prod (#852).
    logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)

    # Detach uvicorn's pre-configured handlers; propagate to root's formatter.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True

    # uvicorn.access is SILENCED, not propagated: its line includes the raw query string, i.e. the
    # ADF webhook `?token=<secret>` (#494).
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers = []
    access_logger.propagate = False

    # Export logs via OpenTelemetry (#524); no-op when telemetry is off.
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
