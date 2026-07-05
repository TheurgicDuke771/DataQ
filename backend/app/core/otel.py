"""Shared OpenTelemetry exporter resolution — the vendor-neutral observability seam (ADR 0010).

Both signal pipelines resolve their backends here from settings so they can't
drift:

- **tracing.py** — request/task spans (a ``BatchSpanProcessor`` per exporter).
- **logging.py** — the structlog → stdlib → OTel log bridge (#524, which replaced
  the EOL opencensus ``AzureLogHandler``).

The exporter behind the seam is **Azure Monitor** — configured when
``APPLICATIONINSIGHTS_CONNECTION_STRING`` is set; unset ⇒ telemetry is a complete
no-op (the pre-#524 ``AzureLogHandler`` gate). A generic OTLP/HTTP exporter joins
it behind the same seam in #589.

All exporter imports are **lazy** (repo convention, see ``secrets.py``) so a
deployment with telemetry off never pays the import cost.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.app.core.config import Settings, get_settings

if TYPE_CHECKING:
    from opentelemetry.sdk._logs.export import LogRecordExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace.export import SpanExporter


def telemetry_enabled(settings: Settings | None = None) -> bool:
    """True when at least one exporter backend is configured."""
    settings = settings or get_settings()
    return bool(settings.applicationinsights_connection_string)


def build_resource(service_name: str) -> Resource:
    """The OTel Resource carrying ``service.name`` (App Insights cloud role name)."""
    from opentelemetry.sdk.resources import Resource

    return Resource.create({"service.name": service_name})


def build_span_exporters(settings: Settings | None = None) -> list[SpanExporter]:
    """The configured span exporters (Azure Monitor for now; OTLP joins in #589)."""
    settings = settings or get_settings()
    exporters: list[SpanExporter] = []

    conn = settings.applicationinsights_connection_string
    if conn:
        from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter

        exporters.append(AzureMonitorTraceExporter(connection_string=conn))

    return exporters


def build_log_exporters(settings: Settings | None = None) -> list[LogRecordExporter]:
    """The configured log exporters (Azure Monitor for now; OTLP joins in #589)."""
    settings = settings or get_settings()
    exporters: list[LogRecordExporter] = []

    conn = settings.applicationinsights_connection_string
    if conn:
        from azure.monitor.opentelemetry.exporter import AzureMonitorLogExporter

        exporters.append(AzureMonitorLogExporter(connection_string=conn))

    return exporters
