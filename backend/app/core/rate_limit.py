"""Request rate limiting on every public surface (#725, ADR 0035)."""

from __future__ import annotations

import hashlib
import ipaddress
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any, Final, Protocol

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from backend.app.core.auth import _bearer_token  # header-only bearer extractor (reused, ADR 0035)
from backend.app.core.circuit_breaker import (
    DEFAULT_OPEN_SECONDS,
    DEFAULT_TRIP_AFTER,
    CircuitBreaker,
)
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import error_envelope
from backend.app.core.logging import get_logger
from backend.app.db.models import ORCHESTRATION_PROVIDERS

if TYPE_CHECKING:
    from redis.asyncio import Redis as AsyncRedis

log = get_logger(__name__)

WINDOW_SECONDS: Final = 60
_GC_SECONDS: Final = WINDOW_SECONDS * 2  # EXPIRE horizon — pure GC, never the limit boundary

# /healthz is hardcoded (not config) — the liveness probe must never be
# throttleable. Genuine CORS preflights are exempted in the middleware.
_EXEMPT_PATHS: Final = frozenset({"/healthz"})
_WEBHOOK_PREFIX: Final = "/api/v1/orchestration/events/"
# Matched on path PREFIX before the bearer branch, so a bearer-carrying request cannot dodge the
# strict cap.
_AUTH_PREFIX: Final = "/api/v1/auth/"
# LLM feature mutations (ADR 0042) — orders of magnitude costlier than any other request
# class. The admin live-probe is the one endpoint OUTSIDE /llm that also pays for a model call.
_LLM_PREFIX: Final = "/api/v1/llm"
_LLM_EXTRA_PATHS: Final = frozenset({"/api/v1/admin/llm/test"})
_MUTATING_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Known providers get their OWN per-IP webhook bucket (#785).
_WEBHOOK_PROVIDERS: Final = frozenset(ORCHESTRATION_PROVIDERS)

# IPv4 host with a `:port` suffix (proxies sometimes append one). IPv6 has its
# own colons and is validated as-is.
_IPV4_PORT_RE: Final = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3}):\d+$")


class RateLimitStore(Protocol):
    async def incr_windows(self, keys: Sequence[str]) -> list[int] | None:
        """Increment every key and return the new counts aligned to `keys`, in
        ONE round trip. `None` = store unavailable → the middleware fails open
        for the whole batch (never a partial result).
        """
        ...


# ── Redis-backed store (production) ──────────────────────────────────────────
_redis_client: AsyncRedis[Any] | None = None


def _get_redis_client() -> AsyncRedis[Any]:
    """Lazily build the shared async Redis client. Short timeouts so a slow/down
    Redis fails fast into the fail-open path rather than stalling the request.
    """
    global _redis_client
    if _redis_client is None:
        from redis.asyncio import from_url

        _redis_client = from_url(
            get_settings().redis_url,
            socket_connect_timeout=0.5,
            socket_timeout=0.2,
        )
    return _redis_client


#: Tuned for a brownout, not an outage: ADR 0035 biases availability over
#: enforcement, so an open breaker must never be a long-lived state.
_BREAKER_TRIP_AFTER = DEFAULT_TRIP_AFTER
_BREAKER_OPEN_SECONDS = DEFAULT_OPEN_SECONDS


def _breaker_now() -> float:
    """Monotonic clock for the breaker's open window — deliberately not `_now`."""
    return time.monotonic()


#: The breaker MECHANISM is shared with `services.otp_service` (#1135); the STATE is per-instance so
#: an OTP brownout can never switch off API rate limiting, nor the reverse.
_BREAKER: Final = CircuitBreaker(
    name="rate_limit_store",
    trip_after=_BREAKER_TRIP_AFTER,
    open_seconds=_BREAKER_OPEN_SECONDS,
    clock=lambda: _breaker_now(),
)


