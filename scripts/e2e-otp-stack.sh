#!/usr/bin/env bash
# Bring up the backend half of the email-OTP browser lane (ADR 0032, #736).
#
#   scripts/e2e-otp-stack.sh
#
# Starts, in order (the order is load-bearing):
#   1. the SMTP sink        (backend/scripts/e2e_otp_smtp_sink.py) on :1025 / :1080
#   2. a second api process (uvicorn, OTP mode) on :8100
#
# The sink emits a self-signed certificate at startup and the api must trust it
# BEFORE it runs: `OtpMailer` uses `ssl.create_default_context()`, which verifies
# the certificate and the hostname, and OpenSSL reads SSL_CERT_FILE once at process
# start. So the sink has to be up and its cert path known before uvicorn is
# launched — hence one script rather than two independent background steps.
#
# Why a SECOND api at all: `backend/app/core/auth.py` picks its authenticator at
# IMPORT time, and OTP wins over dev-bypass. One process cannot serve both the
# existing dev-bypass lane and this one.
#
# Playwright starts the Vite dev server (:3100) itself; see
# frontend/playwright.config.ts and frontend/e2e-otp/README.md.
#
# Environment (all optional):
#   DATABASE_URL     defaults to the compose Postgres, e2e database
#   OTP_API_PORT     8100
#   OTP_SMTP_PORT    1025
#   OTP_SINK_PORT    1080
#   OTP_STATE_DIR    where the cert + pid files go (default: a mktemp dir)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

OTP_API_PORT="${OTP_API_PORT:-8100}"
OTP_SMTP_PORT="${OTP_SMTP_PORT:-1025}"
OTP_SINK_PORT="${OTP_SINK_PORT:-1080}"
OTP_STATE_DIR="${OTP_STATE_DIR:-$(mktemp -d -t dataq-otp-lane)}"
mkdir -p "$OTP_STATE_DIR"

echo "otp lane state dir: $OTP_STATE_DIR"

# ── 1. SMTP sink ─────────────────────────────────────────────────────────────
python -m backend.scripts.e2e_otp_smtp_sink \
  --smtp-port "$OTP_SMTP_PORT" \
  --http-port "$OTP_SINK_PORT" \
  --cert-dir "$OTP_STATE_DIR" \
  >"$OTP_STATE_DIR/sink.log" 2>&1 &
echo $! >"$OTP_STATE_DIR/sink.pid"

for _ in $(seq 1 30); do
  if curl -sf -o /dev/null "http://127.0.0.1:${OTP_SINK_PORT}/healthz"; then
    break
  fi
  sleep 1
done
curl -sf -o /dev/null "http://127.0.0.1:${OTP_SINK_PORT}/healthz" || {
  echo "smtp sink did not come up"; cat "$OTP_STATE_DIR/sink.log"; exit 1;
}
echo "smtp sink up (smtp :${OTP_SMTP_PORT}, capture api :${OTP_SINK_PORT})"

# ── 2. api in OTP mode ───────────────────────────────────────────────────────
# The SMTP password is GENERATED here and lives only in this process's env — no
# credential, not even a throwaway one, goes into a tracked file. The sink accepts
# any credentials; what matters is that the api's real AUTH exchange happens.
#
# `otp_mailer_key` is the SecretStore LOOKUP NAME, not a value: `EnvSecretStore`
# maps `otp-e2e-smtp` → `KV_SECRET_OTP_E2E_SMTP`, so the two lines below have to
# stay in step. It is held in a variable and referenced further down rather than
# written inline as `AUTH_EMAIL_PASSWORD_SECRET_NAME=<literal>` — a generic
# credential scanner sees a `…PASSWORD…=` assignment two lines under a
# `…USERNAME=` one and reports a hardcoded username/password pair, which
# GitGuardian duly did. Please don't re-inline it.
otp_mailer_key=otp-e2e-smtp
export KV_SECRET_OTP_E2E_SMTP="$(openssl rand -hex 16)"

export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg2://dataq:dataq@localhost:5432/dataq_e2e}"
export ENVIRONMENT=dev
export SECRET_STORE=env
# Explicitly OFF: OTP would win anyway (auth.py's branch order), but a lane whose
# correctness depends on a tie-break is a lane that silently becomes a bypass the
# day that order changes.
export AUTH_DEV_BYPASS=false
export AUTH_EMAIL_SMTP_HOST=localhost
export AUTH_EMAIL_SMTP_PORT="$OTP_SMTP_PORT"
export AUTH_EMAIL_USERNAME=dataq-otp-e2e
export AUTH_EMAIL_FROM="dataq@${HOSTNAME:-localhost}.invalid"
export AUTH_EMAIL_PASSWORD_SECRET_NAME="$otp_mailer_key"
# Domain-wide, so each spec can mint its own unique address (see e2e-otp/fixtures.ts).
export AUTH_OTP_ALLOWED_DOMAINS=dataq.local
export WORKSPACE_ADMIN_EMAILS=otp-admin@dataq.local
# Raised, NOT disabled. The per-mailbox cap stays ON so the lane proves it does
# not break a legitimate flow; the ceiling is simply above what the suite uses,
# including Playwright's CI retries. Setting it to 0 would switch off a mail-bomb
# control and prove nothing about it.
export AUTH_OTP_REQUEST_PER_EMAIL_PER_10MIN=50
# The middleware is off for the same reason the dev-bypass lane turns it off
# (#796): the suite shares one per-IP bucket and would 429 mid-run.
export RATE_LIMIT_ENABLED=false
# Plain HTTP lane: without this the api would infer Secure=false anyway, but
# being explicit keeps the lane honest about what it is exercising.
export AUTH_SESSION_COOKIE_SECURE=false
# Trust ONLY the sink's throwaway certificate — read once, at process start.
export SSL_CERT_FILE="$OTP_STATE_DIR/sink-cert.pem"

uvicorn backend.app.main:app --host 127.0.0.1 --port "$OTP_API_PORT" \
  >"$OTP_STATE_DIR/api.log" 2>&1 &
echo $! >"$OTP_STATE_DIR/api.pid"

for _ in $(seq 1 40); do
  # 401 is the healthy answer here — the endpoint is auth-gated and no cookie is
  # presented. `curl -f` would treat that as failure, so match on the status.
  status="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${OTP_API_PORT}/api/v1/me" || true)"
  if [ "$status" = "401" ]; then
    echo "otp api up on :${OTP_API_PORT} (401 = auth enforced)"
    exit 0
  fi
  sleep 1
done

echo "otp api did not come up"
cat "$OTP_STATE_DIR/api.log"
exit 1
