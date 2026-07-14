"""End-to-end rate-limit tests over the wired app (#725, ADR 0035).

TestClient over `backend.app.main.app` — no DB fixtures needed: the limiter
counts *before* routing, so 404/401/whatever from the inner route suffices (we
only assert the 429 boundary, never the inner status). The `limiter` fixture
enables limiting with low limits + an injected in-memory store and freezes time
so a window boundary can't flake the counts.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence

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


@pytest.fixture
def ip_ceiling_limiter(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Low per-IP bearer ceiling, high token cap — exercises the rotated-token
    backstop (`rate_limit_ip_per_minute`) without the per-token bucket firing."""
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_AUTHENTICATED_PER_MINUTE", "100")  # token cap out of the way
    monkeypatch.setenv("RATE_LIMIT_IP_PER_MINUTE", "4")  # per-IP ceiling across all tokens
    monkeypatch.setenv("RATE_LIMIT_WEBHOOK_PER_MINUTE", "50")
    get_settings.cache_clear()
    monkeypatch.setattr(rate_limit, "_now", lambda: _FROZEN)
    set_store_for_testing(InMemoryStore(clock=lambda: _FROZEN))
    with TestClient(app) as c:
        yield c


def test_rotating_bearer_hits_ip_ceiling(ip_ceiling_limiter: TestClient) -> None:
    # A fresh random bearer each request → a fresh tok bucket every time, so the
    # per-token cap never fires; the per-IP ipall ceiling (4) closes the bypass.
    for i in range(4):
        h = {"Authorization": f"Bearer rotate-{i}"}
        assert ip_ceiling_limiter.get(PROBE, headers=h).status_code != 429
    resp = ip_ceiling_limiter.get(PROBE, headers={"Authorization": "Bearer rotate-final"})
    assert resp.status_code == 429
    assert resp.headers["X-RateLimit-Limit"] == "4"  # the IP ceiling, reported


def test_two_distinct_tokens_share_ip_ceiling(ip_ceiling_limiter: TestClient) -> None:
    ha = {"Authorization": "Bearer token-AAA"}
    hb = {"Authorization": "Bearer token-BBB"}
    assert ip_ceiling_limiter.get(PROBE, headers=ha).status_code != 429
    assert ip_ceiling_limiter.get(PROBE, headers=hb).status_code != 429
    assert ip_ceiling_limiter.get(PROBE, headers=ha).status_code != 429
    assert ip_ceiling_limiter.get(PROBE, headers=hb).status_code != 429  # 4 total, at ceiling
    # 5th bearer request from the same IP (either token) → ipall ceiling 429.
    assert ip_ceiling_limiter.get(PROBE, headers=ha).status_code == 429


def test_webhook_with_bearer_not_counted_against_ip_ceiling(ip_ceiling_limiter: TestClient) -> None:
    path = "/api/v1/orchestration/events/adf"
    h = {"Authorization": "Bearer token-AAA"}
    # 6 webhook posts > the ipall ceiling (4); they stay in the webhook class
    # (cap 50) and never touch ipall, so none are throttled.
    for _ in range(6):
        assert ip_ceiling_limiter.post(path, headers=h).status_code != 429
    # And a subsequent bearer request is ipall #1, not #7 → still allowed.
    assert ip_ceiling_limiter.get(PROBE, headers=h).status_code != 429


def test_single_token_still_capped_at_authenticated_limit(limiter: TestClient) -> None:
    # Default `limiter`: AUTH cap 5, IP ceiling default 1200 (out of the way).
    # The per-token bucket stays intact and independent of the higher ceiling.
    h = {"Authorization": "Bearer solo-token"}
    for _ in range(5):
        assert limiter.get(PROBE, headers=h).status_code != 429
    resp = limiter.get(PROBE, headers=h)
    assert resp.status_code == 429
    assert resp.headers["X-RateLimit-Limit"] == "5"  # the token limit, not the ceiling


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


def test_webhook_burst_on_one_provider_does_not_throttle_another(limiter: TestClient) -> None:
    # #785: same source IP, but each provider has its own bucket — an adf burst
    # past the limit must not 429 airflow's or dbt's callbacks.
    adf = "/api/v1/orchestration/events/adf"
    for _ in range(2):  # WEBHOOK limit = 2
        assert limiter.post(adf).status_code != 429
    assert limiter.post(adf).status_code == 429
    assert limiter.post("/api/v1/orchestration/events/airflow").status_code != 429
    assert limiter.post("/api/v1/orchestration/events/dbt").status_code != 429


def test_webhook_unknown_segments_share_one_bucket(limiter: TestClient) -> None:
    # Rotating an unknown segment must not mint fresh buckets — all such requests
    # land in the shared bare-IP webhook bucket and 429 together.
    for i in range(2):  # WEBHOOK limit = 2
        assert limiter.post(f"/api/v1/orchestration/events/scan-{i}").status_code != 429
    assert limiter.post("/api/v1/orchestration/events/scan-99").status_code == 429


# ───────────────────────── 4/5. exemptions ─────────────────────────


def test_healthz_is_exempt(limiter: TestClient) -> None:
    for _ in range(20):
        assert limiter.get("/healthz").status_code == 200


def test_options_preflight_is_exempt(limiter: TestClient) -> None:
    # A GENUINE preflight carries Origin + Access-Control-Request-Method.
    preflight = {"Origin": "https://app.example.com", "Access-Control-Request-Method": "GET"}
    for _ in range(10):  # well past UNAUTH limit = 3
        assert limiter.options(PROBE, headers=preflight).status_code != 429


def test_bare_options_is_counted(limiter: TestClient) -> None:
    # OPTIONS without the preflight headers is NOT a CORS preflight → counted
    # in the normal (unauth) class and throttleable at limit 3.
    for _ in range(3):
        assert limiter.options(PROBE).status_code != 429
    assert limiter.options(PROBE).status_code == 429


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

    async def incr_windows(self, keys: Sequence[str]) -> list[int] | None:
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
