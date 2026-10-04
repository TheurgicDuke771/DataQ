# ADR 0034 — Asset entity with OpenLineage identity; lineage is emitted/pulled, never built

- **Status:** Accepted
- **Date:** 2026-07-10
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0012](0012-monitor-kind-seam.md) (`metric_value` feeds the metrics facet), [0027](0027-suite-permission-model-workspace-admin.md) / [0033](0033-workspace-roles-rbac.md) (asset authz derives from the suite ladder; asset-metadata mutation is an Admin capability row), [0029](0029-dbt-orchestration-provider.md) (the artifact reader the manifest parser extends), [0031](0031-oss-byol-distribution-licensing.md) (rules out the OpenMetadata SDK)
- **Note:** gap G-d design. The full design note is kept with the maintainers rather than published.

## Context

Gap G-d: DataQ has runs and alerts but no answer to "what broke downstream, who owns it, when was it resolved." Closing it needs lineage, incidents, an asset page, and (later) governance-catalog sync — and all four need the same missing primitive: today "the table" exists only implicitly inside `Suite.target` JSONB, so there is nothing for lineage edges, incidents, or catalog entities to reference. Separately, a lineage *source* already exists unconsumed: the ADR-0029 dbt provider polls `run_results.json`, and the sibling `manifest.json` (the model dependency graph) lands at the same artifacts URI every build.

## Decision

