import logging
import sys
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


def _redact_pii(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: (_REDACTED if k.lower() in _PII_KEYS else walk(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value

    result: EventDict = walk(event_dict)
    return result


def _add_request_id(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    rid = request_id_var.get()
    if rid is not None:
        event_dict["request_id"] = rid
    return event_dict


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
            structlog.processors.dict_tracebacks,
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
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True

    conn = settings.applicationinsights_connection_string
    if conn:
        from opencensus.ext.azure.log_exporter import AzureLogHandler

        ai_handler = AzureLogHandler(connection_string=conn)
        ai_handler.setLevel(level)
        root.addHandler(ai_handler)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.dict_tracebacks,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
