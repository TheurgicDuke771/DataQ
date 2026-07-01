"""Tests for request/task span instrumentation (WEEK7 A3).

The Azure exporter is stubbed with the SDK's InMemorySpanExporter so the whole
configure → instrument → span-emit path runs for real without any network.
`trace.set_tracer_provider` is process-global and set-once, so the autouse
fixture no-ops it (both instrumentors receive the provider explicitly via
``tracer_provider=``, so nothing here depends on the global) and shuts the
provider down on teardown — no cross-test poisoning, no leaked export thread.
"""

import importlib
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from backend.app.core import tracing
from backend.app.core.config import get_settings

_CONN = "InstrumentationKey=00000000-0000-0000-0000-000000000000"


@pytest.fixture(autouse=True)
def _isolate_tracing_state(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(tracing, "_provider", None)
    monkeypatch.setattr("opentelemetry.trace.set_tracer_provider", lambda provider: None)
    yield
    if tracing._provider is not None:
        tracing._provider.shutdown()


@pytest.fixture()
def in_memory_exporter(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    """Swap the Azure exporter for an in-memory one; enable tracing settings."""
    exporter = InMemorySpanExporter()
    monkeypatch.setattr(
        "azure.monitor.opentelemetry.exporter.AzureMonitorTraceExporter",
        lambda *, connection_string: exporter,
    )
    monkeypatch.setattr(
        tracing,
        "get_settings",
        lambda: SimpleNamespace(applicationinsights_connection_string=_CONN),
    )
    return exporter


def _server_span_routes(exporter: InMemorySpanExporter) -> set[Any]:
    assert tracing._provider is not None
    tracing._provider.force_flush()
    return {
        s.attributes.get("http.route")
        for s in exporter.get_finished_spans()
        if s.kind.name == "SERVER" and s.attributes
    }


def test_configure_tracing_is_noop_without_connection_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tracing,
        "get_settings",
        lambda: SimpleNamespace(applicationinsights_connection_string=None),
    )
    tracing.configure_tracing(service_name="dataq-api")
    assert tracing._provider is None


def test_instrumentors_and_tagger_are_noops_when_tracing_off() -> None:
    """With no provider configured, instrument_*/tag_request_id must not wrap anything."""
    app = FastAPI()

    @app.get("/api/v1/ping")
    def ping() -> dict[str, str]:
        tracing.tag_request_id("rid-1")  # must not raise with tracing off
        return {"pong": "yes"}

    tracing.instrument_fastapi(app)
    tracing.instrument_celery()

    assert TestClient(app).get("/api/v1/ping").status_code == 200
    assert not getattr(app, "_is_instrumented_by_opentelemetry", False)


def test_spans_emitted_and_excluded_urls_and_query_scrubbed(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """Enabled path: span per API request; none for /healthz or the webhook
    (its ?token= must never reach attributes, #494); query strings scrubbed
    from URL attributes; request_id tag helper works inside a request."""
    tracing.configure_tracing(service_name="dataq-api")
    assert tracing._provider is not None

    app = FastAPI()

    @app.get("/api/v1/ping")
    def ping() -> dict[str, str]:
        tracing.tag_request_id("rid-42")
        return {"pong": "yes"}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/v1/orchestration/events/adf")
    def adf_webhook() -> dict[str, str]:
        return {"received": "yes"}

    tracing.instrument_fastapi(app)
    client = TestClient(app)
    assert client.get("/api/v1/ping?q=olivia@corp.example").status_code == 200
    assert client.get("/healthz").status_code == 200
    assert client.post("/api/v1/orchestration/events/adf?token=supersecret").status_code == 200

    routes = _server_span_routes(in_memory_exporter)
    assert "/api/v1/ping" in routes
    assert "/healthz" not in routes
    assert not any("orchestration" in str(r) for r in routes)

    spans = in_memory_exporter.get_finished_spans()
    ping_server = next(
        s
        for s in spans
        if s.kind.name == "SERVER" and (s.attributes or {}).get("http.route") == "/api/v1/ping"
    )
    assert (ping_server.attributes or {}).get("dataq.request_id") == "rid-42"
    # Neither the webhook secret nor the PII-bearing query may appear anywhere.
    for span in spans:
        rendered = str(dict(span.attributes or {}))
        assert "supersecret" not in rendered
        assert "olivia@corp.example" not in rendered


def test_excluded_urls_are_anchored() -> None:
    """`healthz` as a substring of a real path/host must NOT be excluded (review
    finding: unanchored patterns re.search the full URL, hostname included)."""
    from opentelemetry.util.http import parse_excluded_urls

    excl = parse_excluded_urls(tracing.EXCLUDED_URLS)
    assert excl.url_disabled("http://api/healthz")
    assert excl.url_disabled("http://api/api/v1/orchestration/events/adf")
    assert not excl.url_disabled("http://api/api/v1/suites/healthz-suite/run")
    assert not excl.url_disabled("http://healthz-probe.internal/api/v1/suites")


def test_shipped_main_wiring_emits_request_spans(
    monkeypatch: pytest.MonkeyPatch, in_memory_exporter: InMemorySpanExporter
) -> None:
    """Regression for the review finding that killed prod spans: Starlette
    builds its middleware stack on the FIRST ASGI call (the lifespan scope), so
    instrumentation must happen at module scope in main.py — instrumenting from
    the lifespan handler silently emits nothing. Reload main with tracing
    enabled and assert the real app produces server spans."""
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", _CONN)
    # in_memory_exporter patched tracing.get_settings with a stub; the real
    # module-level wiring must read real (env-driven) settings instead.
    monkeypatch.setattr(tracing, "get_settings", get_settings)
    get_settings.cache_clear()

    import backend.app.main as main

    try:
        reloaded = importlib.reload(main)
        # No lifespan on purpose: module-scope instrumentation must suffice.
        client = TestClient(reloaded.app, raise_server_exceptions=False)
        assert client.get("/api/v1/nonexistent").status_code == 404
        assert tracing._provider is not None
        tracing._provider.force_flush()
        server_spans = [
            s for s in in_memory_exporter.get_finished_spans() if s.kind.name == "SERVER"
        ]
        assert server_spans, "shipped main.py wiring emitted no request spans"
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        if tracing._provider is not None:
            tracing._provider.shutdown()
        tracing._provider = None
        # main.py's module scope also instrumented Celery (producer side);
        # unhook it so later tests' eager tasks don't run through stale hooks.
        from opentelemetry.instrumentation.celery import CeleryInstrumentor

        CeleryInstrumentor().uninstrument()
        importlib.reload(main)
