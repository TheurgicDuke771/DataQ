"""CORS middleware ACTIVATION tests (#385).

`test_config.py` covers `CORS_ALLOW_ORIGINS` string parsing; these cover the
security-relevant wiring in `main.py` — that `CORSMiddleware` is added only
when origins are configured, echoes exactly the allowlisted origin (never
`*`), and ignores everything else.

The middleware is attached at module scope from `get_settings()`, so each case
reloads `backend.app.main` under the desired env (the `test_tracing.py`
pattern) and restores the pristine module afterwards so other tests keep the
original `app` object.
"""

import importlib
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import get_settings

ALLOWED = "https://app.example.com"


@pytest.fixture()
def cors_client(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """TestClient against a `main` reloaded with CORS_ALLOW_ORIGINS = param."""
    origins: str | None = request.param
    if origins is None:
        monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
    else:
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


@pytest.mark.parametrize("cors_client", [None], indirect=True)
def test_middleware_absent_when_unconfigured(cors_client: TestClient) -> None:
    """Dev default (unset) → no CORS middleware, so no ACAO header at all."""
    resp = cors_client.get("/healthz", headers={"Origin": ALLOWED})
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers


@pytest.mark.parametrize("cors_client", [ALLOWED], indirect=True)
def test_configured_origin_is_echoed_never_star(cors_client: TestClient) -> None:
    resp = cors_client.get("/healthz", headers={"Origin": ALLOWED})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ALLOWED
    # credentials mode requires the exact origin — a `*` here would be both a
    # spec violation and an allowlist bypass.
    assert resp.headers["access-control-allow-origin"] != "*"
    assert resp.headers["access-control-allow-credentials"] == "true"


@pytest.mark.parametrize("cors_client", [ALLOWED], indirect=True)
def test_unlisted_origin_gets_no_cors_headers(cors_client: TestClient) -> None:
    resp = cors_client.get("/healthz", headers={"Origin": "https://evil.example.com"})
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers


@pytest.mark.parametrize("cors_client", [ALLOWED], indirect=True)
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


@pytest.mark.parametrize("cors_client", [ALLOWED], indirect=True)
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
