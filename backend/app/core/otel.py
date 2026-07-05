"""Shared OpenTelemetry exporter resolution — the vendor-neutral observability seam (ADR 0010).

Both signal pipelines resolve their backends here from settings so they can't
drift:

- **tracing.py** — request/task spans (a ``BatchSpanProcessor`` per exporter).
- **logging.py** — the structlog → stdlib → OTel log bridge (#524, which replaced
  the EOL opencensus ``AzureLogHandler``).

Two exporters sit behind the seam and **may both be active at once**:

- **Azure Monitor** — when ``APPLICATIONINSIGHTS_CONNECTION_STRING`` is set.
- **Generic OTLP/HTTP** — when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set (#589): spans
  and logs also export to any OTLP consumer (Grafana/Tempo, Jaeger, Datadog, …).
  Base-endpoint semantics per the OTel spec — ``/v1/traces`` and ``/v1/logs`` are
  appended to the configured base. Running both at once is exactly the parity
  check (the same trace/log lands in App Insights AND a local collector).

Neither set ⇒ telemetry is a complete no-op (the pre-#524 ``AzureLogHandler`` gate).

All exporter imports are **lazy** (repo convention, see ``secrets.py``) so a
deployment that uses neither backend never pays the import cost.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.app.core.config import Settings, get_settings

if TYPE_CHECKING:
    from opentelemetry.sdk._logs.export import LogRecordExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace.export import SpanExporter


def _otlp_base(settings: Settings) -> str | None:
    """The OTLP base endpoint (no trailing slash), or None when OTLP is off."""
    endpoint = settings.otel_exporter_otlp_endpoint
    return endpoint.rstrip("/") if endpoint else None


def telemetry_enabled(settings: Settings | None = None) -> bool:
    """True when at least one exporter backend is configured (Azure or OTLP)."""
    settings = settings or get_settings()
    return bool(settings.applicationinsights_connection_string) or bool(_otlp_base(settings))


def build_resource(service_name: str) -> Resource:
    """The OTel Resource carrying ``service.name`` (App Insights cloud role name)."""
    from opentelemetry.sdk.resources import Resource

    return Resource.create({"service.name": service_name})


def build_span_exporters(settings: Settings | None = None) -> list[SpanExporter]:
    """The configured span exporters — Azure Monitor and/or generic OTLP/HTTP."""
    settings = settings or get_settings()
    exporters: list[SpanExporter] = []

    conn = settings.applicationinsights_connection_string
    if conn:
        from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter

        exporters.append(AzureMonitorTraceExporter(connection_string=conn))

    base = _otlp_base(settings)
    if base:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporters.append(OTLPSpanExporter(endpoint=f"{base}/v1/traces"))

    return exporters


def build_log_exporters(settings: Settings | None = None) -> list[LogRecordExporter]:
    """The configured log exporters — Azure Monitor and/or generic OTLP/HTTP."""
    settings = settings or get_settings()
    exporters: list[LogRecordExporter] = []

    conn = settings.applicationinsights_connection_string
    if conn:
        from azure.monitor.opentelemetry.exporter import AzureMonitorLogExporter

        exporters.append(AzureMonitorLogExporter(connection_string=conn))

    base = _otlp_base(settings)
    if base:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

        exporters.append(OTLPLogExporter(endpoint=f"{base}/v1/logs"))

    return exporters
