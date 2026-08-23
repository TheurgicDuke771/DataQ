"""Request/task span instrumentation (WEEK7 A3 — App Insights spans)."""

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

# URLs whose server spans are never recorded.
EXCLUDED_URLS: Final = "/healthz$,/api/v1/orchestration/events/"

# Span attributes (old + new HTTP semconv) that may embed the request URL.
_URL_ATTRS: Final = ("http.url", "http.target", "url.full", "url.query")

_provider: TracerProvider | None = None


def configure_tracing(service_name: str) -> None:
    """Install a TracerProvider exporting to the configured backend(s), or no-op."""
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
        # Same posture as the log bridge: an observability misconfig (bad OTLP endpoint/headers, SDK
        # drift) must not crash the API lifespan or the celery worker-init signal.
        log.warning("tracing_setup_failed", service_name=service_name, exc_info=True)


def _scrub_query_hook(span: Span, scope: dict[str, Any]) -> None:
    """server_request_hook: overwrite URL-bearing attributes with the path only."""
    if span is None or not span.is_recording():
        return
    path = str(scope.get("path", ""))
    for attr in _URL_ATTRS:
        span.set_attribute(attr, path if attr != "url.query" else "")


def instrument_fastapi(app: FastAPI) -> None:
    """Emit a server span per request (minus EXCLUDED_URLS). No-op when tracing is off."""
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
    """Emit a span per Celery task run/publish. No-op when tracing is off."""
    if _provider is None:
        return
    from opentelemetry.instrumentation.celery import CeleryInstrumentor

    CeleryInstrumentor().instrument(tracer_provider=_provider)


def tag_request_id(request_id: str) -> None:
    """Stamp the request_id onto the current span so App Insights spans can be
    joined to the structlog lines keyed on the same id. No-op when tracing is
    off or no span is recording.
    """
    if _provider is None:
        return
    from opentelemetry import trace

    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("dataq.request_id", request_id)
