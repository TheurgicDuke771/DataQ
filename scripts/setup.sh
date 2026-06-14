#!/usr/bin/env bash
# One-command dev environment setup from a fresh clone.
# Usage: ./scripts/setup.sh
# Requires: conda (miniconda/miniforge), docker, pnpm, git

set -euo pipefail

CYAN='\033[0;36m'; GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
step() { echo -e "${CYAN}▶ $1${NC}"; }
ok()   { echo -e "${GREEN}✓ $1${NC}"; }
die()  { echo -e "${RED}✗ $1${NC}" >&2; exit 1; }

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
if [ ! -f .env ]; then
  cp .env.example .env
  ok ".env created from .env.example"
else
  ok ".env already present"
fi
if [ ! -f .env.app ]; then
  cp .env.app.example .env.app
  ok ".env.app created from .env.app.example"
else
  ok ".env.app already present"
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
step "Starting Docker services (Postgres, Redis)"
docker compose up -d postgres redis
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
