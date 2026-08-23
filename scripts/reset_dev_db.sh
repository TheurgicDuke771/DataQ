#!/usr/bin/env bash
# Reset the local dev database: drop everything, re-migrate, re-seed.

set -euo pipefail

CYAN='\033[0;36m'; GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
step() { echo -e "${CYAN}▶ $1${NC}"; }
ok()   { echo -e "${GREEN}✓ $1${NC}"; }
die()  { echo -e "${RED}✗ $1${NC}" >&2; exit 1; }

[ -f .env ] || die ".env not found — run ./scripts/setup.sh first"
set -a
# shellcheck disable=SC1091
. ./.env
set +a

: "${POSTGRES_USER:?POSTGRES_USER not set in .env}"
: "${POSTGRES_DB:?POSTGRES_DB not set in .env}"

step "Ensuring Postgres + OpenBao are up"
# OpenBao is NOT optional here: seed_dev writes connection credentials through
# the SecretStore, and the stock .env.app ships SECRET_STORE=openbao (ADR 0039).
docker compose up -d postgres openbao >/dev/null
for i in $(seq 1 30); do
  docker compose exec -T postgres pg_isready -U "${POSTGRES_USER}" >/dev/null 2>&1 && { ok "Postgres ready"; break; }
  [ "$i" -eq 30 ] && die "Postgres did not become ready in time"
  sleep 1
done
for i in $(seq 1 30); do
  docker compose exec -T -e BAO_ADDR=http://127.0.0.1:8200 openbao bao status >/dev/null 2>&1 && { ok "OpenBao ready"; break; }
  [ "$i" -eq 30 ] && die "OpenBao did not become ready in time"
  sleep 1
done

# DROP SCHEMA clears tables AND the alembic_version row in one shot, so the
# re-migrate below runs the full chain from base — no drift left behind.
step "Dropping and recreating the public schema"
docker compose exec -T postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null
ok "Schema reset"

step "Running Alembic migrations"
conda run -n dataq sh -c "cd backend && alembic upgrade head"
ok "Migrations applied"

step "Seeding dev data"
conda run -n dataq python -m backend.scripts.seed_dev
ok "Dev data seeded"

echo ""
echo -e "${GREEN}Dev database reset complete.${NC}"
