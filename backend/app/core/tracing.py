"""Request/task span instrumentation (WEEK7 A3 — App Insights spans).

OTel-neutral core (ADR 0010): the OpenTelemetry SDK + FastAPI/Celery
instrumentations are vendor-neutral; Azure Monitor is one *exporter* behind
the seam (swap the exporter per cloud, nothing else changes). Everything is
gated on ``settings.applicationinsights_connection_string`` — unset ⇒ a
complete no-op, matching ``configure_logging()``'s AzureLogHandler gate.

Deliberately the **exporter-only** package (`azure-monitor-opentelemetry-
exporter`), NOT the `azure-monitor-opentelemetry` distro: the distro
auto-configures the logging pipeline and would collide with the hardened
structlog + opencensus ``AzureLogHandler`` chain in ``logging.py``
(#393/#405 Py3.13 lock fixes). Migrating the *log* pipeline opencensus→OTel
is tracked separately; this module owns spans only.

All OTel/Azure imports are lazy (repo convention, see ``secrets.py``) so
deployments without App Insights never pay the import cost.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI
    from opentelemetry.sdk.trace import TracerProvider

log = get_logger(__name__)

# URLs whose server spans are never recorded (comma-separated regexes, the
# opentelemetry-instrumentation-asgi `excluded_urls` format): /healthz is
# probe noise, and the orchestration webhook receivers carry a secret in the
# query string (`?token=<secret>`, ADR 0006 / #494) which must never land in
# span attributes such as `url.full`.
EXCLUDED_URLS: Final = "healthz,api/v1/orchestration/events/.*"

_provider: TracerProvider | None = None


def configure_tracing(service_name: str) -> None:
    """Install a TracerProvider exporting to App Insights, or no-op.

    Idempotent per process (the global tracer provider can only be set once);
    safe to call from both the API lifespan and the Celery worker-process-init
    signal.
    """
    global _provider
    if _provider is not None:
        return
    conn = get_settings().applicationinsights_connection_string
    if not conn:
        return

    from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(AzureMonitorTraceExporter(connection_string=conn))
    )
    trace.set_tracer_provider(provider)
    _provider = provider
    log.info("tracing_configured", service_name=service_name)


def instrument_fastapi(app: FastAPI) -> None:
    """Emit a server span per request (minus EXCLUDED_URLS). No-op when tracing is off."""
    if _provider is None:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, tracer_provider=_provider, excluded_urls=EXCLUDED_URLS)


def instrument_celery() -> None:
    """Emit a span per Celery task run. No-op when tracing is off."""
    if _provider is None:
        return
    from opentelemetry.instrumentation.celery import CeleryInstrumentor

    CeleryInstrumentor().instrument(tracer_provider=_provider)
