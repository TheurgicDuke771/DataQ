"""Request rate limiting on every public surface (#725, ADR 0035).

One HTTP middleware, registered on the parent FastAPI app as the *innermost*
user middleware (see `backend/app/main.py`), so a 429 still exits through
CORSMiddleware (cross-origin browsers see the 429, not an opaque CORS error)
and through `request_id_middleware` (429s get `X-Request-ID` + the structured
request log). Being on the parent app — not a route dependency — is what lets it
also cover the mounted FastMCP sub-app at `/mcp`.

Algorithm: a fixed-window counter (60s). The window index is baked *into* the
Redis key (`rl:{cls}:{key}:{window}`), so there is no read-modify-EXPIRE race —
the key simply changes at each window boundary, and EXPIRE is pure garbage
collection at 2x the window. Counting is one round-trip (INCR + EXPIRE pipeline).

Fail-open: any store error → the request is allowed (a Redis outage disables
limiting, logged once per window — never a hard-down on the whole API).
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Final, Protocol

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from backend.app.core.auth import _bearer_token  # header-only bearer extractor (reused, ADR 0035)
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import error_envelope
from backend.app.core.logging import get_logger

if TYPE_CHECKING:
    from redis.asyncio import Redis as AsyncRedis

log = get_logger(__name__)

WINDOW_SECONDS: Final = 60
_GC_SECONDS: Final = WINDOW_SECONDS * 2  # EXPIRE horizon — pure GC, never the limit boundary

# Exempt surfaces. /healthz is hardcoded (not config) — the liveness probe must
# never be throttleable. OPTIONS is handled in the middleware (CORS preflight).
_EXEMPT_PATHS: Final = frozenset({"/healthz"})
_WEBHOOK_PREFIX: Final = "/api/v1/orchestration/events/"

# An IPv4 host with a `:port` suffix (proxies sometimes append one). IPv6 is left
# untouched (it has its own colons) and validated as-is.
_IPV4_PORT_RE: Final = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3}):\d+$")


class RateLimitStore(Protocol):
    async def incr_window(self, key: str) -> int | None:
        """Increment the counter for `key` in its window and return the new count.

        `None` signals the store is unavailable → the middleware fails open.
        """
        ...


# ── Redis-backed store (production) ──────────────────────────────────────────
_redis_client: AsyncRedis[Any] | None = None


def _get_redis_client() -> AsyncRedis[Any]:
    """Lazily build the shared async Redis client. Short timeouts so a slow/down
    Redis fails fast into the fail-open path rather than stalling the request."""
    global _redis_client
    if _redis_client is None:
        from redis.asyncio import from_url

        _redis_client = from_url(
            get_settings().redis_url,
            socket_connect_timeout=0.5,
            socket_timeout=0.2,
        )
    return _redis_client


class RedisStore:
    """Fixed-window counter in Redis via a single INCR+EXPIRE pipeline.

    Stateless (the client is module-level). ANY exception → `None`, the
    fail-open signal — a Redis hiccup must never 500 or block the request.
    """

    async def incr_window(self, key: str) -> int | None:
        try:
            pipe = _get_redis_client().pipeline(transaction=True)
            pipe.incr(key)
            pipe.expire(key, _GC_SECONDS)  # unconditional GC; the window is in the key
            results = await pipe.execute()
            return int(results[0])
        except Exception:
            return None


# ── In-memory store (test-only; never an automatic fallback) ─────────────────
class InMemoryStore:
    """Process-local fixed-window counter — injected only in tests.

    Never used as an automatic fallback for a down Redis (that would silently
    per-process-fragment the limit); the production fail path is fail-open.
    """

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.time
        self._counts: dict[str, tuple[int, float]] = {}

    async def incr_window(self, key: str) -> int | None:
        now = self._clock()
        # Prune on write: drop entries older than the GC horizon so the dict can't
        # grow unbounded across windows.
        stale = [k for k, (_, ts) in self._counts.items() if now - ts > _GC_SECONDS]
        for k in stale:
            del self._counts[k]
        count = self._counts.get(key, (0, now))[0] + 1
        self._counts[key] = (count, now)
        return count


# ── Store selection + test/reset hooks ───────────────────────────────────────
_REDIS_STORE: Final = RedisStore()
_store_override: RateLimitStore | None = None
# Warn-once-per-window stamp for the fail-open path (avoid a log flood on outage).
_store_unavailable_warned_window: int | None = None


def _active_store() -> RateLimitStore:
    return _store_override if _store_override is not None else _REDIS_STORE


def set_store_for_testing(store: RateLimitStore | None) -> None:
    """Test hook: inject a store (e.g. `InMemoryStore`) or clear the override."""
    global _store_override
    _store_override = store


def reset_rate_limit_state() -> None:
    """Test hook: clear the store override, the lazy Redis client, and the
    warn-once stamp (mirrors the reset-hook pattern in `core/secrets.py`)."""
    global _store_override, _redis_client, _store_unavailable_warned_window
    _store_override = None
    _redis_client = None
    _store_unavailable_warned_window = None


def _now() -> float:
    """Indirection so tests can monkeypatch the middleware's time source."""
    return time.time()


