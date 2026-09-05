import asyncio
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Final

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastmcp.utilities.lifespan import combine_lifespans
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.api.v1 import admin as admin_router
from backend.app.api.v1 import admin_members as admin_members_router
from backend.app.api.v1 import admin_privacy as admin_privacy_router
from backend.app.api.v1 import admin_suites as admin_suites_router
from backend.app.api.v1 import api_keys as api_keys_router
from backend.app.api.v1 import assets as assets_router
from backend.app.api.v1 import auth_otp as auth_otp_router
from backend.app.api.v1 import checks as checks_router
from backend.app.api.v1 import connections as connections_router
from backend.app.api.v1 import dashboard as dashboard_router
from backend.app.api.v1 import incidents as incidents_router
from backend.app.api.v1 import llm as llm_router
from backend.app.api.v1 import me as me_router
from backend.app.api.v1 import notification_channels as notification_channels_router
from backend.app.api.v1 import notifications as notifications_router
from backend.app.api.v1 import orchestration as orchestration_router
from backend.app.api.v1 import probe as probe_router
from backend.app.api.v1 import runs as runs_router
from backend.app.api.v1 import schedules as schedules_router
from backend.app.api.v1 import shares as shares_router
from backend.app.api.v1 import suites as suites_router
from backend.app.api.v1 import trigger_bindings as trigger_bindings_router
from backend.app.api.v1 import users as users_router
from backend.app.api.v1._base import TOTAL_COUNT_HEADER
from backend.app.core.auth import init_auth
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import error_envelope, register_exception_handlers
from backend.app.core.logging import configure_logging, get_logger, request_id_var
from backend.app.core.rate_limit import rate_limit_middleware
from backend.app.core.tracing import (
    configure_tracing,
    instrument_celery,
    instrument_fastapi,
    tag_request_id,
)
from backend.app.db.session import get_db
from backend.app.mcp import build_mcp_app

REQUEST_ID_HEADER: Final = "X-Request-ID"
# Validate caller-supplied X-Request-ID before echoing it (security audit 2026-05-28): cap length,
# restrict charset so log lines and response headers can't be polluted with arbitrary content.
_REQUEST_ID_RE: Final = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_log = get_logger(__name__)


def _poll_staleness_tick() -> str:
    """One synchronous staleness check with its own short-lived session (#1052)."""
    from backend.app.db.session import get_session
    from backend.app.services.workspace_health_service import run_poll_staleness_check

    session = get_session()
    try:
        return run_poll_staleness_check(session)
    finally:
        session.close()


async def _poll_staleness_loop(stop: asyncio.Event, interval_s: float) -> None:
    """The API-side poll-staleness watchdog loop (#1052)."""
    logger = get_logger(__name__)
    while not stop.is_set():
        try:
            outcome = await asyncio.to_thread(_poll_staleness_tick)
            logger.debug("poll_staleness_tick", outcome=outcome)
        except Exception:
            # When every alert channel fails, the composite already logged this exact traceback once
            # per channel, including this one (the last).
            logger.exception("poll_staleness_tick_failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            continue


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging(service_name="dataq-api")
    logger = get_logger(__name__)
    settings = get_settings()
    logger.info(
        "app_startup",
        environment=settings.environment,
        log_level=settings.log_level,
        app_insights_enabled=bool(settings.applicationinsights_connection_string),
    )
    await init_auth()
    # Workspace poll-staleness watchdog (#1052).
    staleness_stop = asyncio.Event()
    staleness_task: asyncio.Task[None] | None = None
    if settings.poll_staleness_alert_after_s > 0:
        interval = max(60.0, settings.poll_staleness_alert_after_s / 3)
        staleness_task = asyncio.create_task(_poll_staleness_loop(staleness_stop, interval))
    yield
    if staleness_task is not None:
        staleness_stop.set()
        await staleness_task
    logger.info("app_shutdown")


# The FastMCP server (Week 7) mounts at /mcp as an ASGI sub-app.
_mcp_app = build_mcp_app()
_lifespan = combine_lifespans(lifespan, _mcp_app.lifespan) if _mcp_app is not None else lifespan


def docs_kwargs(settings: Settings) -> dict[str, str | None]:
    """FastAPI doc-exposure kwargs, gated by environment (#170 — prod-docs gate)."""
    enabled = settings.environment != "prod"
    return {
        "docs_url": "/docs" if enabled else None,
        "redoc_url": "/redoc" if enabled else None,
        "openapi_url": "/openapi.json" if enabled else None,
    }


_docs = docs_kwargs(get_settings())
app = FastAPI(
    title="DataQ API",
    lifespan=_lifespan,
    docs_url=_docs["docs_url"],
    redoc_url=_docs["redoc_url"],
    openapi_url=_docs["openapi_url"],
)

# Rate limiting (#725, ADR 0035).
app.middleware("http")(rate_limit_middleware)

# Cross-origin access for the prod Static-Web-App ↔ Container-Apps split.
_cors_origins = get_settings().cors_allow_origin_list
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # X-Total-Count (#925, spread to /pipeline_runs, /incidents, /runs by #1108): every paged
        # list endpoint's total, over its CORS-headers allowlist.
        expose_headers=[REQUEST_ID_HEADER, TOTAL_COUNT_HEADER],
    )