def _breaker_is_open() -> bool:
    """True while the breaker is holding requests off Redis entirely."""
    return _BREAKER.is_open()


def _breaker_record_failure() -> None:
    _BREAKER.record_failure()


def _breaker_record_success() -> None:
    _BREAKER.record_success()


class RedisStore:
    """Fixed-window counter in Redis via a single INCR+EXPIRE pipeline."""

    async def incr_windows(self, keys: Sequence[str]) -> list[int] | None:
        if _breaker_is_open():
            # Fail open WITHOUT awaiting — stop paying the timeout per request
            # while Redis is unwell.
            return None
        try:
            pipe = _get_redis_client().pipeline(transaction=True)
            for key in keys:
                pipe.incr(key)
                pipe.expire(key, _GC_SECONDS)  # unconditional GC; the window is in the key
            results = await pipe.execute()
            # Results interleave INCR, EXPIRE per key → even-indexed entries are
            # the INCR counts, aligned to `keys`.
            counts = [int(results[i]) for i in range(0, len(results), 2)]
        except Exception:
            _breaker_record_failure()
            return None
        _breaker_record_success()
        return counts


# ── In-memory store (test-only; never an automatic fallback) ─────────────────
class InMemoryStore:
    """Process-local fixed-window counter — injected only in tests. Never an
    automatic fallback for a down Redis (that would silently fragment the limit
    per process); the production fail path is fail-open.
    """

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.time
        self._counts: dict[str, tuple[int, float]] = {}

    async def incr_windows(self, keys: Sequence[str]) -> list[int] | None:
        now = self._clock()
        # Prune on write so the dict can't grow unbounded across windows.
        stale = [k for k, (_, ts) in self._counts.items() if now - ts > _GC_SECONDS]
        for k in stale:
            del self._counts[k]
        counts: list[int] = []
        for key in keys:
            count = self._counts.get(key, (0, now))[0] + 1
            self._counts[key] = (count, now)
            counts.append(count)
        return counts


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
    warn-once stamp (mirrors the reset-hook pattern in `core/secrets.py`).
    """
    global _store_override, _redis_client, _store_unavailable_warned_window
    _store_override = None
    _redis_client = None
    _store_unavailable_warned_window = None
    _BREAKER.reset()


def _now() -> float:
    """Test-monkeypatchable time source. Wall clock — it feeds the window index
    every replica must agree on; the breaker uses `_breaker_now` (monotonic).
    """
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


def _client_ip(request: Request, trusted_hops: int) -> str:
    """The client IP for per-IP buckets."""
    xff = request.headers.get("x-forwarded-for")
    if xff and trusted_hops >= 1:
        entries = [e.strip() for e in xff.split(",")]
        if len(entries) >= trusted_hops:
            candidate = _strip_ipv4_port(entries[len(entries) - trusted_hops])
            if candidate and _is_ip(candidate):
                return candidate
    client = request.client
    if client is not None and client.host:
        return client.host
    return "unknown"


def _bucket_ip(ip: str, settings: Settings) -> str:
    """`ip` folded onto its address prefix (#789); a non-address passes through."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    prefix = (
        settings.rate_limit_ipv4_prefix if addr.version == 4 else settings.rate_limit_ipv6_prefix
    )
    return str(ipaddress.ip_network((addr, prefix), strict=False))


