"""Request/task span instrumentation (WEEK7 A3 — App Insights spans).

OTel-neutral core (ADR 0010): the OpenTelemetry SDK + FastAPI/Celery
instrumentations are vendor-neutral; the exporter backends live behind the
shared seam in ``otel.py`` (Azure Monitor and/or generic OTLP/HTTP — #589),
resolved from settings. Tracing is on when **any** backend resolves an exporter
(``otel.build_span_exporters``) — none ⇒ a complete no-op, matching the log
pipeline's gate. Spans and logs (#524) now share the same seam and Resource
(service.name).

Deliberately the **exporter-only** Azure package (`azure-monitor-
opentelemetry-exporter`), NOT the `azure-monitor-opentelemetry` distro: the
distro auto-configures the logging pipeline and would double-configure the
structlog log bridge in ``logging.py``.

All exporter imports are lazy (repo convention, see ``secrets.py``) so
deployments without telemetry never pay the import cost.

Call-ordering constraint (learned the hard way in review): FastAPI
instrumentation patches ``app.build_middleware_stack``, and Starlette builds
the middleware stack on the FIRST ASGI call — which is the *lifespan scope
itself*. So ``instrument_fastapi`` MUST run at module scope (before the app
serves anything), never from inside the lifespan handler, or it silently
instruments nothing. ``backend/tests/core/test_tracing.py`` pins this with a
regression test against the real ``backend.app.main`` wiring.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from backend.app.core import otel
from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.trace import Span

log = get_logger(__name__)

# URLs whose server spans are never recorded. Comma-separated regexes in the
# opentelemetry-util-http `excluded_urls` format: each part is `re.search`ed
# against the full request URL (scheme://host/path — the query string is not
# part of the match), so patterns must be ANCHORED to path boundaries or a
# hostname containing e.g. "healthz" would silently exclude every span.
# - `/healthz$` — probe noise.
# - `/api/v1/orchestration/events/` — the webhook receivers carry a secret in
#   the query string (`?token=<secret>`, ADR 0006 / #494) which must never
#   land in span attributes; exclusion happens before the span is created.
EXCLUDED_URLS: Final = "/healthz$,/api/v1/orchestration/events/"

# Span attributes (old + new HTTP semconv) that may embed the request URL.
# `_scrub_query_hook` rewrites them to the path so query strings — which can
# carry PII (e.g. /users/search?q=<email>) — never reach App Insights. Same
# posture as the request log: path only (#494).
_URL_ATTRS: Final = ("http.url", "http.target", "url.full", "url.query")

_provider: TracerProvider | None = None


def configure_tracing(service_name: str) -> None:
    """Install a TracerProvider exporting to the configured backend(s), or no-op.

    Backends (Azure Monitor and/or generic OTLP/HTTP — #589) come from the shared
    ``otel`` seam. Idempotent per process (the global tracer provider can only be
    set once); safe to call from both the API module scope and the Celery
    worker-process-init signal.
    """
    global _provider
    if _provider is not None:
        return
    settings = get_settings()
    try:
        exporters = otel.build_span_exporters(settings)
        if not exporters:
            return

        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=otel.build_resource(service_name))
        for exporter in exporters:
            provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _provider = provider
        log.info("tracing_configured", service_name=service_name, exporters=len(exporters))
    except Exception:
        # Same posture as the log bridge: an observability misconfig (bad OTLP
        # endpoint/headers, SDK drift) must not crash the API lifespan or the celery
        # worker-init signal. Leave _provider None so instrument_* no-op.
        log.warning("tracing_setup_failed", service_name=service_name, exc_info=True)


def _scrub_query_hook(span: Span, scope: dict[str, Any]) -> None:
    """server_request_hook: overwrite URL-bearing attributes with the path only."""
    if span is None or not span.is_recording():
        return
    path = str(scope.get("path", ""))
    for attr in _URL_ATTRS:
        span.set_attribute(attr, path if attr != "url.query" else "")


def instrument_fastapi(app: FastAPI) -> None:
    """Emit a server span per request (minus EXCLUDED_URLS). No-op when tracing is off.

    MUST be called at module scope, before the app's first ASGI call — see the
    module docstring's call-ordering constraint.
    """
    if _provider is None:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=_provider,
        excluded_urls=EXCLUDED_URLS,
        server_request_hook=_scrub_query_hook,
    )


def instrument_celery() -> None:
    """Emit a span per Celery task run/publish. No-op when tracing is off.

    Must run on BOTH sides: the worker (task/consumer spans, via
    worker_process_init) AND the API process (producer spans + traceparent
    header injection on publish — without it, task spans are orphaned root
    traces unlinked from the triggering request).
    """
    if _provider is None:
        return
    from opentelemetry.instrumentation.celery import CeleryInstrumentor

    CeleryInstrumentor().instrument(tracer_provider=_provider)


def tag_request_id(request_id: str) -> None:
    """Stamp the request_id onto the current span so App Insights spans can be
    joined to the structlog lines keyed on the same id. No-op when tracing is
    off or no span is recording."""
    if _provider is None:
        return
    from opentelemetry import trace

    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("dataq.request_id", request_id)
