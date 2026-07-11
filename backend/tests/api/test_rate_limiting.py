"""End-to-end rate-limit tests over the wired app (#725, ADR 0035).

TestClient over `backend.app.main.app` — no DB fixtures needed: the limiter
counts *before* routing, so 404/401/whatever from the inner route suffices (we
only assert the 429 boundary, never the inner status). The `limiter` fixture
enables limiting with low limits + an injected in-memory store and freezes time
so a window boundary can't flake the counts.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.app.core import rate_limit
from backend.app.core.config import get_settings
from backend.app.core.rate_limit import InMemoryStore, set_store_for_testing
from backend.app.main import app

# A path with no route: the limiter runs pre-routing, so the inner 404 is
# irrelevant — we assert only whether the request was throttled.
PROBE = "/api/v1/__rl_probe__"
_FROZEN = 1_000_000.0


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Plain client — rate limiting OFF (suite default RATE_LIMIT_ENABLED=false)."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def limiter(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_UNAUTHENTICATED_PER_MINUTE", "3")
    monkeypatch.setenv("RATE_LIMIT_AUTHENTICATED_PER_MINUTE", "5")
    monkeypatch.setenv("RATE_LIMIT_WEBHOOK_PER_MINUTE", "2")
    get_settings.cache_clear()
    # Freeze time so every request in a test lands in one fixed window.
    monkeypatch.setattr(rate_limit, "_now", lambda: _FROZEN)
    set_store_for_testing(InMemoryStore(clock=lambda: _FROZEN))
    with TestClient(app) as c:
        yield c


def _structlog_events(records: list[logging.LogRecord]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for rec in records:
        evt = getattr(rec, "_record", None) or rec.__dict__.get("event_dict")
        if evt is None and isinstance(rec.msg, dict):
            evt = rec.msg
        if isinstance(evt, dict):
            out.append(evt)
    return out


# ───────────────────────── 1. full 429 contract ─────────────────────────


def test_unauth_429_at_limit_plus_one_full_contract(limiter: TestClient) -> None:
    for _ in range(3):  # UNAUTH limit = 3
        assert limiter.get(PROBE).status_code != 429
    resp = limiter.get(PROBE)
    assert resp.status_code == 429
    body = resp.json()
    assert body["error"]["code"] == "rate_limited"
    retry = body["error"]["detail"]["retry_after_seconds"]
    assert isinstance(retry, int)
    assert 1 <= retry <= 60
    assert resp.headers["Retry-After"] == str(retry)
    assert resp.headers["X-RateLimit-Limit"] == "3"
    assert resp.headers["X-RateLimit-Remaining"] == "0"
    # 429 still exits through request_id_middleware (innermost-limiter design).
    assert resp.headers.get("X-Request-ID")


# ───────────────────────── 2. per-token separation ─────────────────────────


def test_per_token_buckets_are_independent(limiter: TestClient) -> None:
    ha = {"Authorization": "Bearer token-AAA"}
    for _ in range(5):  # AUTH limit = 5
        assert limiter.get(PROBE, headers=ha).status_code != 429
    assert limiter.get(PROBE, headers=ha).status_code == 429

    # A different token is a fresh bucket.
    hb = {"Authorization": "Bearer token-BBB"}
    assert limiter.get(PROBE, headers=hb).status_code != 429

    # And none of those bearer requests touched the unauth IP bucket.
    for _ in range(3):  # UNAUTH limit = 3, still full
        assert limiter.get(PROBE).status_code != 429
    assert limiter.get(PROBE).status_code == 429


# ───────────────────────── 3. webhook class ─────────────────────────


def test_webhook_class_is_tighter_and_ip_keyed_despite_bearer(limiter: TestClient) -> None:
    path = "/api/v1/orchestration/events/adf"
    headers = {"Authorization": "Bearer token-AAA"}  # bearer must NOT switch it off ip-keying
    assert limiter.post(path, headers=headers).status_code != 429
    assert limiter.post(path, headers=headers).status_code != 429
    assert limiter.post(path, headers=headers).status_code == 429  # WEBHOOK limit = 2


# ───────────────────────── 4/5. exemptions ─────────────────────────


def test_healthz_is_exempt(limiter: TestClient) -> None:
    for _ in range(20):
        assert limiter.get("/healthz").status_code == 200


def test_options_preflight_is_exempt(limiter: TestClient) -> None:
    for _ in range(10):  # well past UNAUTH limit = 3
        assert limiter.options(PROBE).status_code != 429


# ───────────────────────── 6. /mcp covered ─────────────────────────


def test_mcp_mount_is_covered(limiter: TestClient) -> None:
    # /mcp carries no bearer here → unauth class, limit 3. We assert the 429
    # boundary only (never the inner status), and disable redirect-following so
    # each call is exactly one middleware pass.
    for _ in range(3):
        assert limiter.get("/mcp", follow_redirects=False).status_code != 429
    assert limiter.get("/mcp", follow_redirects=False).status_code == 429


# ───────────────────────── 7. disabled flag ─────────────────────────


def test_disabled_flag_never_throttles(client: TestClient) -> None:
    for _ in range(10):
        assert client.get(PROBE).status_code != 429


# ───────────────────────── 8. fail-open ─────────────────────────


class _NoneStore:
    """A store that is always 'unavailable' → the middleware must fail open."""

    async def incr_window(self, key: str) -> int | None:
        return None


@pytest.fixture
def failopen_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # Enter the client (which runs configure_logging, resetting root handlers)
    # in the fixture — BEFORE caplog installs its handler for the test body —
    # else the client-enter would wipe caplog's capture handler.
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_UNAUTHENTICATED_PER_MINUTE", "1")
    get_settings.cache_clear()
    monkeypatch.setattr(rate_limit, "_now", lambda: _FROZEN)
    set_store_for_testing(_NoneStore())
    with TestClient(app) as c:
        yield c


def test_fail_open_when_store_unavailable(
    failopen_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="backend.app.core.rate_limit")

    for _ in range(5):  # well past limit 1 — all allowed (fail-open)
        assert failopen_client.get(PROBE).status_code != 429

    warnings = [
        e
        for e in _structlog_events(caplog.records)
        if e.get("event") == "rate_limit_store_unavailable"
    ]
    assert len(warnings) == 1  # warn-once per window


# ───────────────────────── 9. window reset ─────────────────────────


def test_window_reset_restores_allowance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_UNAUTHENTICATED_PER_MINUTE", "2")
    get_settings.cache_clear()
    clock = [1_000_000.0]
    monkeypatch.setattr(rate_limit, "_now", lambda: clock[0])
    set_store_for_testing(InMemoryStore(clock=lambda: clock[0]))

    with TestClient(app) as c:
        assert c.get(PROBE).status_code != 429
        assert c.get(PROBE).status_code != 429
        resp = c.get(PROBE)
        assert resp.status_code == 429
        assert 1 <= resp.json()["error"]["detail"]["retry_after_seconds"] <= 60
        # Cross the window boundary → fresh allowance.
        clock[0] += 61
        assert c.get(PROBE).status_code != 429


# ───────────────────────── 10. X-Forwarded-For keying ─────────────────────────


def test_xff_last_hop_defines_the_bucket(limiter: TestClient) -> None:
    h = {"X-Forwarded-For": "9.9.9.9"}
    for _ in range(3):
        assert limiter.get(PROBE, headers=h).status_code != 429
    assert limiter.get(PROBE, headers=h).status_code == 429
    # A different last hop is a fresh, independent bucket.
    assert limiter.get(PROBE, headers={"X-Forwarded-For": "8.8.8.8"}).status_code != 429


def test_xff_spoofed_first_hop_shares_bucket_when_last_hop_matches(limiter: TestClient) -> None:
    h1 = {"X-Forwarded-For": "1.1.1.1, 9.9.9.9"}  # spoofed left, real right
    h2 = {"X-Forwarded-For": "2.2.2.2, 9.9.9.9"}
    assert limiter.get(PROBE, headers=h1).status_code != 429
    assert limiter.get(PROBE, headers=h2).status_code != 429
    assert limiter.get(PROBE, headers=h1).status_code != 429
    # 4th request on the shared last-hop bucket (UNAUTH limit = 3) → 429.
    assert limiter.get(PROBE, headers=h2).status_code == 429
