# Post-v1 notes — Assets, lineage & incidents (gap G-d design)

> **Status: design for the next cycle's build (the #596 deliverable — no code in this
> PR).** Gap G-d is the "usable in anger" gap: runs and alerts exist, but "what broke
> downstream / who owns it / when was it resolved" doesn't — and that triad is what DQ
> products are bought for. This doc pins the three objects (asset, lineage edge,
> incident), their identity model, and the build order; the phase-1 issues it files are
> the next cycle's generator input. Decision record: ADR
> [0034](adr/0034-asset-entity-openlineage-identity-lineage-pull.md).
>
> Related: **#596** (this doc), Themes 2/3/5/14 in
> [context/post-v1-roadmap.md](https://github.com/TheurgicDuke771/DataQ/blob/main/context/post-v1-roadmap.md), ADR 0012 (monitor
> kinds), ADR 0027/0033 (authz axes), ADR 0029 (dbt provider), ADR 0031 (licensing).

## 1. The asset entity — the one migration everything rides

Today "the table" exists only implicitly inside `Suite.target` (one JSONB blob,
resolved per connection type in `run_target.py`). Lineage needs asset *nodes*, the
asset page needs asset *identity*, incidents need something to *attach to*, and
catalog sync needs an entity to *map to* — one `assets` table serves all four, so it
ships first (phase 1) and alone.

- **Identity = the OpenLineage dataset naming spec** (`namespace` + `name`,
  unique together), adopted verbatim so Theme-14 emission/pull interop is a no-op
  rather than a mapping layer. Canonical resolution from `resolve_target()` output:

  | Connection type | Namespace | Name |
  |---|---|---|
  | `snowflake` | `snowflake://{org}-{account}` (normalized account identifier) | `DB.SCHEMA.TABLE` |
  | `unity_catalog` | `unitycatalog://{workspace_host}` | `catalog.schema.table` |
  | `adls_gen2` | `abfss://{container}@{account}.dfs.core.windows.net` | `{path or pattern base dir}` |
  | `s3` | `s3://{bucket}` | `{key or pattern base dir}` |
  | `iceberg` | catalog URI verbatim (REST) / `file` (local warehouse) | `{namespace}.{table}` |

- **Normalization rules (byte-compatibility with `openlineage-dbt` / Spark):** strip
  identifier quotes, keep the case the engine's catalog returns (Snowflake ⇒ upper),
  run the same Snowflake account normalization the OL clients use (hyphenated
  org-account passes through; legacy locator gets region/cloud defaults appended).
  A flat-file **pattern** asset is the pattern's literal base directory/prefix — the
  Spark-integration convention (dataset = directory, partitions are not datasets).
- **DEV vs QA is two assets, by design.** The spec keys namespace on physical/tenant
  isolation, so the same logical table via two accounts *correctly* differs. Grouping
  across environments is DataQ's job, not the identifier's: the asset row carries
  `env` (from the connection), and the UI groups by `name + env` when asked. No
  internal ID scheme, no cross-env merging at the identity level.
