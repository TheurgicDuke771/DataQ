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
    assert key == "ip:9.9.9.9"  # per-IP even though a bearer was present


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
    req = _make_request({"x-forwarded-for": "1.1.1.1, 2.2.2.2, 9.9.9.9"})
    assert _client_ip(req) == "9.9.9.9"


def test_client_ip_whitespace_tolerated() -> None:
    req = _make_request({"x-forwarded-for": "1.1.1.1 ,  9.9.9.9  "})
    assert _client_ip(req) == "9.9.9.9"


def test_client_ip_empty_last_hop_falls_back_to_peer() -> None:
    req = _make_request({"x-forwarded-for": "1.1.1.1, "}, client=("5.5.5.5", 1))
    assert _client_ip(req) == "5.5.5.5"


def test_client_ip_garbage_last_hop_falls_back_to_peer() -> None:
    req = _make_request({"x-forwarded-for": "not-an-ip"}, client=("5.5.5.5", 1))
    assert _client_ip(req) == "5.5.5.5"


def test_client_ip_no_client_is_unknown() -> None:
    req = _make_request({}, client=None)
    assert _client_ip(req) == "unknown"


def test_client_ip_strips_ipv4_port_suffix() -> None:
    req = _make_request({"x-forwarded-for": "203.0.113.7:44321"})
    assert _client_ip(req) == "203.0.113.7"


def test_client_ip_no_xff_uses_peer() -> None:
    req = _make_request({}, client=("5.5.5.5", 1))
    assert _client_ip(req) == "5.5.5.5"


# ───────────────────────── InMemoryStore ─────────────────────────


def test_in_memory_counts_within_window() -> None:
    store = InMemoryStore()
    key = "rl:unauth:ip:1.2.3.4:100"
    assert asyncio.run(store.incr_window(key)) == 1
    assert asyncio.run(store.incr_window(key)) == 2
    assert asyncio.run(store.incr_window(key)) == 3


def test_in_memory_new_window_is_fresh_count() -> None:
    now = [1000.0]
    store = InMemoryStore(clock=lambda: now[0])
    assert asyncio.run(store.incr_window("rl:unauth:ip:x:16")) == 1
    assert asyncio.run(store.incr_window("rl:unauth:ip:x:16")) == 2
    now[0] += 61  # next fixed window → different key → fresh count
    assert asyncio.run(store.incr_window("rl:unauth:ip:x:17")) == 1


def test_in_memory_prunes_stale_entries_on_write() -> None:
    now = [1000.0]
    store = InMemoryStore(clock=lambda: now[0])
    asyncio.run(store.incr_window("rl:unauth:ip:x:16"))
    now[0] += 121  # past the 2x-window GC horizon
    asyncio.run(store.incr_window("rl:unauth:ip:y:18"))
    assert "rl:unauth:ip:x:16" not in store._counts
    assert "rl:unauth:ip:y:18" in store._counts


# ───────────────────────── RedisStore fail-open ─────────────────────────


class _StubPipe:
    def incr(self, key: str) -> _StubPipe:
        return self

    def expire(self, key: str, ttl: int) -> _StubPipe:
        return self

    async def execute(self) -> list[int]:
        raise RuntimeError("redis down")


class _StubClient:
    def pipeline(self, transaction: bool = True) -> _StubPipe:
        return _StubPipe()


def test_redis_store_returns_none_when_client_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rate_limit, "_get_redis_client", lambda: _StubClient())
    assert asyncio.run(RedisStore().incr_window("rl:unauth:ip:x:1")) is None


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
