#!/usr/bin/env bash
# One-command dev environment setup from a fresh clone.
# Usage: ./scripts/setup.sh
# Requires: conda (miniconda/miniforge), docker, pnpm, git

set -euo pipefail

CYAN='\033[0;36m'; GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
step() { echo -e "${CYAN}▶ $1${NC}"; }
ok()   { echo -e "${GREEN}✓ $1${NC}"; }
die()  { echo -e "${RED}✗ $1${NC}" >&2; exit 1; }

# set_env_kv <file> <key> <value> — set KEY=value, leaving an already-non-blank
# value alone. Substitutes when the key is present (blank or not) and APPENDS when
# it is absent: a plain `sed s|^KEY=.*|` is a silent no-op on a .env that predates
# the key, which would leave the file short of a value compose then refuses to
# start without — telling the user to run this script, which they just did.
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

# ── Local env files ───────────────────────────────────────────────────────────
# Two files (split in #209 so Settings can run extra="forbid"):
#   .env     — infra/compose only (POSTGRES_*, VITE_*); compose substitutes ${...}
#              from it and the postgres service reads it.
#   .env.app — app config (DATABASE_URL, AZURE_*, …); the file Settings reads.
# App code carries no DB credentials (config.py default is credential-less), so
# host-side tooling (alembic, seed, uvicorn) needs DATABASE_URL from .env.app.
# Create both from their templates on first run, then export so child processes
# inherit them regardless of working dir (alembic runs from backend/, so a
# CWD-relative dotenv lookup wouldn't find the root file).
step "Preparing .env / .env.app"
# Local-dev DB credentials are GENERATED here, never shipped in the tracked
# templates (those ship blank — we don't commit credentials, even mock ones).
# The password must match across both files: .env's POSTGRES_PASSWORD (the
# postgres container + the compose-built container DATABASE_URL) and .env.app's
# host-side DATABASE_URL (alembic/seed/uvicorn). user/db are non-secret
# identifiers; only the password is generated.
local_pg_user="dataq"
local_pg_db="dataq"
# Reuse a password already set in .env (so re-runs stay consistent); otherwise
# generate a fresh hex one (URL-safe — no special chars to encode in
# DATABASE_URL). `=..*` matches only a NON-blank value, so a blank line doesn't
# count as "set".
local_pg_password="$(sed -n 's/^POSTGRES_PASSWORD=\(..*\)$/\1/p' .env 2>/dev/null | head -n1 || true)"
if [ -z "${local_pg_password}" ]; then
  local_pg_password="$(openssl rand -hex 16 2>/dev/null || date +%s | shasum | cut -c1-32)"
fi

# Same story for the OpenBao dev-mode root token (ADR 0039), except it must match
# across .env (compose starts the vault with it) AND .env.app (the app authenticates
# with it) — a mismatch 403s every credential read. Reuse whichever file already has
# one so re-runs stay consistent.
local_bao_token="$(sed -n 's/^OPENBAO_TOKEN=\(..*\)$/\1/p' .env 2>/dev/null | head -n1 || true)"
if [ -z "${local_bao_token}" ]; then
  local_bao_token="$(sed -n 's/^OPENBAO_TOKEN=\(..*\)$/\1/p' .env.app 2>/dev/null | head -n1 || true)"
fi
if [ -z "${local_bao_token}" ]; then
  local_bao_token="$(openssl rand -hex 16 2>/dev/null || date +%s | shasum | cut -c1-32)"
fi

# Create each file from its template if missing, then BACK-FILL the local-dev
# creds whenever the key is still blank — covers a fresh copy AND a pre-existing
# file left blank (e.g. a manual `cp` of the now-blank template). Without the
# back-fill, a blank POSTGRES_PASSWORD trips compose's `${VAR:?}` guard / mismatches
# the host DATABASE_URL.
[ -f .env ] || { cp .env.example .env; ok ".env created from .env.example"; }
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
if ! grep -qE '^DATABASE_URL=..*$' .env.app; then
  db_url="postgresql+psycopg2://${local_pg_user}:${local_pg_password}@localhost:5432/${local_pg_db}"
  sed -i.bak -e "s|^DATABASE_URL=.*|DATABASE_URL=${db_url}|" .env.app && rm -f .env.app.bak
  ok ".env.app host DATABASE_URL set"
fi
if ! grep -qE '^OPENBAO_TOKEN=..*$' .env.app; then
  set_env_kv .env.app OPENBAO_TOKEN "${local_bao_token}"
  ok ".env.app OpenBao token set (matches .env)"
fi
# Host-side dev (uvicorn/celery on your machine) reads these from .env.app; the
# compose api/worker override the address to the in-network hostname. Appended for
# a .env.app that predates ADR 0039 so a re-run leaves a complete file. SECRET_STORE
# is deliberately NOT rewritten — an existing `redis` value raises at startup with
# the migration path, and silently changing someone's configured mode is worse.
set_env_kv .env.app OPENBAO_ADDR "http://localhost:8200"
set_env_kv .env.app OPENBAO_MOUNT "secret"
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

# ── Pre-commit hooks ──────────────────────────────────────────────────────────
# First run compiles the betterleaks secret-scanning hook (language: golang); this
# can take a minute. pre-commit (≥3.0) bootstraps its own Go — no system Go needed.
step "Installing pre-commit hooks"
conda run -n dataq pre-commit install --install-hooks
ok "Pre-commit hooks installed"

# ── Frontend dependencies ─────────────────────────────────────────────────────
step "Installing frontend dependencies (pnpm)"
(cd frontend && pnpm install)
ok "Frontend dependencies installed"

# ── Docker services ───────────────────────────────────────────────────────────
# OpenBao is NOT optional here: the seed step below writes connection credentials
# through the SecretStore (seed_dev → demo_data → connection_service.set), so with
# SECRET_STORE=openbao a missing vault fails the whole bootstrap on a fresh clone.
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

# ── Seed data ─────────────────────────────────────────────────────────────────
# Run as a module (-m) so the repo root is on sys.path and `backend.*` imports
# resolve; running the file directly would not put the root on the path.
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
echo ""
