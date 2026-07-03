"""CORS middleware ACTIVATION tests (#385).

`test_config.py` covers `CORS_ALLOW_ORIGINS` string parsing; these cover the
security-relevant wiring in `main.py` — that `CORSMiddleware` is added only
when origins are configured, echoes exactly the allowlisted origin (never
`*`), and ignores everything else.

The middleware is attached at module scope from `get_settings()`, so each
fixture reloads `backend.app.main` under the desired env (the
`test_tracing.py` pattern) and reloads it again under the original env on
teardown, so later tests import a `main` rebuilt with the real settings.
(Other test modules keep working regardless: they bind `app` at collection
time, before any reload here runs.)

The "unconfigured" case sets CORS_ALLOW_ORIGINS to "" rather than deleting it:
`Settings` also reads the gitignored `.env.app` dotenv, which `delenv` cannot
mask — a developer with the key populated locally would flip the middleware
back on. An empty env var overrides the dotenv and parses to [] (CORS off).
"""

import importlib
from collections.abc import Generator, Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import get_settings

ALLOWED = "https://app.example.com"


@contextmanager
def _client_with_origins(monkeypatch: pytest.MonkeyPatch, origins: str) -> Generator[TestClient]:
    """TestClient against a `main` reloaded with CORS_ALLOW_ORIGINS = origins."""
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", origins)
    get_settings.cache_clear()

    import backend.app.main as main

    try:
        reloaded = importlib.reload(main)
        with TestClient(reloaded.app) as client:
            yield client
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        importlib.reload(main)


@pytest.fixture()
def cors_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with _client_with_origins(monkeypatch, ALLOWED) as client:
        yield client


@pytest.fixture()
def no_cors_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with _client_with_origins(monkeypatch, "") as client:
        yield client


def test_middleware_absent_when_unconfigured(no_cors_client: TestClient) -> None:
    """Dev default (empty) → no CORS middleware, so no ACAO header at all."""
    resp = no_cors_client.get("/healthz", headers={"Origin": ALLOWED})
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers


def test_configured_origin_is_echoed_never_star(cors_client: TestClient) -> None:
    resp = cors_client.get("/healthz", headers={"Origin": ALLOWED})
    assert resp.status_code == 200
    # credentials mode requires the exact origin — a `*` here would be both a
    # spec violation and an allowlist bypass.
    assert resp.headers["access-control-allow-origin"] == ALLOWED
    assert resp.headers["access-control-allow-credentials"] == "true"


def test_unlisted_origin_gets_no_cors_headers(cors_client: TestClient) -> None:
    resp = cors_client.get("/healthz", headers={"Origin": "https://evil.example.com"})
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers


def test_preflight_allows_configured_origin(cors_client: TestClient) -> None:
    """Browser preflight (OPTIONS + Access-Control-Request-Method) round-trips."""
    resp = cors_client.options(
        "/api/v1/suites",
        headers={
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ALLOWED


def test_preflight_rejects_unlisted_origin(cors_client: TestClient) -> None:
    resp = cors_client.options(
        "/api/v1/suites",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    # Starlette's CORSMiddleware answers disallowed preflights with 400 and no
    # allow-origin header — the browser blocks the real request.
    assert resp.status_code == 400
    assert "access-control-allow-origin" not in resp.headers
