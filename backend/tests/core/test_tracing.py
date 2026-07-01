"""Tests for request/task span instrumentation (WEEK7 A3).

The Azure exporter is stubbed with the SDK's InMemorySpanExporter so the whole
configure → instrument → span-emit path runs for real without any network.
`trace.set_tracer_provider` is process-global (a second set is ignored with a
warning), so the enabled-path assertions live in ONE test; the no-op paths
don't touch the global provider and are safe standalone.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from backend.app.core import tracing


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing, "_provider", None)


def _settings(conn: str | None) -> Any:
    return SimpleNamespace(applicationinsights_connection_string=conn)


def test_configure_tracing_is_noop_without_connection_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tracing, "get_settings", lambda: _settings(None))
    tracing.configure_tracing(service_name="dataq-api")
    assert tracing._provider is None


def test_instrumentors_are_noops_when_tracing_off() -> None:
    """With no provider configured, instrument_* must not import or wrap anything."""
    app = FastAPI()

    @app.get("/api/v1/ping")
    def ping() -> dict[str, str]:
        return {"pong": "yes"}

    tracing.instrument_fastapi(app)
    tracing.instrument_celery()

    # The app still serves; no ASGI wrapper / instrumentation flag was added.
    assert TestClient(app).get("/api/v1/ping").status_code == 200
    assert not getattr(app, "_is_instrumented_by_opentelemetry", False)


def test_spans_emitted_for_api_requests_but_not_excluded_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one enabled-path test (global tracer provider can be set only once).

    Covers: exporter constructed with the connection string, a server span per
    API request, and NO spans for /healthz or the secret-bearing orchestration
    webhook URL (its ?token= query must never reach span attributes, #494).
    """
    exporter = InMemorySpanExporter()
    ctor_args: list[str] = []

    def _fake_exporter(*, connection_string: str) -> InMemorySpanExporter:
        ctor_args.append(connection_string)
        return exporter

    monkeypatch.setattr(
        "azure.monitor.opentelemetry.exporter.AzureMonitorTraceExporter", _fake_exporter
    )
    conn = "InstrumentationKey=00000000-0000-0000-0000-000000000000"
    monkeypatch.setattr(tracing, "get_settings", lambda: _settings(conn))

    tracing.configure_tracing(service_name="dataq-api")
    assert ctor_args == [conn]
    assert tracing._provider is not None

    app = FastAPI()

    @app.get("/api/v1/ping")
    def ping() -> dict[str, str]:
        return {"pong": "yes"}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/v1/orchestration/events/adf")
    def adf_webhook() -> dict[str, str]:
        return {"received": "yes"}

    tracing.instrument_fastapi(app)
    client = TestClient(app)
    assert client.get("/api/v1/ping").status_code == 200
    assert client.get("/healthz").status_code == 200
    assert client.post("/api/v1/orchestration/events/adf?token=supersecret").status_code == 200

    tracing._provider.force_flush()
    server_spans = [s for s in exporter.get_finished_spans() if s.kind.name == "SERVER"]
    routes = {s.attributes.get("http.route") for s in server_spans if s.attributes}
    assert "/api/v1/ping" in routes
    assert "/healthz" not in routes
    assert not any("orchestration" in str(r) for r in routes)
    # Belt-and-braces: the webhook secret must not appear anywhere in any span.
    for span in exporter.get_finished_spans():
        assert "supersecret" not in str(dict(span.attributes or {}))