def _resolve_policy(
    path: str, method: str, bearer: str | None, ip: str, settings: Settings
) -> tuple[str, int, str]:
    """Resolve (class, per-minute limit, bucket key) for a request."""
    if path.startswith(_WEBHOOK_PREFIX):
        provider = path[len(_WEBHOOK_PREFIX) :].split("/", 1)[0]
        prefix = f"{provider}:" if provider in _WEBHOOK_PROVIDERS else ""
        return "webhook", settings.rate_limit_webhook_per_minute, f"{prefix}ip:{ip}"
    if path.startswith(_AUTH_PREFIX):
        return "auth", settings.rate_limit_auth_per_minute, f"ip:{ip}"
    if (path.startswith(_LLM_PREFIX) or path in _LLM_EXTRA_PATHS) and method in _MUTATING_METHODS:
        if bearer is not None:
            digest = hashlib.sha256(bearer.encode()).hexdigest()[:32]
            return "llm", settings.rate_limit_llm_per_minute, f"tok:{digest}"
        return "llm", settings.rate_limit_llm_per_minute, f"ip:{ip}"
    if bearer is not None:
        digest = hashlib.sha256(bearer.encode()).hexdigest()[:32]
        return "default", settings.rate_limit_authenticated_per_minute, f"tok:{digest}"
    return "unauth", settings.rate_limit_unauthenticated_per_minute, f"ip:{ip}"


def _warn_store_unavailable_once(window: int, *, path: str, cls: str, key: str) -> None:
    global _store_unavailable_warned_window
    if _store_unavailable_warned_window == window:
        return
    _store_unavailable_warned_window = window
    # Path only (never the full URL / query string) and the key KIND, never the key value; a
    # provider-folded webhook key still reports "ip" so log queries keyed on the documented {tok.
    log.warning(
        "rate_limit_store_unavailable",
        path=path,
        rate_limit_class=cls,
        key_kind="tok" if key.startswith("tok:") else "ip",
    )


async def rate_limit_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return await call_next(request)
    # Only a GENUINE CORS preflight (Origin + Access-Control-Request-Method) is
    # exempt; a bare OPTIONS is counted like any request.
    if (
        request.method == "OPTIONS"
        and "origin" in request.headers
        and "access-control-request-method" in request.headers
    ):
        return await call_next(request)
    path = request.url.path
    if path in _EXEMPT_PATHS:
        return await call_next(request)

    bearer = _bearer_token(request)
    # Every per-IP key site uses the PREFIX bucket (#789); the raw client
    # address is never a key input past this point.
    ip = _bucket_ip(_client_ip(request, settings.rate_limit_xff_trusted_hops), settings)
    cls, limit, key = _resolve_policy(path, request.method, bearer, ip, settings)

    now = int(_now())
    window = now // WINDOW_SECONDS

    # `default` and `webhook` add a per-IP `ipall` ceiling: rotating bearers / provider segments
    # mints fresh primary buckets but shares the ceiling.
    keys = [f"rl:{cls}:{key}:{window}"]
    if cls == "default":
        keys.append(f"rl:default:ipall:{ip}:{window}")
    elif cls == "webhook":
        keys.append(f"rl:webhook:ipall:{ip}:{window}")
    elif cls == "llm":
        # Rotated-bearer backstop (#785 class): fresh tokens mint fresh primary
        # buckets, but every model call from one IP shares this ceiling.
        keys.append(f"rl:llm:ipall:{ip}:{window}")
    counts = await _active_store().incr_windows(keys)

    if counts is None:
        # Fail-open: store unavailable → allow, warn once per window.
        _warn_store_unavailable_once(window, path=path, cls=cls, key=key)
        return await call_next(request)

    # Primary bucket checked first so it wins the reported limit when both exceed.
    exceeded_limit: int | None = None
    if counts[0] > limit:
        exceeded_limit = limit
    elif cls == "default" and counts[1] > settings.rate_limit_ip_per_minute:
        exceeded_limit = settings.rate_limit_ip_per_minute
    elif cls == "webhook" and counts[1] > settings.rate_limit_webhook_ip_per_minute:
        exceeded_limit = settings.rate_limit_webhook_ip_per_minute
    elif cls == "llm" and counts[1] > settings.rate_limit_llm_ip_per_minute:
        exceeded_limit = settings.rate_limit_llm_ip_per_minute

    if exceeded_limit is not None:
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
                "X-RateLimit-Limit": str(exceeded_limit),
                "X-RateLimit-Remaining": "0",
            },
        )
    return await call_next(request)
