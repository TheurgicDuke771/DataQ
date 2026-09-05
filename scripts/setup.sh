#!/usr/bin/env bash
# One-command dev environment setup from a fresh clone.

set -euo pipefail

CYAN='\033[0;36m'; GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
step() { echo -e "${CYAN}▶ $1${NC}"; }
ok()   { echo -e "${GREEN}✓ $1${NC}"; }
die()  { echo -e "${RED}✗ $1${NC}" >&2; exit 1; }

# set_env_kv <file> <key> <value> — set KEY=value, leaving an already-non-blank value alone.
set_env_kv() {
  local file="$1" key="$2" value="$3"
  grep -qE "^${key}=..*$" "${file}" && return 0        # already set — keep it
  if grep -qE "^${key}=" "${file}"; then
    sed -i.bak -e "s|^${key}=.*|${key}=${value}|" "${file}" && rm -f "${file}.bak"
  else
    # A hand-edited file may lack a trailing newline; appending blind would splice
    # the key onto the last line and corrupt both.
    [ -s "${file}" ] && [ "$(tail -c1 "${file}" | wc -l)" -eq 0 ] && printf '\n' >> "${file}"
    printf '%s=%s\n' "${key}" "${value}" >> "${file}"
  fi
}

# ── Prerequisites ─────────────────────────────────────────────────────────────
step "Checking prerequisites"
command -v conda  >/dev/null || die "conda not found — install miniconda or miniforge first"
command -v docker >/dev/null || die "docker not found"
command -v pnpm   >/dev/null || die "pnpm not found — run: npm install -g pnpm"
command -v git    >/dev/null || die "git not found"
ok "Prerequisites OK"

# ── Local env files ─────────────────────────────────────────────────────────── Two files (split in
# #209 so Settings can run extra="forbid"): .env — infra/compose only (POSTGRES_*, VITE_*).
step "Preparing .env / .env.app"
# Local-dev DB credentials are GENERATED here, never shipped in the tracked templates (those ship
# blank — we don't commit credentials, even mock ones).
local_pg_user="dataq"
local_pg_db="dataq"
# Reuse a password already set in .env (so re-runs stay consistent); otherwise generate a fresh hex
# one (URL-safe — no special chars to encode in DATABASE_URL).
local_pg_password="$(sed -n 's/^POSTGRES_PASSWORD=\(..*\)$/\1/p' .env 2>/dev/null | head -n1 || true)"
if [ -z "${local_pg_password}" ]; then
  local_pg_password="$(openssl rand -hex 16 2>/dev/null || date +%s | shasum | cut -c1-32)"
fi

# Same story for the OpenBao dev-mode root token (ADR 0039), except it must match across .env
# (compose starts the vault with it) AND .env.app (the app authenticates with it).
local_bao_token="$(sed -n 's/^OPENBAO_TOKEN=\(..*\)$/\1/p' .env 2>/dev/null | head -n1 || true)"
if [ -z "${local_bao_token}" ]; then
  local_bao_token="$(sed -n 's/^OPENBAO_TOKEN=\(..*\)$/\1/p' .env.app 2>/dev/null | head -n1 || true)"
fi
if [ -z "${local_bao_token}" ]; then
  local_bao_token="$(openssl rand -hex 16 2>/dev/null || date +%s | shasum | cut -c1-32)"
fi

# Create each file from its template if missing, then BACK-FILL the local-dev creds whenever the key
# is still blank.
[ -f .env ] || { cp .env.example .env; ok ".env created from .env.example"; }
# Owner-only.
chmod 600 .env 2>/dev/null || true
if ! grep -qE '^POSTGRES_PASSWORD=..*$' .env; then
  sed -i.bak \
    -e "s|^POSTGRES_USER=.*|POSTGRES_USER=${local_pg_user}|" \
    -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${local_pg_password}|" \
    -e "s|^POSTGRES_DB=.*|POSTGRES_DB=${local_pg_db}|" .env && rm -f .env.bak
  ok ".env local Postgres creds generated"
fi
if ! grep -qE '^OPENBAO_TOKEN=..*$' .env; then
  set_env_kv .env OPENBAO_TOKEN "${local_bao_token}"
  ok ".env OpenBao dev root token generated"
fi

[ -f .env.app ] || { cp .env.app.example .env.app; ok ".env.app created from .env.app.example"; }
chmod 600 .env.app 2>/dev/null || true  # same reasoning as .env above
if ! grep -qE '^DATABASE_URL=..*$' .env.app; then
  db_url="postgresql+psycopg2://${local_pg_user}:${local_pg_password}@localhost:5432/${local_pg_db}"
  sed -i.bak -e "s|^DATABASE_URL=.*|DATABASE_URL=${db_url}|" .env.app && rm -f .env.app.bak
  ok ".env.app host DATABASE_URL set"
fi
if ! grep -qE '^OPENBAO_TOKEN=..*$' .env.app; then
  set_env_kv .env.app OPENBAO_TOKEN "${local_bao_token}"
  ok ".env.app OpenBao token set (matches .env)"
fi
# Host-side dev (uvicorn/celery on your machine) reads these from .env.app; the compose api/worker
# override the address to the in-network hostname.
set_env_kv .env.app OPENBAO_ADDR "http://localhost:8200"
set_env_kv .env.app OPENBAO_MOUNT "secret"

