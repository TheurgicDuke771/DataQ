#!/usr/bin/env bash
# Bring up the backend half of the email-OTP browser lane (ADR 0032, #736). scripts/e2e-otp-stack.sh
# Starts.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

OTP_API_PORT="${OTP_API_PORT:-8100}"
OTP_SMTP_PORT="${OTP_SMTP_PORT:-1025}"
OTP_SINK_PORT="${OTP_SINK_PORT:-1080}"
# An explicit template, and no `-t`.
OTP_STATE_DIR="${OTP_STATE_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/dataq-otp-lane.XXXXXX")}"
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

# ── 2. api in OTP mode ─────────────────────────────────────────────────────── The SMTP password is
# GENERATED here and lives only in this process's env — no credential, not even a throwaway one.
otp_mailer_key=otp-e2e-smtp
export KV_SECRET_OTP_E2E_SMTP="$(openssl rand -hex 16)"

export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg2://dataq:dataq@localhost:5432/dataq_e2e}"
export ENVIRONMENT=dev
export SECRET_STORE=env
# Explicitly OFF: OTP would win anyway (auth.py's branch order), but a lane whose correctness
# depends on a tie-break is a lane that silently becomes a bypass the day that order changes.
export AUTH_DEV_BYPASS=false
export AUTH_EMAIL_SMTP_HOST=localhost
export AUTH_EMAIL_SMTP_PORT="$OTP_SMTP_PORT"
export AUTH_EMAIL_USERNAME=dataq-otp-e2e
export AUTH_EMAIL_FROM="dataq@${HOSTNAME:-localhost}.invalid"
export AUTH_EMAIL_PASSWORD_SECRET_NAME="$otp_mailer_key"
# Domain-wide, so each spec can mint its own unique address (see e2e-otp/fixtures.ts).
export AUTH_OTP_ALLOWED_DOMAINS=dataq.local
export WORKSPACE_ADMIN_EMAILS=otp-admin@dataq.local
# Raised, NOT disabled.
export AUTH_OTP_REQUEST_PER_EMAIL_PER_10MIN=50
# The middleware is off for the same reason the dev-bypass lane turns it off
# (#796): the suite shares one per-IP bucket and would 429 mid-run.
export RATE_LIMIT_ENABLED=false
# Plain HTTP lane: without this the api would infer Secure=false anyway, but
# being explicit keeps the lane honest about what it is exercising.
export AUTH_SESSION_COOKIE_SECURE=false
# Trust ONLY the sink's throwaway certificate, and ONLY for the OTP mailer's own connection (#1146).
export AUTH_EMAIL_CA_BUNDLE="$OTP_STATE_DIR/sink-cert.pem"

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