@app.middleware("http")
async def reject_nul_in_url_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """NUL (``\\x00``) in the URL — 422, same contract as `ApiModel` (#567)."""
    for raw in (request.scope.get("raw_path", b""), request.scope.get("query_string", b"")):
        if b"%00" in raw or b"\x00" in raw:
            return JSONResponse(
                status_code=422,
                content=error_envelope(
                    "validation_error", "NUL (\\x00) characters are not allowed in the URL"
                ),
            )
    return await call_next(request)


@app.middleware("http")
async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    incoming = request.headers.get(REQUEST_ID_HEADER)
    rid = incoming if incoming and _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
    token = request_id_var.set(rid)
    # Join key between the request's span and its structlog lines (A3).
    tag_request_id(rid)
    # Path only — never request.url (it carries the query string, e.g. the ADF
    # webhook ?token=<secret>, ADR 0006 / #494). client host kept for audit.
    client = request.client.host if request.client else None
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        _log.exception(
            "request_failed",
            method=request.method,
            path=request.url.path,
            client=client,
            duration_ms=elapsed_ms,
        )
        request_id_var.reset(token)
        raise
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    _log.info(
        "request",
        method=request.method,
        path=request.url.path,
        client=client,
        status=response.status_code,
        duration_ms=elapsed_ms,
    )
    request_id_var.reset(token)
    response.headers[REQUEST_ID_HEADER] = rid
    return response


register_exception_handlers(app)


app.include_router(me_router.router, prefix="/api/v1")
app.include_router(api_keys_router.router, prefix="/api/v1")
app.include_router(auth_otp_router.router, prefix="/api/v1")
app.include_router(users_router.router, prefix="/api/v1")
app.include_router(probe_router.router, prefix="/api/v1")
app.include_router(connections_router.router, prefix="/api/v1")
app.include_router(suites_router.router, prefix="/api/v1")
app.include_router(checks_router.router, prefix="/api/v1")
app.include_router(notifications_router.router, prefix="/api/v1")
app.include_router(notification_channels_router.router, prefix="/api/v1")
app.include_router(runs_router.router, prefix="/api/v1")
app.include_router(dashboard_router.router, prefix="/api/v1")
app.include_router(schedules_router.router, prefix="/api/v1")
app.include_router(shares_router.router, prefix="/api/v1")
app.include_router(orchestration_router.router, prefix="/api/v1")
app.include_router(trigger_bindings_router.router, prefix="/api/v1")
app.include_router(admin_router.router, prefix="/api/v1")
app.include_router(admin_suites_router.router, prefix="/api/v1")
app.include_router(admin_members_router.router, prefix="/api/v1")
app.include_router(admin_privacy_router.router, prefix="/api/v1")
app.include_router(assets_router.router, prefix="/api/v1")
app.include_router(incidents_router.router, prefix="/api/v1")
app.include_router(llm_router.router, prefix="/api/v1")


#: Cap for the readiness DB probe. Short by design: this answers "can we serve
#: right now", and a slow answer is already a no.
_READYZ_TIMEOUT_MS = 2_000


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: is the process up. Deliberately does NOT touch the database."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz(db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    """Readiness: can this instance actually serve — i.e. can it READ the database."""
    try:
        db.execute(text(f"SET LOCAL statement_timeout = {_READYZ_TIMEOUT_MS}"))
        db.execute(text("SELECT 1"))
    except Exception:
        # No exception text: a DSN can carry a password, and this response is
        # unauthenticated. The reason belongs in the logs, not the body.
        _log.warning("readyz_db_unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from None
    return {"status": "ok"}


# Mount the FastMCP server last so its routes don't shadow the versioned API.
# Path "/" on the sub-app since we mount it under /mcp (fastmcp docs).
if _mcp_app is not None:
    app.mount("/mcp", _mcp_app)


# Spans (A3): no-op unless APPLICATIONINSIGHTS_CONNECTION_STRING is set.
configure_tracing(service_name="dataq-api")
instrument_fastapi(app)
instrument_celery()