1. **A first-class `assets` table is the shared primitive, shipped first and alone.** Suites resolve their target to an `asset_id` on save; runs stamp it at dispatch; a backfill migration resolves existing targets. Suites remain the execution/authz grain (ADR 0027 untouched) — assets are the browse/reason grain. Two axes, like dbt models-vs-jobs.
2. **Asset identity = the OpenLineage dataset naming spec (`namespace` + `name`), adopted verbatim as the canonical key** — including its normalization rules (quote-strip, engine-returned case, the OL Snowflake account normalization) so our identifiers match `openlineage-dbt`/Spark emissions byte-for-byte, making future emission/pull interop a join instead of a mapping layer. Consequences accepted with it: DEV/QA accounts are *distinct* assets (cross-env grouping is a DataQ UI concern over the asset's `env` column, never an identity merge), and a flat-file pattern's asset is its literal base prefix (the Spark convention).
3. **Lineage is emitted and pulled, never authored.** Three slices, in order:
   - **Emit OpenLineage** from `run_service` via `openlineage-python` (Apache-2.0): START/COMPLETE/FAIL RunEvents with the target asset as input dataset carrying `DataQualityAssertionsDatasetFacet` (+ metrics facet for `metric_value` kinds). Dark by default (console transport, `OPENLINEAGE_DISABLED` honored); one emitter feeds Marquez/DataHub/Kafka with zero per-catalog code.
   - **Parse dbt `manifest.json`** (fetched by the ADR-0029 3-scheme artifact reader) into a **`lineage_edges` cache** (`upstream_asset_id`, `downstream_asset_id`, `source`, `last_seen`) — a refreshed cache of external truth, not a graph we construct. Minimal stable field subset only (`parent_map`/`child_map` + node identity; never `compiled_code`/`raw_code`), version-gated on `metadata.dbt_schema_version` (v12, stable dbt-core 1.8→1.11), ephemeral models collapsed, stream-parsed. This is the zero-infra blast-radius floor.
   - **A `LineageProvider` seam** (mirrors `OrchestrationProvider`) for catalog pull, **Marquez as the reference impl** (purpose-built `GET /lineage` API, Apache-2.0, 2 containers on an opt-in compose profile; stalled release tagging accepted as low-risk for a dev-time reference consumer). The seam's graph carries a **node kind** per node (`dataset`/`job`; `bi_report`/`dashboard` reserved), so pulled downstream nodes are not assumed to be tables — a BI/dashboard node round-trips when a capable catalog (Purview/DataHub) lands. Pulled edges are **connection-less**: unlike a dbt refresh, a catalog pull has no orchestration connection, this relaxed `lineage_edges.connection_id` to **nullable** (additive migration) and added a partial unique index `(upstream, downstream, source) WHERE connection_id IS NULL` as its dedup + prune scope — dbt edges (non-NULL `connection_id`, full unique constraint) are never touched.
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

## Amendment (2026-07-13) — the byte-for-byte join premise was **half wrong**

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

> **Second amendment (2026-07-18, [ADR 0037](0037-workspace-visible-asset-identity.md)):**
> decision 5 and the amendment below are **superseded**. Asset *identity* and
> lineage topology (including column-level pairs) are now visible to every workspace
> member; aggregate rollups are **workspace-true** (computed over all composing suites,
> identical for every viewer); the grant boundary moves to suite-derived detail
> (composing-suite names, runs, results, samples, incidents — the ADR 0027 ladder,
> unchanged, with 404-no-leak intact at the suite grain). The asset detail endpoint
> 200s for every existing asset; the redaction machinery this amendment introduced
> (anonymous nodes, redacted browse rows, count-only column boxes) is removed. The
> amendment below is kept for the historical record of why the derived model failed.

## Amendment (2026-07-13) — decision 5's boundary was drawn in the wrong place

Decision 5 says asset visibility derives from suite grants, and that an asset outside
those grants is **404-no-leak**. Two things were wrong in practice, both found in
production by a user clicking a node.

**1. The lineage graph defeated the no-leak 404.** The graph's walk was never
authz-scoped — deliberately, because blast radius is the point (§2). But that meant a
non-admin received the **name, namespace, env and monitored-status** of assets they held
no grant for, and could click one straight into `Failed to load asset: asset not found`.
The 404 was carefully built so it could not confirm such an asset exists; the graph
confirmed it one click earlier. The guarantee was doing nothing.

The fix is **redaction, not omission**: an inaccessible neighbour is returned as an
anonymous node (id + depth; no identity, `is_monitored` forced false), drawn but not
clickable, alongside a count — *"1 connected asset is outside your access."* Dropping it
instead would have asserted "nothing consumes this table", which is false. We do not fix
a leak by shipping a lie — a lesson this project has hit before. Redaction happens **server-side**: a
name hidden in CSS has still crossed the wire.

**2. "No suites" was treated as "outside your grants". It isn't.** Redaction protects a
*grant* — and an asset nobody has granted is protected by nothing. A suite-less asset (a
raw source table, an unmonitored mart, an asset whose last suite was deleted, its runs and
results cascading with it too) has no runs, no results, no samples behind it. The only
thing being withheld was its **name**, whose existence the lineage graph reveals anyway.

That withholding bought no security and cost real correctness:

- **Browse and the detail endpoint disagreed about what exists.** The asset list was a
  plain `Asset.id IN (SELECT suite.asset_id …)`, so a lineage-discovered table never
  appeared — even for a workspace-admin, whom `get_visible_asset` explicitly *does* hand
  suite-less orphans. A schema visibly containing two assets listed one.
- It would have painted **every unmonitored upstream "🔒 Restricted"** to a non-admin. It
  is not restricted; it is merely unmonitored — a different lie, and one that makes
  lineage unreadable for everyone who is not an admin.

### The amended rule (one rule, three surfaces)

An asset is visible iff **the caller can view ≥1 suite targeting it, OR it has no suites
at all**. A workspace-admin sees everything (ADR 0027). The browse list, the detail
endpoint, and the lineage graph all derive from this one predicate — if they ever diverge,
the graph offers a node the endpoint refuses, which is exactly the bug that surfaced this.

The boundary that stays closed, unchanged: an asset that *someone else monitors* and you
may not view is still 404-no-leak, still absent from browse, and now redacted in the
graph.

**Status of decision 5:** amended. Authz stays derived from suite grants (no asset-level
ACLs — the rejection in *Alternatives* stands). What changes is that the derivation now
says explicitly what an *ungranted* asset is: not a secret, and not a dead link.

## Amendment (2026-09-06) — warehouse-native lineage flipped to default ON

The `refresh_warehouse_lineage` beat task (Snowflake `GET_LINEAGE` / UC `system.access`)
shipped gated behind `WAREHOUSE_LINEAGE_ENABLED`, defaulting `false` — recorded only as an
inline comment (`backend/app/core/config.py`), never in this ADR, because the concern at
the time was narrow: the underlying views need `ACCOUNT_USAGE`/`system.access` grants a
connection's principal might not hold, and erroring a daily beat tick on every warehouse
lacking them looked like unwanted noise.

In practice this meant the lineage graph — the flagship "asset-first" surface this ADR
exists to build — stayed dark for every connection until an admin discovered and flipped
an environment variable nothing in the UI surfaces. Combined with ADR 0040's `inventory_sync`
being opt-in too, the *only* thing that reliably populated `assets` and `lineage_edges` in
practice was authoring a check — the lazy `resolve_and_upsert_asset` fallback this ADR
never intended as the primary path.

**Now default ON.** A principal missing the needed grants degrades per-connection
(`lineage_degraded_reason`/`lineage_last_error`, surfaced via `warehouse_lineage_status` —
§ decision 5's amendment) rather than the whole feature staying invisible; that degraded
path already existed and was exercised in testing before this flip. Set
`WAREHOUSE_LINEAGE_ENABLED=false` to opt a deployment back out wholesale (e.g. a principal
known in advance to lack the grants everywhere, or a cost-sensitive `ACCOUNT_USAGE` budget).

## Amendment (2026-09-27) — column grain: a refinement with a coverage state, and its consumers

Column pairs were already pulled (UC `system.access.column_lineage`, Snowflake
`ACCESS_HISTORY.objects_modified[].columns[].directSources`) and stored on the table edge as
`lineage_edges.columns`. Making column-level lineage a first-class capability — tracing, plus
placement, dedup and semantic propagation for check suggestions — forced four decisions the
original column slice left implicit.

**1. Column grain stays a refinement on the table edge — no column-edge table, no `grain`
discriminator.** A pair is only meaningful beside the table edge that carries it, so storing it on
that row makes the edge's provenance key and prune regime (§3) govern its pairs for free. A
separate column-grain table would need its own dedup and prune rules and could disagree with the
table graph (a column edge surviving the prune of its own table edge). Traversal reads the JSONB
per BFS level (`lineage/columns.py`), bounded by the per-edge cap (500 pairs), a depth cap (25) and
a node cap (500). *Revisit* only if a workspace-wide column query ("every downstream of column X
across 50k assets") needs an index — that would be a derived table rebuilt from these rows, never a
second source of truth.

**2. Absence of pairs has a stated reason (the never-a-silent-empty rule, at column grain).** An edge without pairs
used to be indistinguishable from "the columns are unrelated". Each warehouse pull now stamps
every edge it observed with whether it looked at column grain (`lineage_edges.column_grain`:
`captured` · `unavailable` · `not_supported`; NULL = never recorded). The stamp is **per edge, not
per connection**: an incremental source reads column lineage only for the window an edge was seen
in, so a later successful pull over other edges must not relabel an edge nobody looked at; a merge
keeps `captured` once any pull has looked. Every edge then reports a `column_coverage`:
`recorded` · `none_recorded` (the source reads column lineage, recorded none here — still not proof
of no dependency: a Snowflake view is never a DML write, and a write outside the window is gone) ·
`unavailable` (the column read failed) · `unknown` (not refreshed since this was tracked) ·
`not_captured` (dbt manifest / catalog pull — never column-grain). Several sources on one asset pair
combine to the strongest statement. A column trace treats **every non-`recorded` edge as a gap**
the column may cross, reports it, and marks the trace incomplete and any origin behind it
unconfirmed. Only `recorded` supports a column-level claim, on REST, MCP and the UI alike.

**3. Snowflake's column grain comes from `ACCESS_HISTORY` on every tier.** The feature matrix
claimed `GET_LINEAGE` carries column grain; every captured live row is table-grain, because the
traversal runs at `TABLE` domain, where the column fields are NULL. On an Enterprise account — where
`GET_LINEAGE` answers and the `ACCESS_HISTORY` tier was therefore never reached — Snowflake has
been recording **no column pairs at all**. `GET_LINEAGE`'s table edges are now refined with the
`ACCESS_HISTORY` pairs (the existing live-tuned parser), as a refinement only: a DML-only edge the traversal did
not return is not added, so the table-level prune observation is unchanged. A failed refinement
leaves the table edges intact and records `unavailable`; a *transient* failure additionally stops
the snapshot refresh from replacing stored pairs (it merges instead), while a confirmed denial
still replaces — pairs clear rather than freeze once a grant is revoked. Live-verified (2026-09-27, a
least-privileged reader role): the refinement runs on the GET_LINEAGE tier and records `captured`,
but that account's writes carry no `directSources` and its downstream layer is views, so every edge
honestly reads `none_recorded`. `GET_LINEAGE` at `COLUMN` domain *does* return view column lineage
(probed live); reading it is a separate, budgeted follow-up — the coverage vocabulary already
accommodates edges moving from `none_recorded` to `recorded`.

**4. The column name is matched with its engine's unquoted-identifier fold** (§6's fold, applied to
the column: Snowflake UPPER, Unity Catalog lower, exact elsewhere). The column's existence on the
asset is not verified — DataQ holds no schema snapshot to check against — so a misspelt name traces
to nothing and must be reported as "nothing recorded", never "unrelated".

**Consumers** (each shipped separately):

- **Suggestion placement / dedup is advisory, never a silent drop.** A pair records *derivation*,
  not equality — `amount → daily_revenue` is an aggregate — so only a **pass-through** chain (the
  same folded column name on every hop) may say "an equivalent check already covers this upstream".
  A derived column gets its provenance shown, nothing more. The reviewer decides.
- **Semantic propagation is additive-only.** A column whose upstream origin is classified sensitive
  inherits `sensitive` through recorded pairs; nothing ever inherits `public`, and a column's own
  warehouse verdict always wins. Propagation can therefore only add masking relative to the warehouse-tag
  classification ladder, never remove it. DQ dimensions (ADR 0038) are not propagated: a dimension classifies a
  *check*, not a column, so there is nothing upstream to inherit.

## Amendment (2026-09-28): view and dynamic-table column lineage on Snowflake

Column pairs on the `GET_LINEAGE` tier came only from `ACCESS_HISTORY`, which records DML writes.
A view or dynamic table is never a DML write, so the dbt staging and mart layer, where column
placement matters most, had table edges with no column detail. `GET_LINEAGE` at `COLUMN` domain
does answer for them. After the `ACCESS_HISTORY` refinement, DataQ seeds one `COLUMN`-domain
upstream call per column of each edge still without pairs. The pass is bounded by
`WAREHOUSE_LINEAGE_MAX_COLUMN_SEEDS` with loud truncation, and like the `ACCESS_HISTORY` pass it
is refinement only: it never adds a table edge and never fails the table edges.

Live on the reference account (`DATAQ_READER`), all 8 staging and mart edges went from no column
pairs to 2–11 each, including derivations (`ORDER_TOTAL → LIFETIME_VALUE`,
`LINE_TOTAL → RECONCILED_SUBTOTAL`). The daily refresh took about 65 s longer.
