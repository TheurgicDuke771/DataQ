# ADR 0037 — Workspace-visible asset identity; workspace-true aggregates; grants guard suite-derived detail

- **Status:** Accepted
- **Date:** 2026-07-18
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0027](0027-suite-permission-model-workspace-admin.md) (the suite grant ladder — **unchanged** by this ADR; gains this ADR as the asset-layer companion), ADR [0033](0033-workspace-roles-rbac.md) (workspace roles — a Viewer is still a member and sees identity like everyone else), ADR [0034](0034-asset-entity-openlineage-identity-lineage-pull.md) (decision 5 — **amended a second time**, superseding the #845/#846/#920 redaction-for-identity regime), ADR [0036](0036-connection-anchored-check-engines.md) §scorecard
- **Issue:** [#923](https://github.com/TheurgicDuke771/DataQ/issues/923). Supersedes the identity-redaction halves of [#845](https://github.com/TheurgicDuke771/DataQ/issues/845)/[#846](https://github.com/TheurgicDuke771/DataQ/issues/846) (anonymous lineage nodes), [#901](https://github.com/TheurgicDuke771/DataQ/issues/901) (count-only column boxes) and [#920](https://github.com/TheurgicDuke771/DataQ/issues/920) (redacted browse rows). Simplifies [#889](https://github.com/TheurgicDuke771/DataQ/issues/889) (scorecard) and unblocks the onboarding story behind [#919](https://github.com/TheurgicDuke771/DataQ/issues/919) (catalog sync).

## Context

Until now, asset visibility **derived from suite grants** (ADR 0034 decision 5): an
asset was visible iff the caller could view ≥1 composing suite (or, per the
#845/#846 amendment, had no suites at all), everything else was redacted — anonymous
lineage nodes, count-only column boxes, and (as of #920, one day before this ADR)
locked "🔒 Restricted" browse rows. The health rollup was **per-viewer**, computed
over only the suites the caller's grants covered.

Three forces broke this model:

1. **The bootstrap dead-end.** A newly enrolled member holds no grants, so they see
   only suite-less assets plus a wall of locked boxes. They cannot discover what
   tables exist, what feeds what, or what is already monitored — which is precisely
   the information they need to author their first suite. The browse surface was
   informationally useless to the people who need it most.
2. **Per-viewer aggregates silently disagree.** Two users could look at the same
   asset and see different health verdicts, with nothing indicating either view was
   partial. The scorecard design (#889) hit this head-on: a workspace-true score
   sitting next to a per-viewer badge would render two conflicting verdicts about
   one table on one page. #889 had to invent "redacted count contributions" to
   square it — redaction machinery applied to *arithmetic*.
3. **The boundary protected the wrong thing.** The workspace is single-tenant;
   every member is a colleague authenticated through the workspace IdP, and the
   *connections* list — arguably more sensitive than table names — has been
   workspace-visible to every member since Week 2. Meanwhile the genuinely
   sensitive material (failing-row **samples**, check results, run history, suite
   configuration) was never protected by asset redaction at all — it lives behind
   the suite grants, where it stays.

This is also where mature catalog products (DataHub, Collibra, Atlas) converged:
**metadata visible to all, data and results restricted.**

## Decision

One rule, three layers:

> **What exists and how it connects is workspace knowledge. The aggregate verdict
> is a workspace fact — one truth for every viewer. What was measured, by whom,
> and on which rows belongs to the suite's grants.**

| Layer | Contents | Visibility |
|---|---|---|
| **Identity & topology** | asset name, namespace, env, description, owner, `last_seen`, tree placement; lineage nodes, edges, **column-level pairs**; `is_monitored`; lineage-source health (dbt poll / warehouse tier advisories) | **Every authenticated member** |
| **Aggregate verdicts** | `worst_severity`, `checks_total`/`checks_passed`, run-state flags (`has_failed_run` etc.), `last_run_at`, `suite_count`; (future) the #889 scorecard's per-dimension coverage + scores | **Every member — workspace-true**: computed over **all** composing suites, identical for every viewer |
| **Itemized evaluation** | composing-suite names + per-suite runs on the asset page, suite/check configuration, run detail, results, failing-row samples, incidents | **Grant-scoped** (ADR 0027 ladder, unchanged). Non-visible suites collapse to a `restricted_suite_count` — a count, never names |

Concretely:

- **Browse** returns a full row for every asset to every member. The #920 redacted
  row (`name: null`, `name_prefix_segments`, `is_accessible`) is removed from the
  contract, not just unused.
- **The asset detail endpoint 200s for every existing asset.** The 404-no-leak rule
  *retires at the asset grain* and lives on unchanged at the suite grain: suite,
  run, result and incident endpoints still 404 what your grants don't cover, and
  the asset page's suite table lists only your suites plus
  `restricted_suite_count`.
- **The lineage graph names every node** and column-level pairs are shown to every
  member. Column names are schema metadata — identity, not measurement. The #845
  anonymous node and the #901 count-only column box are removed.
- **Health/scorecard aggregation is workspace-true.** `_roll_up` computes over all
  composing suites regardless of the caller. The #889 scorecard inherits this: the
  numbers are public, the drill-down is granted.
- **Lineage-source health advisories** (failing dbt polls, degraded warehouse
  tiers) are shown to every member. The previous "stake gate" guarded connection
  names that every member can already read off `GET /connections`; it gated
  nothing.
- **Asset metadata mutation** (owner, description) stays workspace-Admin-only.
- **Incidents stay grant-scoped.** An incident carries failure evidence — itemized
  measurement, not identity.

## What this deliberately discloses

Named explicitly so nobody discovers it by surprise:

- **Existence + naming of every table/file an asset row exists for**, to every
  member — including tables monitored exclusively by someone else's suites.
- **That an asset is monitored, and by how many suites** — the discovery signal a
  new user needs ("this table matters"); suite *names* stay behind grants.
- **The workspace-true verdict** — a member can see that a table they hold no
  grants on is failing critically. That is the point: a data consumer deciding
  whether to trust a table needs the verdict most when they are not the one
  monitoring it. What they cannot see is which check, which rows, or any sample
  values.

Deployments that need identity itself compartmentalized inside one workspace
(multi-team regulated tenancy) are out of scope for the single-tenant product; that
would be a future per-workspace strictness mode and warrants its own ADR — a
config knob was considered and rejected below.

## Consequences

**Positive**
- A day-one member can browse the full estate, read lineage end-to-end, see what's
  monitored and how it's doing, and pick an unmonitored table to target — the
  onboarding path exists.
- One verdict per asset. Browse, detail, graph, dashboard and (future) scorecard
  can never disagree with each other *or between viewers* about aggregate state.
- The redaction machinery — `_redacted_summary`, `_accessible_asset_ids`,
  anonymous nodes, count-only column boxes, locked tree leaves, and their wire
  fields — is deleted, not maintained. The #889 scorecard sheds its
  redacted-count-contributions mechanism before it was ever built.
- #919 (warehouse catalog sync) becomes a pure inventory feature: synced-but-
  unmonitored tables surface as ordinary public identity rows.

**Costs / risks (accepted)**
- **A reversal one day after #920 shipped.** The redacted-browse-row build taught
  us the layering was wrong (its live test made a new member's view visibly
  useless); its wire tests convert into pinning the new rule. Recorded, not
  hidden.
- **Aggregate verdicts leak coarse information about other teams' monitoring**
  (see the disclosure list). Accepted for a single-tenant workspace.
- **`suite_count` + workspace-true totals let any member infer activity levels**
  on tables they don't monitor. Accepted — same class as the above.

## Alternatives considered

- **Status quo (derived visibility + redaction).** Rejected: the bootstrap
  dead-end is structural, and per-viewer aggregates are quietly inconsistent.
- **Per-workspace strictness toggle (`ASSET_VISIBILITY=workspace|granted`).**
  Rejected for now: a config axis on an authz rule doubles every visibility test
  and blurs the one-rule guarantee. If a real deployment needs the strict mode,
  that decision gets its own ADR with the demand in hand.
- **Public identity but per-viewer aggregates.** Rejected: recreates the
  two-verdicts-on-one-page inconsistency the scorecard surfaced (#889), and health
  derived from "the suites you happen to see" is an unlabeled partial truth.
- **Asset-level ACLs.** Rejected in ADR 0034 and stays rejected — this ADR removes
  the need for an asset-grant concept rather than adding one.

## Test contract

The three-surfaces agreement test
(`backend/tests/services/test_lineage_authz_redaction.py`) pins the new rule: for
any (asset × caller), browse, detail and graph agree that identity + aggregates
are present and identical for every member, and that itemized suite data appears
iff the ADR 0027 ladder grants it. The suite-grain 404-no-leak keeps its own
tests untouched.
