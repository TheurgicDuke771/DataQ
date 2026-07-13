# ADR 0034 — Asset entity with OpenLineage identity; lineage is emitted/pulled, never built

- **Status:** Accepted
- **Date:** 2026-07-10
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0012](0012-monitor-kind-seam.md) (`metric_value` feeds the metrics facet), [0027](0027-suite-permission-model-workspace-admin.md) / [0033](0033-workspace-roles-rbac.md) (asset authz derives from the suite ladder; asset-metadata mutation is an Admin capability row), [0029](0029-dbt-orchestration-provider.md) (the artifact reader the manifest parser extends), [0031](0031-oss-byol-distribution-licensing.md) (rules out the OpenMetadata SDK)
- **Issue:** [#596](https://github.com/TheurgicDuke771/DataQ/issues/596) (gap G-d design). Full design: [docs/post-v1-assets-lineage-incidents-notes.md](../post-v1-assets-lineage-incidents-notes.md).

## Context

Gap G-d: DataQ has runs and alerts but no answer to "what broke downstream, who owns it, when was it resolved." Closing it needs lineage, incidents, an asset page, and (later) governance-catalog sync — and all four need the same missing primitive: today "the table" exists only implicitly inside `Suite.target` JSONB, so there is nothing for lineage edges, incidents, or catalog entities to reference. Separately, a lineage *source* already exists unconsumed: the ADR-0029 dbt provider polls `run_results.json`, and the sibling `manifest.json` (the model dependency graph) lands at the same artifacts URI every build.

## Decision

1. **A first-class `assets` table is the shared primitive, shipped first and alone.** Suites resolve their target to an `asset_id` on save; runs stamp it at dispatch; a backfill migration resolves existing targets. Suites remain the execution/authz grain (ADR 0027 untouched) — assets are the browse/reason grain. Two axes, like dbt models-vs-jobs.
2. **Asset identity = the OpenLineage dataset naming spec (`namespace` + `name`), adopted verbatim as the canonical key** — including its normalization rules (quote-strip, engine-returned case, the OL Snowflake account normalization) so our identifiers match `openlineage-dbt`/Spark emissions byte-for-byte, making future emission/pull interop a join instead of a mapping layer. Consequences accepted with it: DEV/QA accounts are *distinct* assets (cross-env grouping is a DataQ UI concern over the asset's `env` column, never an identity merge), and a flat-file pattern's asset is its literal base prefix (the Spark convention).
3. **Lineage is emitted and pulled, never authored.** Three slices, in order:
   - **Emit OpenLineage** from `run_service` via `openlineage-python` (Apache-2.0): START/COMPLETE/FAIL RunEvents with the target asset as input dataset carrying `DataQualityAssertionsDatasetFacet` (+ metrics facet for `metric_value` kinds). Dark by default (console transport, `OPENLINEAGE_DISABLED` honored); one emitter feeds Marquez/DataHub/Kafka with zero per-catalog code.
   - **Parse dbt `manifest.json`** (fetched by the ADR-0029 3-scheme artifact reader) into a **`lineage_edges` cache** (`upstream_asset_id`, `downstream_asset_id`, `source`, `last_seen`) — a refreshed cache of external truth, not a graph we construct. Minimal stable field subset only (`parent_map`/`child_map` + node identity; never `compiled_code`/`raw_code`), version-gated on `metadata.dbt_schema_version` (v12, stable dbt-core 1.8→1.11), ephemeral models collapsed, stream-parsed. This is the zero-infra blast-radius floor.
   - **A `LineageProvider` seam** (mirrors `OrchestrationProvider`) for catalog pull, **Marquez as the reference impl** (purpose-built `GET /lineage` API, Apache-2.0, 2 containers on an opt-in compose profile; stalled release tagging accepted as low-risk for a dev-time reference consumer). Built in #762. The seam's graph carries a **node kind** per node (`dataset`/`job`; `bi_report`/`dashboard` reserved), so pulled downstream nodes are not assumed to be tables — a BI/dashboard node round-trips when a capable catalog (Purview/DataHub) lands. Pulled edges are **connection-less**: unlike a dbt refresh, a catalog pull has no orchestration connection, so #762 relaxed `lineage_edges.connection_id` to **nullable** (additive migration) and added a partial unique index `(upstream, downstream, source) WHERE connection_id IS NULL` as their dedup + prune scope — dbt edges (non-NULL `connection_id`, full unique constraint) are never touched.
4. **Incident objects anchor to assets**: at most one open incident per `(asset_id, check_id)`, lifecycle `open → acknowledged → resolved`, occurrences instead of duplicates, auto-resolve-on-pass (per-suite configurable), reopen = a **new** incident linked to the prior one (a resolved row is never mutated back to open), the Theme-2 deterministic evidence card as payload, delivered on the existing `ResultPublisher` seam (no new delivery path), routed to the suite owner today / asset owner later. Alerts remain per-result notifications that reference the open incident.
5. **Asset/incident visibility is derived from suite grants, never separately granted** — visible iff the caller can `view` ≥1 composing suite, aggregation filtered to their grants, 404-no-leak preserved. Asset-metadata mutation (owner, description) starts workspace-Admin-only per the 0033 matrix pattern.

## Consequences

**Positive** — one additive migration unblocks four features (lineage, incidents, asset page, catalog sync); identity interop with the OL ecosystem is free forever; the dbt slice needs no new infrastructure and outlives Azure (`file://` artifacts); the emitter makes DataQ a good OL citizen before asking anything of the ecosystem; no authz re-keying, no graph engine to own.

**Negative / accepted** — OL naming makes cross-environment "same logical table" a two-asset reality the UI must group over; `lineage_edges` freshness is bounded by the artifact-poll cadence; Marquez's release cadence is slow; blast radius is only as complete as the sources feeding the cache (dbt first — warehouse-internal lineage like raw ADF copies won't appear until a catalog/OL source covers it); asset rows accrete as targets change (`last_seen` + a sweep, not deletes, is the cleanup posture).

## Alternatives considered

- **Build our own lineage graph** (the original v1-roadmap "React Flow, ~1 week" sketch) — rejected: authoring and maintaining graph truth is a product in itself; every serious catalog already knows it; pull/emit is cheaper and neutral (Theme 14).
- **Internal surrogate identity for assets** (own ID scheme, map to OL names later) — rejected: the mapping layer is permanent tax, and the OL spec already solves the hard cases (accounts, three-level names, file paths).
- **Asset-level ACLs** — rejected: re-keying authz off suites is a painful migration for little gain (ADR 0027's rationale); derivation keeps one authz source of truth.
- **DataHub as the reference consumer** — rejected for the compose stack (Kafka + OpenSearch + MySQL, 8 GB RAM minimum); it still works via the same emitter/seam when a user brings one.
- **OpenMetadata via its Python SDK** — rejected outright: `openmetadata-ingestion` is Collate source-available with a non-compete clause, prohibited by ADR 0031; REST-API-only integration would need its own ADR.
- **Purview / Atlas now** — parked: preview-grade OL support, Azure-only hosting, contra the wind-down posture.

---

## Amendment (2026-07-13, #823) — the byte-for-byte join premise was **half wrong**

Decision 2 above claims our identifiers "match `openlineage-dbt`/Spark emissions
byte-for-byte, making future emission/pull interop a join instead of a mapping layer."

**Measured against a real producer, that is true of the `namespace` and false of the
`name`.** Real `openlineage-dbt` 1.51.0, fed the real `manifest.json` from a real
Snowflake build:

| | namespace | name |
|---|---|---|
| `openlineage-dbt` | `snowflake://ACCT` ✅ | `DATAQ_DB.ANALYTICS.`**`mart_order_revenue`** |
| DataQ (`asset_identity`) | `snowflake://ACCT` ✅ | `DATAQ_DB.ANALYTICS.`**`MART_ORDER_REVENUE`** |

Same physical table; different bytes. `openlineage-dbt` formats a name as a bare
`".".join([database, schema, table])` with **no case folding**, so it emits whatever its
source spelled — `database`/`schema` from the dbt *profile*, the table from the model
*filename*. The result is mixed case, per segment. **OpenLineage carries no case-folding
rule**, so nothing obliges two producers to agree, and catalogs byte-match.

The consequence was not a degraded join — it was **no join at all**: every seed 404'd
against a perfectly-populated catalog, and the pull was permanently, silently dark
(`unavailable=10 fetched_pairs=0`). Worse, where a catalog held *both* casings (DataQ's
own emitter sends `asset.name` verbatim, so DataQ and dbt naming the same table create
**two datasets**), the seed resolved `200` and returned the *wrong, stale* subgraph.

### What changes

**The identity itself does not.** `assets.namespace`/`name` keep the engine's own case —
that is what makes an asset identity *correct*, and it is what our emission puts on the
wire. No migration, no re-keying.

**A reconciliation step is added at the `LineageProvider` seam** — the mapping layer this
ADR hoped to avoid, but scoped to exactly one boundary (`lineage/identity.py`):

- **A canonical fold**, mirroring how each engine folds an *unquoted* identifier:
  `snowflake://` → UPPER, `unitycatalog://` → lower, and **no fold at all** for
  `abfss://` / `s3://` / Iceberg. That last part is load-bearing: those stores are
  case-**sensitive**, so folding them would not repair a mismatch, it would *invent* one
  and silently merge two distinct objects into one asset.
- **The catalog is enumerated, not guessed** (`LineageProvider.list_datasets`). We seed
  with the catalog's own string, because we cannot construct it — and case variants
  cannot substitute, since the real name is neither all-upper nor all-lower.
- **Exact match wins; the fold is a fallback; an ambiguous fold is refused.** Snowflake's
  quoted `"orders"` really is a different table from unquoted `ORDERS`, so when two
  catalog datasets fold to one key we log and skip rather than draw a wrong edge.
- **Pulled identities are canonicalized on ingest**, so a catalog dataset can never fork
  a second asset for a table DataQ already knows.

### The honest lesson

The original premise was adopted from the OL *spec*; it was never checked against an OL
*implementation*. It survived a green test suite because every fixture was hand-written
by us — so the fixtures agreed with us. The regression tests for this are now driven by a
**captured real payload** (`backend/tests/fixtures/lineage/marquez_*_dbt_real.json`),
which is the only kind that could have caught it.

**Status of decision 2:** amended. Adopting the OL naming spec is still right (the
namespace half genuinely joins, and it is what makes DataQ a good OL citizen), but
"interop is a join, not a mapping layer" is withdrawn — cross-producer name reconciliation
is a permanent, if small, cost.