# ── Policy + client-IP resolution ────────────────────────────────────────────
def _strip_ipv4_port(host: str) -> str:
    match = _IPV4_PORT_RE.match(host)
    return match.group(1) if match else host


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _client_ip(request: Request) -> str:
    """The client IP for per-IP buckets.

    Trust the LAST (rightmost) X-Forwarded-For hop: our nginx appends the real
    peer via `$proxy_add_x_forwarded_for`, so the rightmost entry is
    proxy-added and unspoofable (a client-supplied XFF only pollutes the *left*).
    Whitespace is tolerated and an IPv4 `:port` suffix stripped; an empty/garbage
    last hop falls back to the socket peer, else `"unknown"`.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        candidate = _strip_ipv4_port(xff.split(",")[-1].strip())
        if candidate and _is_ip(candidate):
            return candidate
    client = request.client
    if client is not None and client.host:
        return client.host
    return "unknown"


def _resolve_policy(
    path: str, bearer: str | None, ip: str, settings: Settings
) -> tuple[str, int, str]:
    """Resolve (class, per-minute limit, bucket key) for a request.

    Order matters: the webhook (machine) path is keyed per-IP EVEN when a bearer
    is present, so an orchestrator's callbacks share one bucket regardless of any
    token they carry. Otherwise a bearer buckets per sha256(token) and the
    unauthenticated path per client-IP. The raw token is never used as a key —
    only its hash — so it is never logged or stored.
    """
    if path.startswith(_WEBHOOK_PREFIX):
        return "webhook", settings.rate_limit_webhook_per_minute, f"ip:{ip}"
    if bearer is not None:
        digest = hashlib.sha256(bearer.encode()).hexdigest()[:32]
        return "default", settings.rate_limit_authenticated_per_minute, f"tok:{digest}"
    return "unauth", settings.rate_limit_unauthenticated_per_minute, f"ip:{ip}"


def _warn_store_unavailable_once(window: int, *, path: str, cls: str, key: str) -> None:
    global _store_unavailable_warned_window
    if _store_unavailable_warned_window == window:
        return
    _store_unavailable_warned_window = window
    # Path only (never request.url / query string), class, and key KIND (tok/ip) —
    # never the key value.
    log.warning(
        "rate_limit_store_unavailable",
        path=path,
        rate_limit_class=cls,
        key_kind=key.split(":", 1)[0],
    )


async def rate_limit_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return await call_next(request)
    # CORS preflight carries no credentials and must not be throttled.
    if request.method == "OPTIONS":
        return await call_next(request)
    path = request.url.path
    if path in _EXEMPT_PATHS:
        return await call_next(request)

    bearer = _bearer_token(request)
    ip = _client_ip(request)
    cls, limit, key = _resolve_policy(path, bearer, ip, settings)

    now = int(_now())
    window = now // WINDOW_SECONDS
    count = await _active_store().incr_window(f"rl:{cls}:{key}:{window}")

    if count is None:
        # Fail-open: store unavailable → allow, warn once per window.
        _warn_store_unavailable_once(window, path=path, cls=cls, key=key)
        return await call_next(request)

    if count > limit:
        retry_after = max(1, (window + 1) * WINDOW_SECONDS - now)
        return JSONResponse(
            status_code=429,
            content=error_envelope(
                "rate_limited",
                "Too many requests",
                {"retry_after_seconds": retry_after},
            ),
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
            },
        )
    return await call_next(request)
