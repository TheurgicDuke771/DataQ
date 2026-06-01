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
| [0005](0005-severity-tier-weights.md) | Severity tier weights (warn / fail / critical → health score) | Accepted |
| [0006](0006-adf-webhook-authentication.md) | ADF webhook authentication (shared secret in URL + hard-cutover rotation) | Accepted |
| [0007](0007-airflow-callback-model.md) | Airflow callback model (HMAC-signed webhook + polling fallback) | Accepted |
| [0009](0009-flat-monorepo-layout.md) | Repo layout — flat monorepo (`backend/` + `frontend/`) | Accepted |
| [0010](0010-provider-agnostic-infrastructure-seams.md) | Provider-agnostic infrastructure seams (Azure is the default, not the architecture) | Accepted |
| [0011](0011-extensibility-seams-for-deferred-integrations.md) | Extensibility seams for deferred connectors and integrations | Accepted |
| [0012](0012-monitor-kind-seam.md) | Monitor-kind seam (`check.kind` discriminator + numeric metric storage) | Accepted |
| [0013](0013-marketplace-distribution-and-anti-lock-in.md) | Marketplace distribution (customer-deployed BYOL) and anti-vendor-lock-in guardrails | Accepted |

## Pending (to be written in their respective weeks)

| # | Topic | Target week |
|---|---|---|
| 0008 | MCP mounted at `/mcp` with Azure AD auth | Week 7 |
