"""Unit tests for the rate-limit primitives (#725, ADR 0035).

Pure — no TestClient / DB. Policy resolution, client-IP extraction, the two
stores, and the warn-once fail-open stamp. The middleware wired into the app is
exercised end-to-end in `backend/tests/api/test_rate_limiting.py`.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from starlette.requests import Request

from backend.app.core import rate_limit
from backend.app.core.config import Settings
from backend.app.core.rate_limit import (
    InMemoryStore,
    RedisStore,
    _client_ip,
    _resolve_policy,
    _warn_store_unavailable_once,
)


def _settings() -> Settings:
    return Settings(_env_file=None)


def _make_request(
    headers: dict[str, str] | None = None,
    client: tuple[str, int] | None = ("1.2.3.4", 5000),
) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope: dict[str, object] = {"type": "http", "headers": raw, "client": client}
    return Request(scope)


# ───────────────────────── policy resolution ─────────────────────────


def test_webhook_prefix_wins_even_with_bearer() -> None:
    s = _settings()
    cls, limit, key = _resolve_policy("/api/v1/orchestration/events/adf", "sometoken", "9.9.9.9", s)
    assert cls == "webhook"
    assert limit == s.rate_limit_webhook_per_minute
    assert key == "adf:ip:9.9.9.9"  # per-IP even though a bearer was present


@pytest.mark.parametrize("provider", sorted(rate_limit._WEBHOOK_PROVIDERS))
def test_webhook_key_folds_known_provider(provider: str) -> None:
    # Each provider gets its own per-IP bucket (#785), so a burst on one can't
    # crowd out another's callbacks from the same egress IP. Parametrized over
    # the production set so a newly added provider is exercised automatically.
    _, _, key = _resolve_policy(
        f"/api/v1/orchestration/events/{provider}", None, "9.9.9.9", _settings()
    )
    assert key == f"{provider}:ip:9.9.9.9"


def test_webhook_key_trailing_path_still_folds_provider() -> None:
    _, _, key = _resolve_policy(
        "/api/v1/orchestration/events/adf/extra", None, "9.9.9.9", _settings()
    )
    assert key == "adf:ip:9.9.9.9"


@pytest.mark.parametrize("segment", ["nonesuch", "", "adf\x00", "ADF"])
def test_webhook_unknown_segment_shares_bare_ip_bucket(segment: str) -> None:
    # NB: ASGI hands the middleware the percent-DECODED path, so an encoded
    # probe like `adf%00` arrives here as the raw "adf\x00" — test that layer.
    # Unknown segments must NOT mint fresh buckets (a scanner rotating the path
    # would never 429) — they all share the bare per-IP bucket.
    _, _, key = _resolve_policy(
        f"/api/v1/orchestration/events/{segment}", None, "9.9.9.9", _settings()
    )
    assert key == "ip:9.9.9.9"


def test_webhook_providers_match_orchestration_registry() -> None:
    # `_WEBHOOK_PROVIDERS` derives from the shared `db.models.ORCHESTRATION_PROVIDERS`
    # vocabulary; this pins that vocabulary to the orchestration registry so a
    # provider registered there can't silently land in the shared bare-IP bucket.
    from backend.app.orchestration.registry import _PROVIDERS

    assert rate_limit._WEBHOOK_PROVIDERS == frozenset(_PROVIDERS)


def test_bearer_keys_by_sha256_prefix() -> None:
    s = _settings()
    cls, limit, key = _resolve_policy("/api/v1/suites", "sometoken", "9.9.9.9", s)
    assert cls == "default"
    assert limit == s.rate_limit_authenticated_per_minute
    assert key.startswith("tok:")
    digest = key.removeprefix("tok:")
    assert len(digest) == 32
    assert all(c in "0123456789abcdef" for c in digest)
    assert digest == hashlib.sha256(b"sometoken").hexdigest()[:32]


def test_raw_token_never_in_key() -> None:
    _, _, key = _resolve_policy("/api/v1/suites", "super-secret-token", "9.9.9.9", _settings())
    assert "super-secret-token" not in key


def test_no_bearer_keys_by_ip() -> None:
    s = _settings()
    cls, limit, key = _resolve_policy("/api/v1/suites", None, "9.9.9.9", s)
    assert cls == "unauth"
    assert limit == s.rate_limit_unauthenticated_per_minute
    assert key == "ip:9.9.9.9"


# ───────────────────────── client-IP extraction ─────────────────────────


def test_client_ip_multi_hop_takes_last() -> None:
    # hops=1 (default) → the rightmost entry.
    req = _make_request({"x-forwarded-for": "1.1.1.1, 2.2.2.2, 9.9.9.9"})
    assert _client_ip(req, 1) == "9.9.9.9"


def test_client_ip_whitespace_tolerated() -> None:
    req = _make_request({"x-forwarded-for": "1.1.1.1 ,  9.9.9.9  "})
    assert _client_ip(req, 1) == "9.9.9.9"


def test_client_ip_empty_last_hop_falls_back_to_peer() -> None:
    req = _make_request({"x-forwarded-for": "1.1.1.1, "}, client=("5.5.5.5", 1))
    assert _client_ip(req, 1) == "5.5.5.5"


def test_client_ip_garbage_last_hop_falls_back_to_peer() -> None:
    req = _make_request({"x-forwarded-for": "not-an-ip"}, client=("5.5.5.5", 1))
    assert _client_ip(req, 1) == "5.5.5.5"


def test_client_ip_no_client_is_unknown() -> None:
    req = _make_request({}, client=None)
    assert _client_ip(req, 1) == "unknown"


def test_client_ip_strips_ipv4_port_suffix() -> None:
    req = _make_request({"x-forwarded-for": "203.0.113.7:44321"})
    assert _client_ip(req, 1) == "203.0.113.7"


def test_client_ip_no_xff_uses_peer() -> None:
    req = _make_request({}, client=("5.5.5.5", 1))
    assert _client_ip(req, 1) == "5.5.5.5"


# ───────────────────────── client-IP trusted-hops ─────────────────────────


def test_client_ip_hops_3_picks_client_at_left() -> None:
    # Chain = client, public-envoy, nginx, internal-envoy appends → 3 trusted
    # appends means the real client is the 3rd-from-right = entries[0] here.
    req = _make_request({"x-forwarded-for": "7.7.7.7, 10.0.0.1, 10.0.0.2"})
    assert _client_ip(req, 3) == "7.7.7.7"


def test_client_ip_hops_3_ignores_spoofed_left_entries() -> None:
    # A client can prepend extra LEFT hops, but at the correct depth the picked
    # entry is still the genuine client-supplied one 3-from-right, not the spoof.
    req = _make_request({"x-forwarded-for": "1.2.3.4, 5.6.7.8, 7.7.7.7, 10.0.0.1, 10.0.0.2"})
    assert _client_ip(req, 3) == "7.7.7.7"


def test_client_ip_chain_shorter_than_hops_falls_back_to_peer() -> None:
    # Only 2 entries but 3 hops expected → chain didn't traverse the trusted
    # stack → fall back to the socket peer, NOT entries[0].
    req = _make_request({"x-forwarded-for": "1.1.1.1, 2.2.2.2"}, client=("5.5.5.5", 1))
    assert _client_ip(req, 3) == "5.5.5.5"


# ───────────────────────── InMemoryStore ─────────────────────────


def test_in_memory_counts_within_window() -> None:
    store = InMemoryStore()
    key = "rl:unauth:ip:1.2.3.4:100"
    assert asyncio.run(store.incr_windows([key])) == [1]
    assert asyncio.run(store.incr_windows([key])) == [2]
    assert asyncio.run(store.incr_windows([key])) == [3]


def test_in_memory_new_window_is_fresh_count() -> None:
    now = [1000.0]
    store = InMemoryStore(clock=lambda: now[0])
    assert asyncio.run(store.incr_windows(["rl:unauth:ip:x:16"])) == [1]
    assert asyncio.run(store.incr_windows(["rl:unauth:ip:x:16"])) == [2]
    now[0] += 61  # next fixed window → different key → fresh count
    assert asyncio.run(store.incr_windows(["rl:unauth:ip:x:17"])) == [1]


def test_in_memory_prunes_stale_entries_on_write() -> None:
    now = [1000.0]
    store = InMemoryStore(clock=lambda: now[0])
    asyncio.run(store.incr_windows(["rl:unauth:ip:x:16"]))
    now[0] += 121  # past the 2x-window GC horizon
    asyncio.run(store.incr_windows(["rl:unauth:ip:y:18"]))
    assert "rl:unauth:ip:x:16" not in store._counts
    assert "rl:unauth:ip:y:18" in store._counts


def test_in_memory_multi_key_increments_independently_aligned() -> None:
    store = InMemoryStore()
    a, b = "rl:default:tok:aaa:5", "rl:default:ipall:1.2.3.4:5"
    # First batch: both fresh → [1, 1].
    assert asyncio.run(store.incr_windows([a, b])) == [1, 1]
    # Bump only `a` once via its own batch, then a joint batch: a=3, b=2.
    assert asyncio.run(store.incr_windows([a])) == [2]
    assert asyncio.run(store.incr_windows([a, b])) == [3, 2]


# ───────────────────────── RedisStore fail-open ─────────────────────────


class _StubPipe:
    def __init__(self, *, fail: bool) -> None:
        self._fail = fail
        self._calls = 0  # number of INCR pushes → drives the fake interleaved result

    def incr(self, key: str) -> _StubPipe:
        self._calls += 1
        return self

    def expire(self, key: str, ttl: int) -> _StubPipe:
        return self

    async def execute(self) -> list[int]:
        if self._fail:
            raise RuntimeError("redis down")
        # Mimic Redis: [INCR, EXPIRE] per key → even indices are the counts.
        out: list[int] = []
        for _ in range(self._calls):
            out.extend([1, 1])
        return out


class _StubClient:
    def __init__(self, *, fail: bool) -> None:
        self._fail = fail

    def pipeline(self, transaction: bool = True) -> _StubPipe:
        return _StubPipe(fail=self._fail)


def test_redis_store_returns_none_when_client_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rate_limit, "_get_redis_client", lambda: _StubClient(fail=True))
    assert asyncio.run(RedisStore().incr_windows(["rl:unauth:ip:x:1"])) is None


def test_redis_store_returns_aligned_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rate_limit, "_get_redis_client", lambda: _StubClient(fail=False))
    # Two keys → two even-indexed INCR results extracted from the interleaved pipe.
    assert asyncio.run(RedisStore().incr_windows(["a:1", "b:1"])) == [1, 1]


# ───────────────────────── warn-once fail-open stamp ─────────────────────────


class _RecordingLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def warning(self, event: str, **kwargs: object) -> None:
        self.events.append((event, kwargs))


def test_warn_store_unavailable_once_per_window(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _RecordingLogger()
    monkeypatch.setattr(rate_limit, "log", rec)

    _warn_store_unavailable_once(100, path="/api/v1/x", cls="unauth", key="ip:1.2.3.4")
    _warn_store_unavailable_once(100, path="/api/v1/x", cls="unauth", key="ip:1.2.3.4")
    assert len(rec.events) == 1
    event, fields = rec.events[0]
    assert event == "rate_limit_store_unavailable"
    assert fields["key_kind"] == "ip"  # kind only — never the key value
    assert "1.2.3.4" not in str(fields)

    # A new window warns again.
    _warn_store_unavailable_once(101, path="/api/v1/x", cls="unauth", key="ip:1.2.3.4")
    assert len(rec.events) == 2

    # A provider-folded webhook key (#785) still reports kind "ip" — the field's
    # value domain is pinned to {tok, ip} so log queries keyed on it keep matching.
    _warn_store_unavailable_once(102, path="/api/v1/x", cls="webhook", key="adf:ip:1.2.3.4")
    assert rec.events[2][1]["key_kind"] == "ip"
    _warn_store_unavailable_once(103, path="/api/v1/x", cls="default", key="tok:abcd")
    assert rec.events[3][1]["key_kind"] == "tok"
