from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.core.logging import get_logger

logger = get_logger(__name__)


class ErrorBody(BaseModel):
    code: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody


class DataQError(Exception):
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.detail: dict[str, Any] = detail or {}


class SafeMonitorError(Exception):
    """Marker: ``str(exc)`` is DataQ-authored and safe to persist verbatim (#900).

    A failure message can end up in ``results.observed_value``, in a run's
    ``failure_reason``, and in a dry-run's 502 detail — all sinks the
    logger-level scrubber never sees (CLAUDE.md §10 protects logs, not DB
    columns or API bodies). Raw driver/SDK text must never reach them: an Azure
    storage exception embeds the full SAS-signed URL (#828), and a SQLAlchemy
    error echoes the statement and every bound value (#1203). So by default
    everything is routed through `failure_classifier.classify_failure_reason`,
    which reads the text only to pick a category and returns a constant.

    But classifying *everything* would be its own bug: it would replace
    "unknown freshness column 'nope'" — which we wrote, which names the user's
    actual mistake, and which contains nothing sensitive — with a generic "the
    run failed to execute", making a config typo undiagnosable from the UI.

    So safety is **declared, not guessed**. Subclass this **only** when every
    message the exception can carry is built by DataQ from the user's own
    configuration (a column name, a numeric range, a monitor kind, a cap) or
    from static text. If it can ever interpolate a driver message, a URL, or a
    connection string, leave it unmarked and let it be classified.

    **No exceptions — the rule holds as written (#989).**
    `monitors._as_aware_datetime` used to truncate and echo the offending *cell
    value*, which is target data, not configuration, and the rule was stated
    more strictly than it was enforced. It no longer does: the value rides on
    ``MonitorConfigError.unparsed_value`` and reaches the user through the read
    layer under the suite's column policy, so the diagnostic survives without
    the message carrying data. A message that needs to show a cell is a message
    that needs a structured field instead.

    Lives in `core.errors` rather than beside its first user (#595): the
    contract is an error-*policy* one, and `services.failure_classifier` — which
    `datasources.monitors` imports — is the single reader that decides whether a
    message is echoed or classified. Anchoring the marker here is what lets that
    one policy serve the monitor loop, the run path and the dry-run preview
    without an import cycle or a third near-copy of the same isinstance branch.
    """


def error_envelope(code: str, message: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    return ErrorResponse(
        error=ErrorBody(code=code, message=message, detail=detail or {})
    ).model_dump()


async def _dataq_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DataQError)
    logger.warning("dataq_error", code=exc.code, message=exc.message, status=exc.status_code)
    return JSONResponse(
        status_code=exc.status_code,
        content=error_envelope(exc.code, exc.message, exc.detail),
    )


async def _http_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    return JSONResponse(
        status_code=exc.status_code,
        content=error_envelope("http_error", str(exc.detail)),
    )


def _jsonable(value: Any) -> Any:
    """Coerce a Pydantic error structure to JSON-safe types. `exc.errors()` is
    not JSON-clean: `ctx` can carry live exception objects (e.g. the ValueError
    a model_validator raised) and `input` echoes the raw payload, which can hold
    non-JSON scalars (#371). Stringify anything that isn't plainly serializable."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    return str(value)


async def _validation_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=error_envelope(
            "validation_error", "Request validation failed", {"errors": _jsonable(exc.errors())}
        ),
    )


async def _unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception", error_type=type(exc).__name__)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_envelope("internal_error", "Internal server error"),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DataQError, _dataq_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