# ── Sign-in mode (#1150) ────────────────────────────────────────────────────── The local stack
# boots into email one-time codes (ADR 0032) against the bundled `mailpit` catcher.
if ! grep -qE '^DATAQ_SIGNIN_EMAIL=' .env; then
  step "Choosing a sign-in mode"
  echo "  DataQ signs you in with a 6-digit code emailed to a local inbox"
  echo "  (http://localhost:8025) — nothing leaves this machine, and no real"
  echo "  mailbox is needed. Which address should be allowed to sign in?"
  echo ""
  signin_email=""
  dev_bypass="${DATAQ_DEV_BYPASS:-}"
  if [ -n "${DATAQ_SIGNIN_EMAIL-}" ]; then
    signin_email="${DATAQ_SIGNIN_EMAIL}"
    echo "  Using DATAQ_SIGNIN_EMAIL from the environment."
  elif [ "${dev_bypass}" = "true" ]; then
    echo "  DATAQ_DEV_BYPASS=true in the environment — developer bypass."
  elif [ -t 0 ]; then
    while [ -z "${signin_email}" ] && [ "${dev_bypass}" != "true" ]; do
      printf '  Your email address: '
      read -r signin_email || true
      signin_email="$(printf '%s' "${signin_email}" | tr -d '[:space:]')"
      if [ -z "${signin_email}" ]; then
        echo ""
        echo "  Developers only: the bypass disables sign-in entirely — every request"
        echo "  is one fixed user and anyone who can reach the port is a workspace admin."
        printf '  Enable the developer bypass instead? [y/N] '
        read -r answer || true
        case "${answer}" in
          y|Y|yes|YES) dev_bypass="true" ;;
          *) echo "  OK — an email address is needed for sign-in." ;;
        esac
      fi
    done
  else
    die "No TTY and no sign-in mode chosen. Set DATAQ_SIGNIN_EMAIL=you@example.com (or, developers only, DATAQ_DEV_BYPASS=true) and re-run."
  fi
  set_env_kv .env DATAQ_SIGNIN_EMAIL "${signin_email}"
  if [ "${dev_bypass}" = "true" ]; then
    set_env_kv .env DATAQ_DEV_BYPASS true
    set_env_kv .env.app AUTH_DEV_BYPASS true
    ok "Developer bypass enabled (no sign-in) — set DATAQ_SIGNIN_EMAIL and remove DATAQ_DEV_BYPASS in .env to change"
  else
    ok "Email sign-in enabled for ${signin_email}"
  fi
fi

set -a
# shellcheck disable=SC1091
. ./.env
# shellcheck disable=SC1091
. ./.env.app
set +a

# ── Conda environment ─────────────────────────────────────────────────────────
step "Creating / updating conda environment 'dataq'"
if conda env list | grep -q "^dataq "; then
  conda env update -n dataq -f environment.yml --prune
  ok "Conda env updated"
else
  conda env create -f environment.yml
  ok "Conda env created"
fi

# ── Pre-commit hooks ────────────────────────────────────────────────────────── First run compiles
# the betterleaks secret-scanning hook (language: golang).
step "Installing pre-commit hooks"
conda run -n dataq pre-commit install --install-hooks
ok "Pre-commit hooks installed"

# ── Frontend dependencies ─────────────────────────────────────────────────────
step "Installing frontend dependencies (pnpm)"
(cd frontend && pnpm install)
ok "Frontend dependencies installed"

# ── Docker services ─────────────────────────────────────────────────────────── OpenBao is NOT
# optional here: the seed step below writes connection credentials through the SecretStore
# (seed_dev → demo_data → connection_service.set), so with SECRET_STORE=openbao a missing vault
# fails the whole bootstrap on a fresh clone.
step "Starting Docker services (Postgres, Redis, OpenBao)"
docker compose up -d postgres redis openbao
ok "Docker services started"

# ── Database migrations ───────────────────────────────────────────────────────
step "Waiting for Postgres to be ready"
for i in $(seq 1 30); do
  if docker compose exec -T postgres pg_isready -U dataq >/dev/null 2>&1; then
    ok "Postgres ready"
    break
  fi
  [ "$i" -eq 30 ] && die "Postgres did not become ready in time"
  sleep 1
done

# Same gate for the vault — the seed races a cold OpenBao otherwise, and the
# failure surfaces as an opaque "failed to store connection credential".
step "Waiting for OpenBao to be ready"
for i in $(seq 1 30); do
  if curl -sf -o /dev/null "${OPENBAO_ADDR:-http://localhost:8200}/v1/sys/health"; then
    ok "OpenBao ready"
    break
  fi
  [ "$i" -eq 30 ] && die "OpenBao did not become ready in time"
  sleep 1
done

step "Running Alembic migrations"
conda run -n dataq sh -c "cd backend && alembic upgrade head"
ok "Migrations applied"

# ── Seed data ───────────────────────────────────────────────────────────────── Run as a module
# (-m) so the repo root is on sys.path and `backend.*` imports resolve.
step "Seeding dev data"
conda run -n dataq python -m backend.scripts.seed_dev
ok "Dev data seeded"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  DataQ dev environment ready!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Next steps:"
echo "    conda activate dataq"
echo "    docker compose up          # start all services"
echo "    # API: http://localhost:8000/docs"
echo "    # UI:  http://localhost:3000"
if [ -n "${DATAQ_SIGNIN_EMAIL-}" ]; then
echo ""
echo "  Signing in: enter ${DATAQ_SIGNIN_EMAIL} on the UI, then read the"
echo "  6-digit code in the local inbox at http://localhost:8025."
fi
echo ""
