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

The `default` (bearer) class carries a dual-key ceiling: besides the per-token
bucket it also increments a per-IP `ipall` bucket counting ALL bearer traffic
from one IP, so an attacker rotating a fresh random `Bearer <nonce>` per request
(which would otherwise mint a fresh `tok:` bucket every time and never 429) is
still capped by `rate_limit_ip_per_minute`. The `webhook` class carries the same
dual-key shape (#785): its primary bucket is per provider + IP (so one noisy
orchestrator can't crowd out another's callbacks), and a per-IP `ipall` ceiling
(`rate_limit_webhook_ip_per_minute`) bounds the aggregate one IP can spend
across provider buckets. Both counters move in one pipelined round trip.

"Per-IP" everywhere above means per address PREFIX (#789 — IPv4 /24, IPv6 /64
by default, configurable): keying on the full address lets a rotating NAT/proxy
pool spread a burst across sibling addresses so no bucket ever fills. See
`_bucket_ip`.
"""

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
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import error_envelope
from backend.app.core.logging import get_logger
from backend.app.db.models import ORCHESTRATION_PROVIDERS

if TYPE_CHECKING:
    from redis.asyncio import Redis as AsyncRedis

log = get_logger(__name__)

WINDOW_SECONDS: Final = 60
_GC_SECONDS: Final = WINDOW_SECONDS * 2  # EXPIRE horizon — pure GC, never the limit boundary

# Exempt surfaces. /healthz is hardcoded (not config) — the liveness probe must
# never be throttleable. A genuine CORS preflight (OPTIONS carrying Origin +
# Access-Control-Request-Method) is exempted in the middleware; a bare OPTIONS is
# counted like any other request.
_EXEMPT_PATHS: Final = frozenset({"/healthz"})
_WEBHOOK_PREFIX: Final = "/api/v1/orchestration/events/"

# The provider segments that get their OWN per-IP webhook bucket (#785), so a
# burst from one provider can't crowd out another's callbacks when both egress
# through the same IP. Sourced from the shared provider vocabulary in `db.models`
# (already a transitive dependency via `core.auth`); a sync test pins it to the
# orchestration registry. An UNKNOWN segment falls into the shared bare-IP
# bucket: folding arbitrary path segments into the key would let a scanner mint
# a fresh bucket per request and never 429.
_WEBHOOK_PROVIDERS: Final = frozenset(ORCHESTRATION_PROVIDERS)

# An IPv4 host with a `:port` suffix (proxies sometimes append one). IPv6 is left
# untouched (it has its own colons) and validated as-is.
_IPV4_PORT_RE: Final = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3}):\d+$")


class RateLimitStore(Protocol):
    async def incr_windows(self, keys: Sequence[str]) -> list[int] | None:
        """Increment every key in `keys` and return the new counts, aligned to
        `keys` order, in ONE round trip.

        `None` signals the store is unavailable → the middleware fails open for
        the whole batch (a single fail-open signal, never a partial result).
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

    async def incr_windows(self, keys: Sequence[str]) -> list[int] | None:
        try:
            pipe = _get_redis_client().pipeline(transaction=True)
            for key in keys:
                pipe.incr(key)
                pipe.expire(key, _GC_SECONDS)  # unconditional GC; the window is in the key
            results = await pipe.execute()
            # Results interleave INCR, EXPIRE per key → the even-indexed entries
            # are the INCR counts, aligned to `keys`.
            return [int(results[i]) for i in range(0, len(results), 2)]
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

    async def incr_windows(self, keys: Sequence[str]) -> list[int] | None:
        now = self._clock()
        # Prune on write: drop entries older than the GC horizon so the dict can't
        # grow unbounded across windows.
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


