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
