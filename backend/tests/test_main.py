"""Tests for the request_id middleware: validation + structured-log emission.

Per 2026-05-28 security audit + observability work (#50, #51).
"""

import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.main import REQUEST_ID_HEADER, app, docs_kwargs


@pytest.fixture
def client() -> Iterator[TestClient]:
    # FastAPI lifespan runs configure_logging(); avoid re-running it for tests
    # by entering the context manually under TestClient.
    with TestClient(app) as c:
        yield c


# ───────────────────────── X-Request-ID validation ─────────────────────────


def test_request_id_generated_when_caller_omits(client: TestClient) -> None:
    response = client.get("/healthz")
    rid = response.headers.get(REQUEST_ID_HEADER)
    assert rid is not None
    # uuid4().hex is 32 lowercase hex chars
    assert len(rid) == 32
    assert all(c in "0123456789abcdef" for c in rid)


def test_request_id_echoed_when_valid(client: TestClient) -> None:
    response = client.get("/healthz", headers={REQUEST_ID_HEADER: "trace-abc.123_XYZ"})
    assert response.headers[REQUEST_ID_HEADER] == "trace-abc.123_XYZ"


def test_request_id_replaced_when_too_long(client: TestClient) -> None:
    """Caller-supplied IDs over 64 chars are rejected (security audit #2)."""
    too_long = "a" * 65
    response = client.get("/healthz", headers={REQUEST_ID_HEADER: too_long})
    echoed = response.headers[REQUEST_ID_HEADER]
    assert echoed != too_long
    assert len(echoed) == 32  # fresh uuid


def test_request_id_replaced_on_bad_chars(client: TestClient) -> None:
    """Caller-supplied IDs containing whitespace or JSON-control chars are rejected."""
    for bad in ['inject"quote', "with space", "tab\there", "semi;colon"]:
        response = client.get("/healthz", headers={REQUEST_ID_HEADER: bad})
        echoed = response.headers[REQUEST_ID_HEADER]
        assert echoed != bad
        assert len(echoed) == 32


def test_request_id_replaced_on_empty(client: TestClient) -> None:
    response = client.get("/healthz", headers={REQUEST_ID_HEADER: ""})
    echoed = response.headers[REQUEST_ID_HEADER]
    assert len(echoed) == 32


# ───────────────────────── per-request structured log ─────────────────────────


def _request_events_from_caplog(records: list[logging.LogRecord]) -> list[dict[str, object]]:
    """Pick request-event records that structlog routed through stdlib logging.

    structlog's stdlib bridge emits records whose `msg` is the rendered dict
    (post-processors) — args slot carries the original event_dict.
    """
    out: list[dict[str, object]] = []
    for rec in records:
        # The structlog ProcessorFormatter wraps the original event_dict on rec.msg
        # (after processors run). We pull from rec.__dict__ to get the structured fields.
        evt = getattr(rec, "_record", None) or rec.__dict__.get("event_dict")
        if evt is None and isinstance(rec.msg, dict):
            evt = rec.msg
        if not isinstance(evt, dict):
            continue
        if evt.get("event") == "request":
            out.append(evt)
    return out


def test_per_request_log_emitted_on_success(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """#51: every successful request emits one event=request structlog entry."""
    caplog.set_level(logging.INFO, logger="backend.app.main")
    client.get("/healthz", headers={REQUEST_ID_HEADER: "trace-1"})
    events = _request_events_from_caplog(caplog.records)
    assert len(events) == 1
    evt = events[0]
    assert evt["method"] == "GET"
    assert evt["path"] == "/healthz"
    assert evt["status"] == 200
    assert isinstance(evt["duration_ms"], int | float)
    assert evt["duration_ms"] >= 0
    assert evt["request_id"] == "trace-1"


def test_per_request_log_uses_generated_request_id_when_invalid(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Invalid X-Request-ID → generated UUID propagates into the request log."""
    caplog.set_level(logging.INFO, logger="backend.app.main")
    client.get("/healthz", headers={REQUEST_ID_HEADER: "with space"})
    events = _request_events_from_caplog(caplog.records)
    assert len(events) == 1
    rid = events[0]["request_id"]
    assert isinstance(rid, str)
    assert len(rid) == 32  # uuid4().hex, not the rejected "with space"


# ───────────────────────── prod-docs gate (#170) ─────────────────────────


def test_docs_enabled_in_dev_and_staging() -> None:
    for env in ("dev", "staging"):
        kw = docs_kwargs(Settings(_env_file=None, environment=env))
        assert kw == {
            "docs_url": "/docs",
            "redoc_url": "/redoc",
            "openapi_url": "/openapi.json",
        }


def test_docs_disabled_in_prod() -> None:
    kw = docs_kwargs(Settings(_env_file=None, environment="prod"))
    assert kw == {"docs_url": None, "redoc_url": None, "openapi_url": None}


def test_openapi_schema_served_in_test_env(client: TestClient) -> None:
    """The test env is non-prod, so the wired app exposes the schema + docs UI."""
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


def test_every_api_endpoint_has_summary_and_tags() -> None:
    """Swagger-completeness guardrail (W7 hardening): every /api/* operation
    carries a `summary` and at least one `tag`, so Swagger/ReDoc stay navigable
    and self-describing. Fails loudly if a new endpoint omits them."""
    schema = app.openapi()
    http_methods = {"get", "post", "put", "patch", "delete"}
    missing: list[str] = []
    for path, operations in schema["paths"].items():
        if not path.startswith("/api/"):
            continue  # /healthz and the mounted /mcp app are out of scope
        for method, op in operations.items():
            if method not in http_methods:
                continue
            if not op.get("summary"):
                missing.append(f"{method.upper()} {path}: missing summary")
            if not op.get("tags"):
                missing.append(f"{method.upper()} {path}: missing tags")
    assert not missing, "endpoints missing Swagger metadata:\n" + "\n".join(missing)
