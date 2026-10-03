#!/usr/bin/env bash
# Smoke-test a running prebuilt-image stack (docker-compose.ghcr.yml) the way a deployment is
# smoked after a roll: TLS, the single public surface, the auth gates, a real sign-in, a write,
# the rate limiter, and TLS between the stack's own services.
#
#   scripts/local-smoke.sh                      # https://localhost:3000, inbox on :8025
#   scripts/local-smoke.sh --email you@example.com --base-url https://localhost:3000
#
# Run it beside docker-compose.ghcr.yml (or set DATAQ_COMPOSE_FILE). It signs in as --email
# (default: the first address in DATAQ_SIGNIN_EMAIL), which must be allowed to sign in. Each
# run sends one sign-in mail (counted against that address's code quota, so a handful of
# runs in ten minutes stops the mail), signs in and out again changing nothing else, and
# bursts up to 300 anonymous requests at the API, so the rate limiter may refuse requests
# from this machine for the next minute.
# Exit status: 0 when every check passes, 1 otherwise.

set -uo pipefail

BASE_URL="https://localhost:3000"
INBOX_URL="http://localhost:8025"
EMAIL="${DATAQ_SIGNIN_EMAIL:-}"
EMAIL="${EMAIL%%,*}"
COMPOSE_FILE="${DATAQ_COMPOSE_FILE:-docker-compose.ghcr.yml}"

usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; }
while [ $# -gt 0 ]; do
  case "$1" in
    --base-url)  BASE_URL="${2:-}"; shift ;;
    --inbox-url) INBOX_URL="${2:-}"; shift ;;
    --email)     EMAIL="${2:-}"; shift ;;
    -h|--help)   usage; exit 0 ;;
    *)           usage; exit 2 ;;
  esac
  shift
done
[ -n "${EMAIL}" ] || { echo "No address to sign in as: pass --email or export DATAQ_SIGNIN_EMAIL" >&2; exit 2; }

GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
failures=0
pass() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; failures=$((failures + 1)); }
# check <description> <actual> <expected> — exactly three words: an <actual> that expanded into
# two would otherwise be compared with itself and pass.
check() {
  if [ $# -ne 3 ]; then fail "$1 (the check itself is malformed: $# arguments)"; return; fi
  if [ "$2" = "$3" ]; then pass "$1"; else fail "$1 (got '$2', want '$3')"; fi
}
# JSON bodies are built here, not inline: bash 3.2 (macOS) brace-expands an inline `{a,b}` body
# inside a quoted command substitution and sends two requests.
json() { local format="$1"; shift; printf "${format}" "$@"; }

workdir="$(mktemp -d)"
trap 'rm -rf "${workdir}"' EXIT
ca="${workdir}/ca.pem"
jar="${workdir}/cookies"

compose() {
  OPENBAO_TOKEN="${OPENBAO_TOKEN:-unused}" DATAQ_SIGNIN_EMAIL="${DATAQ_SIGNIN_EMAIL-unused@example.com}" \
    docker compose -f "${COMPOSE_FILE}" "$@"
}
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$@"; }

# ── TLS at the edge ───────────────────────────────────────────────────────────
echo "TLS"
compose cp frontend:/certs/ca.pem "${ca}" >/dev/null 2>&1
if [ ! -s "${ca}" ]; then
  fail "the stack's CA certificate could not be read (is the stack up, beside ${COMPOSE_FILE}?)"
  exit 1
fi
check "the certificate verifies against the stack's CA" "$(code --cacert "${ca}" "${BASE_URL}/healthz")" "200"
# An empty CA file, so a CA you trusted system-wide does not make this pass by accident. curl
# reports 60 (untrusted) or 77 (no usable CA file) depending on its TLS library.
untrusted_rc="$(curl -s -o /dev/null --max-time 20 --cacert /dev/null "${BASE_URL}/healthz"; echo $?)"
case "${untrusted_rc}" in 60|77) untrusted="refused" ;; *) untrusted="rc ${untrusted_rc}" ;; esac
check "with no trusted CA the handshake fails, so verification is real" "${untrusted}" "refused"
plain_url="http://${BASE_URL#https://}"
check "plain HTTP is redirected to HTTPS" \
  "$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 20 "${plain_url}/suites")" "308 ${BASE_URL}/suites"

# ── One public surface, gated ─────────────────────────────────────────────────
echo "Surface"
check "the UI loads" "$(code --cacert "${ca}" "${BASE_URL}/")" "200"
check "a deep link resolves to the app" "$(code --cacert "${ca}" "${BASE_URL}/results/smoke")" "200"
check "the API refuses an anonymous caller" "$(code --cacert "${ca}" "${BASE_URL}/api/v1/me")" "401"
check "MCP refuses an anonymous caller" "$(code --cacert "${ca}" "${BASE_URL}/mcp/")" "401"
check "the interactive API page is off" \
  "$(curl -s --cacert "${ca}" --max-time 20 "${BASE_URL}/docs" | grep -c -i 'swagger')" "0"
