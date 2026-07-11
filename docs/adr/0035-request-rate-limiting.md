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
  | `webhook` | `/api/v1/orchestration/events/*` | per client-IP (**even with a bearer** — machine path) | 30 |
  | `default` | any request with a bearer | per `sha256(token)[:32]` | 300 |
  | `unauth` | everything else | per client-IP | 120 |

  The raw token is never a key input — only its hash — so it is never logged or
  stored. `/healthz` and `OPTIONS` (preflight) are exempt. The class table is the
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

Per-IP buckets trust the **rightmost** `X-Forwarded-For` hop: our nginx appends
the real peer via `$proxy_add_x_forwarded_for`, so the last entry is
proxy-added and unspoofable (a client-supplied XFF only pollutes the *left*).
**ACA double-ingress caveat:** behind two ingress hops the rightmost entry can be
an internal load-balancer IP, collapsing many clients into one per-IP bucket.
Accepted: token traffic (the `default` class, per-hash) dominates real usage, the
per-IP classes are a coarse backstop, and Azure is winding down anyway.

## Consequences

**Positive** — every surface (REST + webhooks + `/mcp`) is throttled by one
portable seam; the OTP prereq (#738) is unblocked; no new dependency, no license
review; fail-open means a Redis blip never takes the API down.

**Accepted residual risks** —
- **Bearer rotation mints fresh token buckets:** a rotating attacker gets a new
  `tok:` bucket per token, but each bad token still costs one hashed 401; the
  per-IP `unauth`/`webhook` classes and a future network-layer backstop cover
  the volumetric case.
- **Fixed-window 2× boundary burst:** a client can send up to 2× the limit across
  a window boundary. Acceptable for abuse-prevention (vs the complexity of a
  sliding-log / token-bucket).
- **Fail-open disables limiting during a Redis outage** — a deliberate
  availability-over-enforcement trade, logged once per window.

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
