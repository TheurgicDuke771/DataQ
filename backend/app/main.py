import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Final

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

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
from backend.app.core.config import get_settings
from backend.app.core.errors import register_exception_handlers
from backend.app.core.logging import configure_logging, get_logger, request_id_var

REQUEST_ID_HEADER: Final = "X-Request-ID"
# Validate caller-supplied X-Request-ID before echoing it (security audit
# 2026-05-28): cap length, restrict charset so log lines and response
# headers can't be polluted with arbitrary content.
_REQUEST_ID_RE: Final = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
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


app = FastAPI(title="DataQ API", lifespan=lifespan)

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
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        _log.exception(
            "request_failed",
            method=request.method,
            path=request.url.path,
            duration_ms=elapsed_ms,
        )
        request_id_var.reset(token)
        raise
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    _log.info(
        "request",
        method=request.method,
        path=request.url.path,
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
