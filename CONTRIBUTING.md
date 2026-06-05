# Contributing to DataQ

> Working agreements for everyone (human and AI) writing code in this repo. These rules are locked in Week 1 and override any "speed > quality" temptation. If a week's exit gate conflicts with them, **the rules win**.

---

## A. Commit & change discipline

1. **One functionality per commit** (where possible). Each commit must be independently reviewable; do not bundle two unrelated changes.
2. **Manually test each committed change before starting the next functionality.** Required until the unit-test suite reaches 80% coverage (Week 8 gate). "Tested" means: the affected code path was exercised locally, not just that it compiled.
3. **Defects → GitHub issue first, never silent fixes.** Use `gh issue create --title "fix: <desc>"`. The PR that fixes it must include `Fixes #N` in the title or body.
4. **From Week 8 onward, every new functionality ships with unit tests.** Tests live next to the code they cover (`backend/tests/`, `frontend/tests/`).
5. **Definition of Done (DoD)** per task:
   - Code merged to `main`
   - Manually tested locally
   - (From Week 8) unit tests written and passing
   - Docs / ADR updated if the change is user-facing or architectural
   - Linked GitHub issue closed if one exists

---

## B. Git workflow

6. **Trunk-based development** — branch off `main`, PR back to `main`. No long-lived `develop` or `release` branches.
7. **Branch naming:**
   - `feature/<short-desc>` — new functionality
   - `fix/issue-<N>-<short-desc>` — bug fix tied to a GitHub issue
   - `chore/<short-desc>` — tooling, deps, config, non-functional
   - `docs/<short-desc>` — documentation only
8. **`main` branch protection:** PR required + passing CI + no force-push. Approving-review count is 0 during solo-dev phase; re-enable (≥1) before onboarding a second contributor.
9. **Squash-merge only into `main`.** Keeps history linear and matches rule #1. Feature branch commit history is squashed into a single commit on merge.
10. **Conventional commits** for PR titles and the squash-merge commit message:
    - `feat:` — new user-facing functionality
    - `fix:` — bug fix
    - `chore:` — tooling, config, deps (no production behaviour change)
    - `docs:` — documentation only
    - `test:` — tests only
    - `refactor:` — code restructure with no behaviour change
    - `ci:` — CI/CD workflow changes
    - Format: `<type>(<optional scope>): <short imperative description>`
    - Examples: `feat(api): add suite dry-run endpoint`, `fix(celery): handle graceful shutdown on SIGTERM`
11. **PR template** checklist (`.github/pull_request_template.md`): manual test ✓, linked issue, security implications, schema migration.
12. **`CODEOWNERS`** (`.github/CODEOWNERS`) auto-routes reviews per area.

---

## C. CI/CD quality gates

All checks run on every PR and must pass before merge.

13. **Python:** Ruff (lint) → Black `--check` (format) → mypy (types) → Bandit (SAST) → pytest (from Week 8).
14. **Frontend:** ESLint → Prettier `--check` → Vitest (from Week 8).
15. **Secret scanning:** gitleaks in pre-commit hook AND in CI. A secret detected in CI blocks merge.
16. **SAST:** Bandit (Python) + CodeQL (GitHub Actions) on every PR.
17. **Dependency vulnerability scanning:** Dependabot alerts + auto-PRs for security updates, plus a synchronous CI gate (`pip-audit` backend, `pnpm audit` frontend). Python deps are pinned in `backend/requirements*.txt` (single source of truth; `environment.yml` and CI install from there).

---

## D. Coding structure & tooling

These are locked on Day 1 of Week 1. Do not drift.