def _client_ip(request: Request, trusted_hops: int) -> str:
    """The client IP for per-IP buckets.

    X-Forwarded-For is a comma-separated chain — each trusted proxy appends the
    peer it saw. Exactly `trusted_hops` proxies are trusted to append (config
    `RATE_LIMIT_XFF_TRUSTED_HOPS`), so the real client is the entry
    `trusted_hops` from the right (`entries[len - hops]`). Picking a fixed depth
    from the right is what survives a multi-proxy chain: a single-proxy compose
    setup uses hops=1 (rightmost), while the ACA public-envoy→nginx→internal-
    envoy chain uses hops=3.

    If the chain is SHORTER than `trusted_hops`, the request did not traverse the
    expected proxy stack, so the whole XFF header is untrustworthy → fall back to
    the socket peer (never `entries[0]`, the most spoofable position). Whitespace
    is tolerated and an IPv4 `:port` suffix stripped; a garbage candidate or a
    missing peer falls back to `"unknown"`.
    """
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
    """The per-IP bucket key component: `ip` folded onto its address prefix (#789).

    Keying per full /32 dilutes the cap against a rotating NAT/proxy pool — the
    pool spreads a burst across many sibling addresses so no single bucket ever
    fills (observed live: 200 requests over 11 distinct IPs in one /24, none near
    the cap). Folding onto a configurable prefix (IPv4 `rate_limit_ipv4_prefix`,
    default /24; IPv6 `rate_limit_ipv6_prefix`, default /64 — one subscriber's
    standard allocation) makes the whole pool share one bucket. The prefix length
    rides in the key, so retuning the mask starts fresh buckets rather than
    cross-counting. A non-address (`"unknown"`) passes through unchanged.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    prefix = (
        settings.rate_limit_ipv4_prefix
        if isinstance(addr, ipaddress.IPv4Address)
        else settings.rate_limit_ipv6_prefix
    )
    network = ipaddress.ip_network((addr, prefix), strict=False)
    return f"{network.network_address}/{prefix}"


def _resolve_policy(
    path: str, bearer: str | None, ip: str, settings: Settings
) -> tuple[str, int, str]:
    """Resolve (class, per-minute limit, bucket key) for a request.

    Order matters: the webhook (machine) path is keyed per-IP EVEN when a bearer
    is present, so an orchestrator's callbacks share one bucket regardless of any
    token they carry. A known provider segment (adf/airflow/dbt) is folded into
    the webhook key so each provider gets an independent per-IP bucket (#785).
    Otherwise a bearer buckets per sha256(token) and the unauthenticated path per
    client-IP. The raw token is never used as a key — only its hash — so it is
    never logged or stored.
    """
    if path.startswith(_WEBHOOK_PREFIX):
        provider = path[len(_WEBHOOK_PREFIX) :].split("/", 1)[0]
        prefix = f"{provider}:" if provider in _WEBHOOK_PROVIDERS else ""
        return "webhook", settings.rate_limit_webhook_per_minute, f"{prefix}ip:{ip}"
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
    # never the key value. The kind's value domain is pinned to {tok, ip} — a
    # provider-folded webhook key (`adf:ip:…`, #785) still reports "ip" so log
    # queries keyed on the documented domain keep matching.
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
    # Only a GENUINE CORS preflight is exempt — OPTIONS carrying both Origin and
    # Access-Control-Request-Method. A bare OPTIONS is counted like any request.
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
    # Every per-IP key site (policy key + the ipall ceilings) uses the PREFIX
    # bucket (#789), so a NAT/proxy pool rotating sibling addresses can't dilute
    # the cap. The raw client address is never a key input past this point.
    ip = _bucket_ip(_client_ip(request, settings.rate_limit_xff_trusted_hops), settings)
    cls, limit, key = _resolve_policy(path, bearer, ip, settings)

    now = int(_now())
    window = now // WINDOW_SECONDS

    # The `default` (bearer) class gets a SECOND per-IP `ipall` bucket that counts
    # all bearer traffic from one IP, closing the rotated-token bypass (a fresh
    # random bearer mints a fresh tok bucket but shares the ipall ceiling). The
    # `webhook` class gets the same dual-key shape (#785): its primary bucket is
    # per provider + IP, so without an `ipall` ceiling one IP could spend
    # (providers + 1) x the webhook cap by rotating the segment. The unauth class
    # stays single-key — it is already per-IP on its one bucket.
    keys = [f"rl:{cls}:{key}:{window}"]
    if cls == "default":
        keys.append(f"rl:default:ipall:{ip}:{window}")
    elif cls == "webhook":
        keys.append(f"rl:webhook:ipall:{ip}:{window}")
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
