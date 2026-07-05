"""Tests for the shared OTel exporter seam (otel.py) — the resolution of which
backend(s) each signal pipeline exports to (#524 logs + #589 generic OTLP).

The lazy exporter classes are swapped for sentinels so nothing touches the
network; the tests assert only the *resolution* logic (which backends, in which
order) across the four config combinations.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.core import otel

_CONN = "InstrumentationKey=00000000-0000-0000-0000-000000000000"


def _settings(*, conn: str | None = None, otlp: str | None = None) -> Any:
    return SimpleNamespace(
        applicationinsights_connection_string=conn, otel_exporter_otlp_endpoint=otlp
    )


@pytest.fixture
def sentinel_exporters(monkeypatch: pytest.MonkeyPatch) -> None:
    """Swap all four lazy exporter classes for record-only sentinels."""

    class _Azure:
        def __init__(self, *, connection_string: str) -> None:
            self.connection_string = connection_string

    class _Otlp:
        def __init__(self, *, endpoint: str) -> None:
            self.endpoint = endpoint

    monkeypatch.setattr("azure.monitor.opentelemetry.exporter.AzureMonitorTraceExporter", _Azure)
    monkeypatch.setattr("azure.monitor.opentelemetry.exporter.AzureMonitorLogExporter", _Azure)
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter", _Otlp
    )
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http._log_exporter.OTLPLogExporter", _Otlp
    )
    return None


def test_no_exporters_when_telemetry_off(sentinel_exporters: None) -> None:
    assert otel.build_span_exporters(_settings()) == []
    assert otel.build_log_exporters(_settings()) == []


def test_azure_only(sentinel_exporters: None) -> None:
    spans = otel.build_span_exporters(_settings(conn=_CONN))
    logs = otel.build_log_exporters(_settings(conn=_CONN))
    assert [e.connection_string for e in spans] == [_CONN]
    assert [e.connection_string for e in logs] == [_CONN]


def test_otlp_only_appends_signal_paths(sentinel_exporters: None) -> None:
    spans = otel.build_span_exporters(_settings(otlp="http://collector:4318"))
    logs = otel.build_log_exporters(_settings(otlp="http://collector:4318"))
    assert [e.endpoint for e in spans] == ["http://collector:4318/v1/traces"]
    assert [e.endpoint for e in logs] == ["http://collector:4318/v1/logs"]


def test_otlp_base_trailing_slash_is_normalised(sentinel_exporters: None) -> None:
    spans = otel.build_span_exporters(_settings(otlp="http://collector:4318/"))
    assert [e.endpoint for e in spans] == ["http://collector:4318/v1/traces"]


def test_both_backends_active_for_parity(sentinel_exporters: None) -> None:
    # The W2 parity check: the same signal exports to App Insights AND a local
    # OTLP consumer at once — Azure first, then OTLP.
    spans = otel.build_span_exporters(_settings(conn=_CONN, otlp="http://collector:4318"))
    logs = otel.build_log_exporters(_settings(conn=_CONN, otlp="http://collector:4318"))
    assert [type(e).__name__ for e in spans] == ["_Azure", "_Otlp"]
    assert [type(e).__name__ for e in logs] == ["_Azure", "_Otlp"]


def test_build_resource_carries_service_name() -> None:
    resource = otel.build_resource("dataq-api")
    assert resource.attributes["service.name"] == "dataq-api"