18. **Python runtime:** `conda` only (`conda create -n dataq python=3.11`). Not venv, not poetry, not pyenv.
19. **Python formatter:** Black. Config in `pyproject.toml`. CI rejects unformatted code.
20. **Python linter:** Ruff. Replaces flake8 + isort + pyupgrade. Config in `pyproject.toml`.
21. **Python type checker:** mypy (strict mode). Config in `pyproject.toml`. When adding a new runtime import to `backend/app/`, add the pinned package to **two** places that must agree: `backend/requirements-typecheck.txt` (the single source of truth — CI's mypy + pytest jobs install from it) and `.pre-commit-config.yaml`'s `mypy.additional_dependencies` list. The `typecheck-deps-sync` pre-commit hook (which also runs in CI) fails if they diverge, so drift is caught before push. Versions must also match `environment.yml`.
22. **Frontend package manager:** pnpm. Not npm, not yarn.
23. **Frontend formatter:** Prettier. Config in `frontend/.prettierrc`.
24. **Frontend linter:** ESLint with TypeScript rules. Config in `frontend/eslint.config.cjs`.
25. **Config / 12-factor:** All environment-specific config via env vars. Backend uses Pydantic Settings (`backend/app/core/config.py`). Frontend uses Vite env vars (`VITE_*`). No env-specific code branches.

---

## E. Observability & error handling

26. **Structured logging from Week 1.** `structlog` with JSON output. Every log entry carries a `request_id` correlation ID propagated FastAPI → Celery → GX. Configuration in `backend/app/core/logging.py`.
27. **PII redaction at the logger level** — not at every call site. Failed-check sample rows can contain sensitive data. The redactor in `backend/app/core/logging.py` strips known PII fields centrally.
28. **Consistent error shape** across all API responses. Definition in `backend/app/core/errors.py`:
    ```json
    { "error": { "code": "SNAKE_CASE_CODE", "message": "Human-readable string", "detail": {} } }
    ```
29. **App Insights exception tracking wired from Week 1**, not Week 7. Middleware in `backend/app/main.py` captures all unhandled exceptions from the first commit.

---

## F. Database & migrations

30. **Backward-compatible migrations only.** No `DROP COLUMN` + code change in the same PR. Two-step deploys from Week 5 onward:
    1. PR A: migration adds/renames column; old code still works.
    2. PR B (later): code change that depends on the new schema.
31. **Migration PR checklist:** rollback plan documented + "tested `alembic upgrade head` and `alembic downgrade -1` locally" ticked before requesting review.

---

## G. Documentation & decision history

32. **ADRs in `docs/adr/`** for every significant architecture decision. Use the `/adr-create` skill or follow the template in `docs/adr/README.md`. One short markdown per decision; keep it to 1–2 pages.
33. **Architecture diagram in `docs/architecture.md`** (Mermaid). When a new component, datasource, or integration is added, update the diagram in the same PR as the code.
34. **Local setup script** `scripts/setup.sh` — one command from a fresh clone to a working dev environment (conda env + pre-commit install + docker-compose up + `alembic upgrade head` + seed data).

---

## H. Security review cadence

35. **End-of-week quick scan from Week 2 onward:** review Dependabot vuln alerts, secret scan results, OWASP top-10 spot check on any new endpoints, Key Vault access audit.
36. **Hard security review gate before Week 7 deploy:** full pass on all of the above plus public-endpoint exposure review (especially `/api/v1/orchestration/events/*` and `/mcp`).
37. **Security vulnerabilities are not public GitHub issues.** Report via [GitHub Security Advisories](https://github.com/TheurgicDuke771/DataQ/security/advisories/new). See [SECURITY.md](.github/SECURITY.md).

---

## Module boundaries & naming conventions

### Backend (`backend/`)

| Layer | Package | Responsibility |
|---|---|---|
| API | `backend/app/api/v1/` | FastAPI routers only — no business logic |
| Services | `backend/app/services/` | Business logic, orchestration between layers |
| Orchestration | `backend/app/orchestration/` | `OrchestrationProvider` abstraction + ADF/Airflow impls |
| Datasources | `backend/app/datasources/` | GX adapter per datasource type |
| DB | `backend/app/db/` | SQLAlchemy models + session management |
| Core | `backend/app/core/` | Config, logging, errors — imported by all layers |
| MCP | `backend/app/mcp/` | FastMCP tools (Week 7 only) |

**Naming:**
- Files: `snake_case.py`
- Classes: `PascalCase`
- Functions / variables: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Async functions: always `async def` for anything that touches DB, Redis, or external HTTP

**Import order** (enforced by Ruff / isort):
1. Standard library
2. Third-party (`fastapi`, `sqlalchemy`, `celery`, …)
3. First-party (`backend.app.*`)

### Frontend (`frontend/`)

| Layer | Path | Responsibility |
|---|---|---|
| Pages | `src/pages/` | Route-level components |
| Components | `src/components/` | Reusable UI components |
| API client | `src/api/` | Generated OpenAPI client (Week 4+) |
| Hooks | `src/hooks/` | Custom React hooks |
| Store | `src/store/` | Global state (if needed) |

**Naming:**
- Component files: `PascalCase.tsx`
- Hook files: `useXxx.ts`
- Util files: `camelCase.ts`

---

## Local development

```bash
# One-time setup from a fresh clone (requires conda installed):
./scripts/setup.sh

# Day-to-day:
conda activate dataq
docker-compose up           # starts Postgres, Redis, FastAPI, React dev server, Celery worker

# Backend only:
cd backend && uvicorn app.main:app --reload

# Frontend only:
cd frontend && pnpm dev

# Run pre-commit on all files:
pre-commit run --all-files

# Run backend tests (Week 8+):
cd backend && pytest

# Run frontend tests (Week 8+):
cd frontend && pnpm test
```

> Commands above assume Week 1 scaffolding (`environment.yml`, `docker-compose.yml`, `scripts/setup.sh`) is committed.
