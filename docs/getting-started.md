# Getting started (local dev)

## Prerequisites

- **conda** (the backend uses a conda env — not venv/poetry), **Docker** + Docker
  Compose, and **Node 24+ / pnpm 9+** for the frontend.

## One-command setup

```bash
git clone https://github.com/TheurgicDuke771/DataQ.git
cd DataQ
./scripts/setup.sh     # creates the `dataq` conda env, installs pre-commit, pulls
                       # images, runs DB migrations, seeds dev data, writes a local .env
conda activate dataq
docker-compose up      # Postgres + Redis + FastAPI (:8000) + React (:3000) + Celery
```

Local auth uses a **dev-bypass** (no Azure tenant needed) — every request resolves to a
fixed dev user. Real Azure AD SSO is configured via environment variables in deployed
environments.

## Configuration

All runtime config is environment variables read by the backend's `Settings`. The
**complete, commented reference** is
[`.env.app.example`](https://github.com/TheurgicDuke771/DataQ/blob/main/.env.app.example) —
copy it to `.env.app` (gitignored) and adjust. Never commit secrets; `scripts/setup.sh`
generates local-dev credentials on first run.

## Running tests

```bash
# Backend (needs a Postgres; CI provides one — locally set TEST_DATABASE_URL):
conda run -n dataq python -m pytest backend/tests

# Frontend:
cd frontend && pnpm test
```

Before pushing, run the same gate CI does: Ruff, Black `--check`, mypy, Bandit, pytest
(backend) and ESLint, Prettier `--check`, Vitest (frontend). See the
**[Contributing guide](contributing.md)**.