published="$(compose ps --format '{{.Service}} {{.Ports}}' 2>/dev/null \
  | grep -e '->' | awk '{print $1}' | sort | tr '\n' ' ' | sed 's/ $//')"
check "only the UI and the inbox publish a host port" "${published}" "frontend mailpit"

headers="$(curl -sI --cacert "${ca}" --max-time 20 "${BASE_URL}/" | tr -d '\r' | tr '[:upper:]' '[:lower:]')"
for header in x-frame-options x-content-type-options referrer-policy permissions-policy content-security-policy; do
  check "header ${header}" "$(echo "${headers}" | grep -c "^${header}:")" "1"
done
check "no HSTS on localhost (it would bind every local port)" \
  "$(echo "${headers}" | grep -c '^strict-transport-security:')" "0"

# ── A real sign-in, a read and a write ────────────────────────────────────────
echo "Sign-in"
before="$(curl -s --max-time 20 "${INBOX_URL}/api/v1/message/latest" | sed -n 's/.*"ID":"\([^"]*\)".*/\1/p')"
request_body="$(json '{"email":"%s"}' "${EMAIL}")"
check "a sign-in code is requested" \
  "$(code --cacert "${ca}" -X POST "${BASE_URL}/api/v1/auth/otp/request" -H 'content-type: application/json' -d "${request_body}")" "200"
otp=""
for _ in $(seq 1 20); do
  message="$(curl -s --max-time 20 "${INBOX_URL}/api/v1/message/latest")"
  latest="$(echo "${message}" | sed -n 's/.*"ID":"\([^"]*\)".*/\1/p')"
  if [ -n "${latest}" ] && [ "${latest}" != "${before}" ]; then
    # From the plain-text body, where the code has a line to itself — not from the message
    # JSON, whose address fields can contain six digits of their own.
    otp="$(curl -s --max-time 20 "${INBOX_URL}/view/latest.txt" \
      | grep -E '^[[:space:]]*[0-9]{6}[[:space:]]*$' | head -n1 | tr -cd '0-9')"
    [ -n "${otp}" ] && break
  fi
  sleep 1
done
if [ -n "${otp}" ]; then pass "the code arrives in the inbox (mail sent over STARTTLS)"; else fail "no sign-in mail arrived for ${EMAIL} — is it allowed to sign in?"; fi
verify_body="$(json '{"email":"%s","code":"%s"}' "${EMAIL}" "${otp}")"
check "the code signs in" \
  "$(code --cacert "${ca}" -c "${jar}" -D "${workdir}/signin-headers" -X POST "${BASE_URL}/api/v1/auth/otp/verify" -H 'content-type: application/json' -d "${verify_body}")" "200"
cookie="$(grep -i '^set-cookie:' "${workdir}/signin-headers" 2>/dev/null | tr '[:upper:]' '[:lower:]')"
check "the session cookie is Secure and HttpOnly" \
  "$(echo "${cookie}" | grep -c 'secure')/$(echo "${cookie}" | grep -c 'httponly')" "1/1"
check "an authenticated read works" "$(code --cacert "${ca}" -b "${jar}" "${BASE_URL}/api/v1/me")" "200"
# The write: signing out revokes the session server-side, and changes nothing else.
check "an authenticated write works (sign-out)" \
  "$(code --cacert "${ca}" -b "${jar}" -X POST "${BASE_URL}/api/v1/auth/logout")" "204"
check "the signed-out session is refused" "$(code --cacert "${ca}" -b "${jar}" "${BASE_URL}/api/v1/me")" "401"

# ── TLS between the stack's own services ──────────────────────────────────────
echo "Inside the stack"
check "every remote Postgres connection is TLS" \
  "$(compose exec -T postgres psql -U dataq -d dataq -Atc "select count(*) from pg_stat_ssl s join pg_stat_activity a using (pid) where a.client_addr is not null and not s.ssl" 2>/dev/null | tr -d '[:space:]')" "0"
check "Redis refuses a plaintext client" \
  "$(compose exec -T redis redis-cli -h redis ping 2>&1 | grep -c PONG)" "0"
check "Redis answers over verified TLS" \
  "$(compose exec -T redis redis-cli --tls --cacert /certs/ca.pem -h redis ping 2>/dev/null | tr -d '[:space:]')" "PONG"

# ── The rate limiter (last: it throttles this machine for a minute) ───────────
echo "Rate limit"
limited=0
for _ in $(seq 1 300); do
  if [ "$(code --cacert "${ca}" "${BASE_URL}/api/v1/me")" = "429" ]; then limited=1; break; fi
done
check "a burst is answered with 429" "${limited}" "1"


echo ""
if [ "${failures}" -eq 0 ]; then
  echo -e "${GREEN}All checks passed.${NC}"
else
  echo -e "${RED}${failures} check(s) failed.${NC}"
  exit 1
fi
