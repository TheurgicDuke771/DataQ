# ADR 0009 — Flat monorepo repo layout (`backend/` + `frontend/`)

- **Status:** Accepted
- **Date:** 2026-05-24
- **Deciders:** @TheurgicDuke771

## Context

DataQ v1 ships one backend (FastAPI + Celery + GX, Python) and one frontend (React + Vite + Ant Design, Node) on an 8-week timeline. The two stacks have independent toolchains (conda vs pnpm), test runners (pytest vs Vitest), and CI gates, but a single deployment story (Azure Container Apps + Static Web App) and a shared OpenAPI contract.

We need to pick a repo layout in Week 1 before any code lands, because module-resolution paths, CI workflow paths, Dependabot ecosystems, CODEOWNERS rules, and `scripts/setup.sh` all bake in the choice.

Two realistic options:

1. **Flat monorepo** — `backend/` and `frontend/` sit at the repo root. No shared packages.
2. **`apps/` + `packages/`** — apps live under `apps/backend` and `apps/frontend`; reusable code lives under `packages/*` (e.g. `packages/openapi-client`). Common in TypeScript monorepos (pnpm workspaces, Turborepo, Nx).

## Decision

**Adopt a flat monorepo (`backend/` + `frontend/`) at the repo root for v1. Defer `apps/` + `packages/` promotion until a real shared package emerges.**

The trigger for promotion is concrete: when the auto-generated OpenAPI TypeScript client (planned for Week 4–5) is large enough that the frontend wants to import it as a versioned package rather than copy-generate it in-place. Until then, the cost of workspace tooling buys us nothing.

### Layout

```
DataQ/
├── backend/                     # Python (conda)
│   ├── app/
│   ├── alembic/
│   └── tests/
├── frontend/                    # Node (pnpm)
│   ├── src/
│   └── tests/
├── docs/
├── scripts/
├── context/
├── .github/
├── docker-compose.yml
├── environment.yml
├── pyproject.toml
└── ...
```

## Consequences

**Positive**
- Zero workspace tooling overhead — no `pnpm-workspace.yaml`, no Turborepo/Nx config, no path-rewriting in tsconfig/pyproject.
- CI path filters are trivial (`backend/**`, `frontend/**`).
- CODEOWNERS, Dependabot, and `scripts/setup.sh` map 1:1 to two top-level directories.
- New contributors orient in seconds.

**Negative**
- If/when a shared TypeScript package appears, we'll pay a one-time migration cost to move `frontend/` under `apps/` and add `packages/`. Acceptable — the migration is mechanical and only happens once.
- No enforced boundary preventing a future shared utility from being inlined into one app and then copy-pasted into the other. Mitigated by the explicit promotion trigger above.

## Alternatives considered

- **`apps/` + `packages/` from day one** — rejected. Premature abstraction; we have no shared package today and may never need one if the OpenAPI client stays small enough to live inside `frontend/src/api/`. Working-agreement principle: don't design for hypothetical future requirements.
- **Two separate repos (`dataq-backend`, `dataq-frontend`)** — rejected. Single-team, single-deploy product; cross-repo PRs for API contract changes would slow down Week 2–5 iteration. Monorepo keeps OpenAPI changes and their frontend consumers in one reviewable unit.

## Related

- CLAUDE.md §3 (repo layout reference).
- Promotion trigger revisits in Week 4–5 when the OpenAPI client codegen decision lands.
