import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Final

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastmcp.utilities.lifespan import combine_lifespans

from backend.app.api.v1 import admin as admin_router
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
from backend.app.core.errors import register_exception_handlers
from backend.app.core.logging import configure_logging, get_logger, request_id_var
from backend.app.core.tracing import configure_tracing, instrument_fastapi
from backend.app.mcp import build_mcp_app

REQUEST_ID_HEADER: Final = "X-Request-ID"
# Validate caller-supplied X-Request-ID before echoing it (security audit
# 2026-05-28): cap length, restrict charset so log lines and response
# headers can't be polluted with arbitrary content.
_REQUEST_ID_RE: Final = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    # Spans (A3): no-op unless APPLICATIONINSIGHTS_CONNECTION_STRING is set.
    # /healthz + the secret-bearing webhook URLs are excluded (tracing.py).
    configure_tracing(service_name="dataq-api")
    instrument_fastapi(_app)
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
async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    incoming = request.headers.get(REQUEST_ID_HEADER)
    rid = incoming if incoming and _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
    token = request_id_var.set(rid)
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
