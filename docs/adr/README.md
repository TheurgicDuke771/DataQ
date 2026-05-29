# Architecture Decision Records (ADRs)

Each ADR captures a single significant architecture decision: the context, the decision, the consequences, and the alternatives considered. New ADRs are append-only — supersede an old decision by adding a new ADR and marking the old one's status as `Superseded by ADR-NNNN`.

## Format

- File name: `NNNN-short-kebab-slug.md` (zero-padded 4-digit sequence)
- Frontmatter fields: Status, Date, Deciders
- Sections: Context, Decision, Consequences, Alternatives considered, Related (optional)
- Keep each ADR short — 1–2 pages. If it grows past that, the decision is probably two decisions.

## Index

| # | Title | Status |
|---|---|---|
| [0001](0001-trunk-based-branching.md) | Trunk-based branching with squash-merge into `main` | Accepted |
| [0002](0002-conventional-commits.md) | Conventional commits for PR titles and commit messages | Accepted |
| [0003](0003-gx-only-for-v1.md) | GX-only for v1; DQX deferred to v1.1 for DLT/streaming | Accepted |
| [0004](0004-orchestration-abstraction.md) | Unified `OrchestrationProvider` abstraction for ADF and Airflow | Accepted |
| [0009](0009-flat-monorepo-layout.md) | Repo layout — flat monorepo (`backend/` + `frontend/`) | Accepted |
| [0010](0010-provider-agnostic-infrastructure-seams.md) | Provider-agnostic infrastructure seams (Azure is the default, not the architecture) | Accepted |
| [0011](0011-extensibility-seams-for-deferred-integrations.md) | Extensibility seams for deferred connectors and integrations | Accepted |

## Pending (to be written in their respective weeks)

| # | Topic | Target week |
|---|---|---|
| 0005 | Severity tier weights (warn / fail / critical → health score) | Week 3, Day 1 |
| 0006 | ADF webhook authentication (shared secret + Key Vault rotation) | Week 2 |
| 0007 | Airflow callback model (HMAC signing + polling fallback) | Week 2 |
| 0008 | MCP mounted at `/mcp` with Azure AD auth | Week 7 |
