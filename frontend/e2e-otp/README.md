# Email-OTP browser lane (ADR 0032, #736)

The third Playwright lane, beside `e2e/` (dev-bypass, the default CI lane) and
`e2e-live/` (opt-in smoke against the deployed app). It drives the **`otp`** auth
mode end to end: type an address → a real email is sent → read the code back →
sign in → get an HttpOnly session cookie.

It runs **fully local — no Azure, no IdP, no cloud mailbox.**

## Why it needs its own stack

Two constraints, both structural:

1. **A second api.** `backend/app/core/auth.py` picks its authenticator at _import_
   time, and OTP wins over dev-bypass. One process cannot serve both this lane and
   the dev-bypass lane, so the OTP api is a separate uvicorn on **:8100**, behind
   its own Vite dev server on **:3100**.
2. **A real SMTP server.** `OtpMailer` does SMTP + STARTTLS with
   `ssl.create_default_context()` — it verifies the certificate _and_ the
   hostname, and it authenticates. A stock MailHog/smtp4dev container satisfies
   none of that out of the box, so the lane ships its own sink
   (`backend/scripts/e2e_otp_smtp_sink.py`), which emits a throwaway self-signed
   certificate the api is pointed at via `AUTH_EMAIL_CA_BUNDLE`, and exposes
   captured codes over a small HTTP API.

The alternative — a "test mode" that hands the code straight to the test — was
rejected: it would add a bypass to a sign-in flow and would mean the lane proves a
code path production never runs. **Everything on the app side of the wire is the
shipped code**, including the STARTTLS handshake and the `Set-Cookie`.

> **Deployment note that fell out of building this, now fixed:** the mailer used
> to only speak `ssl.create_default_context()` with no way to name a private CA,
> so DataQ couldn't talk to an internal relay on one without putting that CA in
> the process-wide trust store. `AUTH_EMAIL_CA_BUNDLE` (scoped to the mailer's own
> connection) and `AUTH_EMAIL_TLS_MODE=implicit` (SMTPS `:465`) now cover both gaps
> — see [#1146](https://github.com/TheurgicDuke771/DataQ/issues/1146).

## Auth-mode injection

The mode is set per page as `window.__DATAQ_CONFIG__` in `fixtures.ts` — which is
the **production contract**: nginx renders exactly that global from `DATAQ_AUTH_*`
env (ADR 0028), and `public/config.js` is an empty stub in dev, so nothing
clobbers it. No build-time flag, no test-only code path in `src/`.

## Running it

```bash
# 1. Postgres reachable (docker compose up postgres), and a database to use.
createdb dataq_otp_e2e            # or reuse the e2e database

# 2. Backend half: SMTP sink + OTP-mode api. Blocks until both are healthy.
DATABASE_URL=postgresql+psycopg2://dataq:dataq@localhost:5432/dataq_otp_e2e \
  scripts/e2e-otp-stack.sh

# 3. The specs. Playwright starts the :3100 Vite server itself.
cd frontend && E2E_OTP=1 pnpm e2e:otp
```

`E2E_OTP=1` is what registers the project and its web server; without it the lane
is inert and `pnpm e2e` behaves exactly as before. CI sets it in the
`frontend-e2e` job.

The stack script prints a state directory holding `sink.log`, `api.log`, the pid
files and the throwaway certificate — read those first when something fails.

## Addresses

The api allow-lists the **domain** `dataq.local`, so every spec mints its own
unique address (`freshEmail()`). That is not cosmetic: the per-mailbox request cap
is active even with the rate-limit middleware off (by design — a mail-bomb control
a test harness switches off is not a control), and a shared address would exhaust
it and fail specs for a reason that has nothing to do with the UI. The cap is
_raised_ for the lane, never disabled. `otp-admin@dataq.local` is the one fixed
address, named in `WORKSPACE_ADMIN_EMAILS` so the admin-nav gating can be checked.

## What is deliberately not covered

- **A time-expired code.** It would need a 10-minute wait or a clock hack, and the
  api returns one uniform 401 for wrong / expired / used / out-of-attempts, so at
  the boundary there is nothing new to observe. `signin.spec.ts` covers the
  reachable equivalent instead — a code **superseded** by a newer request, which is
  server-side-invalidated exactly like an expired one and additionally proves that
  re-requesting kills the previous code. TTL arithmetic is backend-tested (#1134).
- **The `/auth/*` exclusion** in the axios 401 handler. A spec written for it
  passed with the exclusion removed (a wrong-code 401 can only land while the
  provider is already signed-out), so it lives where it can actually fail:
  `tests/api/client.test.ts`.
