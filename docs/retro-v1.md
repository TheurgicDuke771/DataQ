# DataQ v1.0.0 — retrospective

> Internal document (excluded from the published site). Written 2026-07-04 at the
> `v1.0.0` tag, closing the 8-week build (2026-05-24 → 2026-07-03). Companion to
> [progress-v1.md](progress-v1.md) (the archived v1 per-PR ledger) and
> [post-v1-roadmap.md](../context/post-v1-roadmap.md) (what's next and the honest
> gap register).

## What shipped

A single-tenant data-quality monitoring platform on GX Core: 4 datasources
(Snowflake, ADLS Gen2, S3, Unity Catalog) + 2 orchestration providers (ADF,
Airflow) behind provider-agnostic seams; suite/check authoring with custom SQL,
severity tiers, and freshness/volume monitor kinds (pulled forward from the
post-v1 roadmap); async execution with live progress, scheduling, trigger
bindings, and near-real-time failure ingest; a 13-screen React frontend; Teams/
Slack/email alerting with routing, dedup, and suppression; an 8-tool MCP server;
and the whole thing deployed to Azure Container Apps behind a sole-public
cloud-neutral frontend image. 189-task roadmap: 187 done, 2 consciously
re-scoped (W2 browse/list pickers → post-v1, #466).

**By the numbers:** 405 commits · ~570 PRs+issues (110 issues closed) · 27 ADRs ·
1,289 backend tests (98.4% line coverage, CI gate ≥80%) · 337 frontend tests
(~88%, all-src gate ≥80%) · 25 Playwright E2E specs + an opt-in live-smoke lane ·
8 weeks.

## What worked (keep doing)

- **Working agreements as code.** One-functionality commits, defects-as-issues
  (never silent fixes), conventional commits, squash-merge, and the 12-check CI
  gate meant the history stayed navigable and every regression had a paper trail.
  The discipline felt heavy in week 1 and paid for itself by week 3.
- **Seams before features.** `ConnectionAdapter`, `CheckRunner`, `OrchestrationProvider`,
  `ResultPublisher`, `SecretStore`, `get_current_user`, and the monitor-kind
  discriminator (ADR 0012) were all designed before their second implementation
  existed. Every one of them later absorbed a change that would otherwise have
  been a rewrite (DQX-shape UC runner, Slack/email publishers, freshness/volume
  kinds, the generic-OIDC cutover).
- **ADRs for anything with a rationale.** 27 short records; at least a dozen
  "why is it like this?" moments during W7–W8 were answered by linking an ADR
  instead of re-litigating. The decision-record pattern (0026's deferred-with-
  shape-confirmed) works well for "not now, but decided".
- **Adversarial testing as a standing rule (rule 4a).** The adversarial harness,
  mutation spikes (mutmut 436/436 on `dashboard_service`; Stryker 82.35%), and
  the qa-verifier agent's data-level batteries repeatedly caught what line
  coverage could not — most recently the NUL-byte 500 (#567) at 98% coverage.
- **The external live harness (ADR 0021).** Real Snowflake/UC/ADLS/ADF/Airflow
  with seeded DQ issues turned "works on my machine" into three verified live
  flows, and caught genuine production bugs (UC dialect regression #535,
  credential leak in tracebacks #536, suite-delete cascade #540).

## What hurt (do differently)

- **Happy-path validation debt surfaced late.** The NUL-byte class (#567) and the
  422-handler crash (#371) both lived in the gap between "Pydantic accepts it"
  and "Postgres refuses it" — found in go-live week by the qa-verifier workout.
  Lesson: run the data-level hostile battery *per feature PR* (it's cheap), not
  only at milestones; the boundary between validation layers is where 500s hide.
- **Deploy-day surprises cluster around identity/config, not code.** Celery beat
  lock (#405), missing `AZURE_CLIENT_ID` for UAMI (#406), orphaned SWA EasyAuth
  (#511), CLI consent (AADSTS65001/650057 → #565): none were app logic. The
  start-time-secret-snapshot restart rule and the pre-authorized-client pattern
  are now documented; budget explicit time for auth plumbing in any new cloud.
- **Point-in-time counts in living docs rot fast.** progress.md/CLAUDE.md carry
  counts that drifted within days (test totals, open-issue counts) and needed a
  dedicated fact-check pass before the tag. Lesson: label point-in-time numbers
  with their date ("at flip"), keep current-state counts in one place only.
- **Session/token ergonomics taxed every live task.** SPA refresh tokens cap at
  24h, az sessions are single-active-account, and every live verification began
  with an auth dance. ADR 0026 (PATs) is deferred with eyes open — it should be
  early in the post-v1 sequence if programmatic/live use grows.

## Go-live decisions of record (2026-07-03/04)

- **ADR 0026 (API keys): deferred to post-v1 Theme 3.** Shape confirmed —
  user-scoped PATs first, service-account principals later, HTTP Basic auth
  rejected. Interim: Azure CLI pre-authorized on the API scope (#565).
- **Databricks Free Edition (gap G-h):** fine for demo/eval; migrate to a paid
  workspace before any commercial use.
- **Pre-marketplace harness teardown (gap G-i):** strip Flows A/B/C, the 5
  harness connections, demo users, and the seeded-breach check from any
  marketplace/customer-facing artifact.
- **Ops/renewal timers: consciously skipped.** The September-2026 credential
  cluster (Snowflake PAT ~09-26, Databricks PATs ×3 ~09-30, ADLS SAS 2027-06-28)
  is demo-harness-scoped only; expiry self-signals through DataQ's own alerting
  (#419 always-alerts operationally-failed runs) and recovery is re-mint +
  KV update (documented). No reminder infrastructure; the teardown note (G-i)
  covers the end state.
- **KV purge protection: left off** (decided 2026-07-02; demo-scoped vault,
  destroy/re-apply flexibility, all secrets re-mintable).

## Final verification before the tag

- CI gates green on `main` (coverage 98.4% / ~88% against the 80% gates).
- qa-verifier data-level workout: initial NO-GO (found #567) → fix #570 →
  re-run **GO** (21 NUL injection points 422, zero 500s, all batteries green).
- Live prod verification as a real non-admin user (Olivia): 15/15 — suite-scoped
  authz, permission tiers, NUL/custom-SQL guardrails, live run `succeeded`.
- Webhook auth hostility live vs prod: 7/7 structured 401s; valid paths proven
  by production traffic (#492 ADF alert 4m14s fire→ingest; #490 Airflow callbacks).
- Follow-ups open by choice, none blocking: #563 (mutation-spike survivors),
  #568 (threshold-ordering validation), #571 (checks_total on pre-dispatch
  failures), #573 (SchedulesPanel CI flake).

## Post-v1

The single source for what's next is
[context/post-v1-roadmap.md](../context/post-v1-roadmap.md) — 53 issues across
13 themes plus the honest gap register (G-a…G-i). Recommended opening sequence
per that doc: `schema_drift` + `anomaly` monitor kinds (rides ADR 0012) →
scale-aware execution (G-b) → incident/lineage design (G-d).