- **Shape (indicative):** `assets(id, namespace, name, env, connection_id FK
  nullable — provenance hint, not identity, first_seen, last_seen,
  owner_user_id nullable — §4)`; unique `(namespace, name)`. Suites resolve their
  target to an `asset_id` on save; runs stamp the resolved `asset_id` at dispatch
  (targets can change; run history shouldn't rewrite). Backfill migration resolves
  every existing `Suite.target`.

## 2. Lineage — pull, don't build (three slices, in order)

We do not build a lineage graph engine (Theme 14 decision, reaffirmed). Three slices,
each independently shippable:

1. **Emit OpenLineage first** — `openlineage-python` (Apache-2.0) hooks in
   `run_service`: START on dispatch, COMPLETE/FAIL on finish, the suite's asset as an
   input `Dataset` carrying **`DataQualityAssertionsDatasetFacet`** (check →
   `assertion`, outcome → `success`, our severity tiers → `severity`) and the
   metrics facet for `metric_value` monitor kinds. Console transport by default,
   `OPENLINEAGE_DISABLED` honored — **dark by default, zero new required infra**, and
   one emitter reaches Marquez, DataHub, and Kafka consumers with no per-catalog code.
2. **dbt-manifest edges — the zero-infra blast-radius floor.** The ADR-0029 provider
   already polls `run_results.json`; the same 3-scheme artifact reader fetches the
   sibling `manifest.json` (present at `raw/dbt/latest/` today, unconsumed). Parse the
   **minimal stable subset** (`parent_map`/`child_map`, per-node
   `database`/`schema`/`alias`/`relation_name`/`resource_type`/`config.materialized`;
   never `compiled_code`/`raw_code` — stream-parse, manifests hit tens of MB at
   thousands of models), gate on `metadata.dbt_schema_version` (v12 is stable across
   dbt-core 1.8→1.11), collapse ephemeral models (null `relation_name`) by recursing
   to their nearest physical ancestors, drop tests/operations. Edges land in a small
   **`lineage_edges` cache** (`upstream_asset_id`, `downstream_asset_id`, `source`,
   `last_seen`) — a cache of external truth refreshed per poll, not a graph we author.
   Harness test bed already green-on-paper: 4 `RETAIL` sources → 4 `ANALYTICS_STG`
   views → 2 `ANALYTICS` dynamic-table marts.
3. **A `LineageProvider` seam for catalog pull** — mirrors `OrchestrationProvider`:
   `get_lineage(asset, depth) → graph`. **Marquez is the reference impl**
   (`GET /api/v1/lineage?nodeId=dataset:{ns}:{name}` — purpose-built, Apache-2.0,
   2 containers on the compose stack; slow release tagging is an accepted risk).
   **DataHub** = same seam, deferred until a user brings one (8 GB-RAM footprint
   disqualifies it from the reference stack; our slice-1 emitter already feeds its
   native OL receiver). **OpenMetadata: do not integrate via SDK** — its
   `openmetadata-ingestion` package is Collate source-available (non-compete), which
   ADR 0031 prohibits; REST-only if ever, behind its own ADR. **Purview parked**
   (Atlas API, preview-grade OL, Azure-only — contra the wind-down).

Blast radius = walk `lineage_edges` downstream from the failing asset, join back to
assets that have suites/checks. Slice 2 alone answers it for dbt-modeled warehouses;
slice 3 widens the sources without changing the query.

## 3. Incident objects — stateful, deduped, evidence-carrying

An **alert** is a per-result notification (fire-and-forget, already shipped: severity
routing, dedup, snooze). An **incident** is the stateful object those signals roll up
into:

- **Lifecycle:** `open → acknowledged → resolved` (+ `resolved_by: user | auto`).
  Timestamps and actor per transition. Reopen = new incident linked to the old one.
- **Dedup vs alerts:** at most **one open incident per `(asset_id, check_id)`**;
  repeat failures while open attach as occurrences (`occurrence_count`,
  `last_seen_at`) instead of new incidents — reusing the alerting dedup key
  discipline (#386's shared severity rank), one level up. Alerts keep firing per
  their own dedup/snooze rules and reference the open incident.
- **Auto-resolve on pass** (first passing result for the pair), on by default,
  per-suite configurable — manual ack/resolve always wins over auto.
- **Payload = the Theme-2 deterministic evidence card, layer 1, no LLM:** upstream
  pipeline run (status + delay vs. history, via `triggered_by` correlation),
  `metric_value` trend (sudden vs. drift), profile-diff of failing vs. last-passing
  batch, sibling checks on the same asset, and downstream blast radius from §2.
  Snapshotted as JSONB at open, refreshed per occurrence; delivered on the existing
  `ResultPublisher` seam so the ticket/webhook arrives with the diagnosis attached
  (Theme-5 tier-1 create-only targets get it for free).
- **Ownership routing:** today the incident routes to the **suite owner** (+ the
  suite's notification config). `assets.owner_user_id` is the later hop — once asset
  owners exist, routing prefers asset owner, falls back to suite owner. No new
  routing engine; it's a recipient-resolution function.

## 4. Authz & surfaces

- **Asset visibility = derived, not granted.** ~~An asset is visible iff the caller can
  `view` ≥1 suite mapped to it; the asset page aggregates **only** the
  suites/results the caller's grants cover (ADR 0027 ladder untouched, 404-no-leak
  for assets wholly outside one's grants).~~ **Superseded by [ADR 0037](adr/0037-workspace-visible-asset-identity.md)
  (#923):** asset identity + lineage topology are visible to every member; the rollup is
  workspace-true; the ADR 0027 grants guard suite-derived detail (composing-suite names,
  runs, results, samples). Incidents stay suite-granted (unchanged). Workspace-admins
  see all (0027); Viewers cap at `view` (0033).
- **Asset metadata mutations** (assign owner, description): **workspace-Admin-only**
  at first — the cheap, safe row on the 0033 matrix; widen to composing-suite `edit`
  later if it chafes.
- **UI phasing** (phase 1 — the asset entity — has no UI): (2) read-only Assets list + asset page (health across suites) →
  (3) lineage panel + incident list on the asset page → (4) navigation inversion
  (sidebar leads with Assets; suites demote to "execution groups"). Phase 4 is where
  DataQ stops feeling like GX-as-a-service — and it is deliberately last.

## Filed build issues (order)

_("Phase-1" in the #596 sense — the first filed set. Within them, "phase 1–4" below
and in §1–§4 means the four build phases: #757 = phase 1, #760 = phase 2, #759/#761
= phase 3, navigation inversion = phase 4.)_

1. **#757** — asset entity + resolution migration + backfill (the prerequisite of everything).
2. **#758** — OpenLineage emission from `run_service` (assertions + metrics facets, dark by default).
3. **#759** — dbt `manifest.json` → `lineage_edges` (parser + cache + blast-radius query).
4. **#760** — read-only asset view (API + UI, grant-derived authz).
5. **#761** — incident objects (lifecycle, dedup, evidence-card payload, routing).
6. **#762** — `LineageProvider` seam + Marquez reference impl (compose stack + docs).

#758/#759 are independent after #757; #760 needs #757 (richer with #759); #761 needs
#757 (richer with #759/#760); #762 extends #759. Phase 4 (navigation inversion) files
only once #760 proves the asset page. The dbt/ADLS test bed outlives Azure — `upload_artifacts.py` already
writes the same layout to `file://`, so the local-first posture (#591) keeps the whole
lineage path testable after the wind-down.

## Guiding principle

Assets are what users **reason about**; suites remain how checks **execute** —
split the noun from the verb, don't replace one with the other. Identity comes from
the open spec, lineage comes from systems that already know it, and the incident is
just the durable, deduped, evidence-carrying view of signals DataQ already produces.
Anything that requires authoring a graph, re-keying authz, or a heavyweight catalog
in the reference stack is out.
