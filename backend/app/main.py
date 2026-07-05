import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Final

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastmcp.utilities.lifespan import combine_lifespans

from backend.app.api.v1 import admin as admin_router
from backend.app.api.v1 import api_keys as api_keys_router
from backend.app.api.v1 import checks as checks_router
from backend.app.api.v1 import connections as connections_router
from backend.app.api.v1 import dashboard as dashboard_router
from backend.app.api.v1 import me as me_router
from backend.app.api.v1 import notifications as notifications_router
from backend.app.api.v1 import orchestration as orchestration_router
from backend.app.api.v1 import probe as probe_router
from backend.app.api.v1 import runs as runs_router
from backend.app.api.v1 import schedules as schedules_router
from backend.app.api.v1 import shares as shares_router
from backend.app.api.v1 import suites as suites_router
from backend.app.api.v1 import trigger_bindings as trigger_bindings_router
from backend.app.api.v1 import users as users_router
from backend.app.core.auth import init_auth
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import error_envelope, register_exception_handlers
from backend.app.core.logging import configure_logging, get_logger, request_id_var
from backend.app.core.tracing import (
    configure_tracing,
    instrument_celery,
    instrument_fastapi,
    tag_request_id,
)
from backend.app.mcp import build_mcp_app

REQUEST_ID_HEADER: Final = "X-Request-ID"
# Validate caller-supplied X-Request-ID before echoing it (security audit
# 2026-05-28): cap length, restrict charset so log lines and response
# headers can't be polluted with arbitrary content.
_REQUEST_ID_RE: Final = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_log = get_logger(__name__)


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
    yield
    logger.info("app_shutdown")


# The FastMCP server (Week 7) mounts at /mcp as an ASGI sub-app. `build_mcp_app`
# returns None when MCP must not be exposed (no resolvable auth — see
# mcp.auth.mcp_enabled), so the endpoint never goes live unauthenticated. Its
# streamable-http session manager needs its lifespan run, so when present we
# combine it with the app's own startup (combine_lifespans, fastmcp docs).
_mcp_app = build_mcp_app()
_lifespan = combine_lifespans(lifespan, _mcp_app.lifespan) if _mcp_app is not None else lifespan


def docs_kwargs(settings: Settings) -> dict[str, str | None]:
    """FastAPI doc-exposure kwargs, gated by environment (#170 — prod-docs gate).

    The interactive docs (`/docs`, `/redoc`) and the raw OpenAPI schema
    (`/openapi.json`) are OFF in production — don't publish the full API surface
    on the public ingress — and ON in dev/staging for developer convenience.
    Disabling `openapi_url` also disables both doc UIs (they fetch it), so all
    three ride the one environment check.
    """
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

# Cross-origin access for the prod Static-Web-App ↔ Container-Apps split. Added
# only when origins are configured (empty in dev — the Vite proxy keeps it
# same-origin), so the allowlist is explicit and never `*`. credentials=True so
# the bearer/auth flow works from the SPA origin.
_cors_origins = get_settings().cors_allow_origin_list
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
    )


@app.middleware("http")
async def reject_nul_in_url_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """NUL (``\\x00``) in the URL — 422, same contract as `ApiModel` (#567).

    `ApiModel` guards request *bodies*; a NUL smuggled into a query/path string
    (``?status=succ%00eeded``) otherwise reaches a SQL parameter and dies as the
    same driver ``ValueError`` → 500. `%00` is the only percent-encoding of NUL
    (hex digits are case-free, and ``%2500`` decodes to the literal text
    ``%00``, not the byte), so a raw-bytes scan is exact — no decode needed.
    """
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
    # Join key between the request's span and its structlog lines (A3). Done
    # here, not in a server_request_hook: the OTel middleware is outermost, so
    # at span start this middleware hasn't resolved the request_id yet.
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
app.include_router(users_router.router, prefix="/api/v1")
app.include_router(probe_router.router, prefix="/api/v1")
app.include_router(connections_router.router, prefix="/api/v1")
app.include_router(suites_router.router, prefix="/api/v1")
app.include_router(checks_router.router, prefix="/api/v1")
app.include_router(notifications_router.router, prefix="/api/v1")
app.include_router(runs_router.router, prefix="/api/v1")
app.include_router(dashboard_router.router, prefix="/api/v1")
app.include_router(schedules_router.router, prefix="/api/v1")
app.include_router(shares_router.router, prefix="/api/v1")
app.include_router(orchestration_router.router, prefix="/api/v1")
app.include_router(trigger_bindings_router.router, prefix="/api/v1")
app.include_router(admin_router.router, prefix="/api/v1")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# Mount the FastMCP server last so its routes don't shadow the versioned API.
# Path "/" on the sub-app since we mount it under /mcp (fastmcp docs).
if _mcp_app is not None:
    app.mount("/mcp", _mcp_app)


# Spans (A3): no-op unless APPLICATIONINSIGHTS_CONNECTION_STRING is set.
# MUST stay at module scope — Starlette builds its middleware stack on the
# first ASGI call (the lifespan scope itself), so instrumenting from inside
# the lifespan handler is too late and silently emits no spans (see
# tracing.py's call-ordering note + the regression test in test_tracing.py).
# instrument_celery() here covers the PRODUCER side (traceparent injection on
# task publish); the worker side hooks worker_process_init in celery_app.py.
configure_tracing(service_name="dataq-api")
instrument_fastapi(app)
instrument_celery()
