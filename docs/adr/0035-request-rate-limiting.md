# ADR 0035 — Request rate limiting on every public surface

- **Status:** Accepted
- **Date:** 2026-07-11
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0028](0028-cloud-neutral-image-runtime-config-generic-oidc.md) / [0013](0013-marketplace-distribution-and-anti-lock-in.md) (portable, cloud-neutral posture — the limiter must ride the app image, not an Azure edge), [0026](0026-auth-api-keys-and-principal-seam.md) (PATs — the authenticated `tok:` bucket), [0032](0032-email-otp-signin.md) (email OTP — this limiter is its named hard prereq, #738), [0008](0008-mcp-server.md) (the `/mcp` mount this must also cover)
- **Issue:** [#725](https://github.com/TheurgicDuke771/DataQ/issues/725)

## Context

Every public surface DataQ exposes — the REST API, the orchestration webhook
receivers, and the mounted FastMCP app at `/mcp` — is unthrottled. A single
client (a misconfigured poller, a credential-stuffing script against the token
endpoints, a runaway MCP loop) can saturate the API worker pool and the Postgres
connection budget. This is a standing gap, and it is a **named hard prerequisite
for ADR 0032's email OTP sign-in** (#738): passwordless OTP mints and verifies
codes over unauthenticated HTTP, which must not be brute-forceable.

The deployment is cloud-neutral by policy (ADR 0028 / 0013) and headed for a
local-first posture as Azure winds down, so the throttle cannot live in an
Azure-specific edge (Front Door / APIM). It must ride the app itself.

## Decision

**An app-level HTTP middleware** (`backend/app/core/rate_limit.py`), registered
on the parent FastAPI app as the **innermost** user middleware.

- **Why app-level, not nginx `limit_req` or a FastAPI dependency:** the limiter
  must be *principal-aware* (per-token vs per-IP — nginx can't hash our bearer),
  portable across ACA / compose / local (ADR 0028), and cover the **`/mcp`
  ASGI mount** — which a route `Depends(...)` cannot reach (it wraps a
  sub-application, not our routes). Parent-app middleware sees every request
  before routing, `/mcp` included.
- **Why innermost:** a 429 then exits back out through `CORSMiddleware` (so a
  cross-origin browser sees the 429, not an opaque CORS failure) and through
  `request_id_middleware` (so 429s get `X-Request-ID` + the structured request
  log).
- **Algorithm — fixed-window counter, 60s, window index in the key**
  (`rl:{cls}:{key}:{window}`). Baking the window into the key removes the
  read-modify-EXPIRE race entirely: the key simply rotates at each boundary, and
  `EXPIRE` (set unconditionally at 2× the window in the same INCR pipeline) is
  pure garbage collection, never the limit boundary. One Redis round-trip per
  request.
- **No new dependency.** `redis==5.3.1` is already in the runtime
  (`redis.asyncio`). A 30-line INCR beats adding `slowapi`/`limits` — a
  dependency, its transitive graph, and a license review (CONTRIBUTING rule 40)
  — for an algorithm this small. The store sits behind a `RateLimitStore`
  Protocol so the impl is swappable.
- **Fail-open.** Any store error returns `None` → the request is allowed, logged
  once per window (`rate_limit_store_unavailable`). A Redis outage must degrade
  to "no limiting", never to a hard-down API.
- **Endpoint classes** (per-minute, config-driven — `RATE_LIMIT_*`):

  | Class | Matches | Key | Default |
  |---|---|---|---|
  | `webhook` | `/api/v1/orchestration/events/*` | per client-IP (**even with a bearer** — machine path) | 120 |
  | `default` | any request with a bearer | per `sha256(token)[:32]` **plus** a per-IP `ipall` ceiling | 300 (token) / 1200 (`ipall`) |
  | `unauth` | everything else | per client-IP | 120 |

  The raw token is never a key input — only its hash — so it is never logged or
  stored. `/healthz` and a **genuine CORS preflight** (an `OPTIONS` carrying both
  `Origin` and `Access-Control-Request-Method`) are exempt; a bare `OPTIONS`
  is counted like any other request.

  - **Per-IP ceiling on the `default` class (`rate_limit_ip_per_minute`, default
    1200)** — the rotated-token backstop. The middleware runs *before* auth, so a
    bearer is unvalidated: an attacker cycling a fresh random `Bearer <nonce>` per
    request would otherwise mint a brand-new `tok:` bucket every time (count = 1,
    never a 429) and never be capped. The `default` class therefore increments a
    SECOND `rl:default:ipall:{ip}:{window}` bucket counting ALL bearer traffic
    from one IP; a request is throttled when the token bucket OR the `ipall`
    ceiling is exceeded (both counters move in one pipelined round trip; when both
    exceed, the 429 reports the token limit). The `webhook`/`unauth` classes stay
    single-key — each is already IP-capped on its own bucket, and dropping the
    bearer only demotes an attacker to the lower `unauth` per-IP cap. The class table is the
  **extension point** for ADR 0032's future per-email OTP class (a fourth row,
  keyed on the normalized email). **`/mcp` shares the `default` class** — it is a
  bearer-authenticated surface like the REST API, so per-token buckets apply
  uniformly; no separate policy needed.
- **Headers on the 429 only:** `Retry-After`, `X-RateLimit-Limit`,
  `X-RateLimit-Remaining: 0`, with `error_envelope("rate_limited", …,
  {"retry_after_seconds": N})`. We do not spend a header budget on every 200.
- **nginx `limit_req` is explicitly out of scope.** A connection-level zone in
  the frontend proxy is legitimate defence-in-depth, but the `http{}`-context
  zone directive has no in-repo home today (the nginx conf is generated), and it
  can't do principal-aware limits. Left as a documented future layer.

### Threat model — the client-IP rule

Per-IP buckets pick the client from `X-Forwarded-For` at a **configurable trusted
depth** (`RATE_LIMIT_XFF_TRUSTED_HOPS`, default 1). XFF is a chain — each trusted
proxy appends the peer it saw — so the genuine client is the entry
`trusted_hops` from the right; a client-supplied XFF only pollutes the *left*,
beyond the trusted depth. A single-proxy compose setup uses hops=1 (rightmost);
the **ACA ingress chain measures three appends** — public-envoy → nginx →
internal-envoy — so the deployment sets hops=3, restoring per-IP class integrity
(hops=1 would collapse every client into the shared nginx-pod IP). **Direct-hit
caveat:** XFF is trusted only for the *exact* configured depth — a chain shorter
than `trusted_hops` means the request did not traverse the expected proxy stack,
so XFF is untrustworthy and we fall back to the socket peer (never the leftmost,
most-spoofable entry). Confirm the exact prod depth against one logged live XFF
post-deploy.

## Consequences

**Positive** — every surface (REST + webhooks + `/mcp`) is throttled by one
portable seam; the OTP prereq (#738) is unblocked; no new dependency, no license
review; fail-open means a Redis blip never takes the API down.

**Accepted residual risks** —
- **Bearer rotation is now bounded by the per-IP ceiling:** a rotating attacker
  still mints a fresh `tok:` bucket per token, but every one of those requests
  also increments the `default` class's `rl:default:ipall:{ip}` bucket, so a
  single IP is capped at `rate_limit_ip_per_minute` (1200) regardless of how many
  tokens it cycles. A genuinely distributed rotation (many IPs) still needs a
  network-layer backstop, but the single-IP bypass is closed.
- **Fixed-window 2× boundary burst:** a client can send up to 2× the limit across
  a window boundary. Acceptable for abuse-prevention (vs the complexity of a
  sliding-log / token-bucket).
- **Fail-open disables limiting during a Redis outage** — a deliberate
  availability-over-enforcement trade, logged once per window.

**Follow-ups (filed from the #783 review round):** [#784](https://github.com/TheurgicDuke771/DataQ/issues/784)
(a circuit breaker for a slow-but-alive Redis, so a laggy store isn't paid per
request) and [#785](https://github.com/TheurgicDuke771/DataQ/issues/785) (key the
webhook bucket per-provider-path, not bare per-IP, so one noisy orchestrator
can't throttle another).

## Alternatives considered

- **`slowapi` / `limits` dependency** — rejected: a dep + transitive graph +
  license review for a 30-line INCR (CONTRIBUTING rule 40).
- **nginx `limit_req`** — rejected as the primary control: not principal-aware,
  no in-repo home for the zone directive; kept as a documented future layer.
- **FastAPI route dependency** — rejected: cannot cover the `/mcp` ASGI mount,
  and would have to be wired onto every router.
- **Postgres counter table** — rejected: a hot-path write per request on the
  same DB the app is trying to protect.
- **Fail-closed** — rejected: a Redis outage would take the entire API down;
  availability wins for a rate limiter.
