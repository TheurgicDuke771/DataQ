# ADR 0040 — Warehouse inventory sync + the table-enumeration seam

- **Status:** Accepted (2026-07-28)
- **Issues:** [#919](https://github.com/TheurgicDuke771/DataQ/issues/919) (inventory sync), [#892](https://github.com/TheurgicDuke771/DataQ/issues/892) (GET_LINEAGE seeds), [#466](https://github.com/TheurgicDuke771/DataQ/issues/466) (interactive pickers, future), [#1064](https://github.com/TheurgicDuke771/DataQ/issues/1064) (S3-compatible namespace)
- **Builds on:** ADR 0034 (assets / "future catalog sync"), ADR 0037 (workspace-visible asset identity), the #911 scoped-pull discipline, the #823 identity discipline

## 1. Problem

Assets materialize from exactly three signals — a suite targets the table, a run
stamps it, or a lineage edge touches it. A table with none of the three is
**invisible**, not merely unmonitored: prod's `dataq_retail.reference.*` schema
(static lookup tables; nothing writes them, so `system.access` has no edges
either) does not appear in the asset view at all. ADR 0037 made asset identity
workspace-visible precisely so a new member can browse and target the estate;
tables that never materialize break that promise silently. Separately, #892's
`GET_LINEAGE` traversal is blocked on a *seed list* (which tables to walk), and
#466 wants interactive table pickers — three consumers of the same missing
capability: **enumerate the tables a connection can see**.

## 2. Decision — one seam, three consumers

A per-datasource **table enumerator**:

```python
enumerate_tables(conn_type, config, secret) -> tuple[AssetIdentity, ...]
```

implemented for `snowflake` and `unity_catalog` in v1 of this slice (the
"warehouse inventory" of #919's title; flat-file/iceberg are §7 non-goals).
It reads the engine's own catalog views —
`INFORMATION_SCHEMA.TABLES` bound to the connection's one database (Snowflake)
and `system.information_schema.tables` (Unity Catalog) — scoped to **the
connection's own boundary, exactly like its lineage pull** (#911): one database
for Snowflake; the workspace for UC, whose connections carry no catalog field
(minus the `system`/`samples`/`__databricks_internal` catalogs). Table types are
restricted to the same domain vocabulary the lineage pull accepts
(base/view/materialized/dynamic/external), system schemas
(`INFORMATION_SCHEMA`) and Snowpark ephemera (`SNOWPARK_TEMP_*`) excluded —
every exclusion in the WHERE clause itself, so a bounded read's LIMIT budget is
never consumed by rows that were going to be discarded.

Consumers:

1. **Inventory sync (#919, this slice):** a new daily beat task
   (`sync_asset_inventory`, wall-clock crontab per #1091) upserts the
   enumeration into `assets` for every **opted-in** connection.
2. **GET_LINEAGE seeds (#892, next slice of this batch — not wired yet):**
   the Snowflake per-seed traversal will walk the same enumeration — no second
   discovery path.
3. **Interactive pickers (#466, future):** the picker endpoint wraps the same
   seam live; nothing here forecloses it.

## 3. Identity — the #823-safe path, by construction

The enumerator builds `AssetIdentity` **directly from the catalog rows in the
engine's own case** — the same pattern the warehouse-native lineage pull uses —
sharing the namespace helpers (`normalize_snowflake_account`, the UC host rule)
with `asset_identity.py`. There is deliberately **no fold/re-derive step**: #823
proved that re-deriving names through config-driven fold rules is where
identity drift lives. A discovered `reference.customers` therefore lands with
byte-identical `(namespace, name)` to what a suite target or lineage edge for
the same table would produce.

## 4. Lifecycle — the sweep already owns retirement

No new column, no new sweep guard. The sync calls
`upsert_assets(..., preserve_provenance=True)`, which advances `last_seen` on
every tick — so a table that still exists never becomes a sweep candidate, and
a table dropped from the warehouse freezes and ages out through the existing
orphan sweep (#770) after `ASSET_ORPHAN_RETENTION_DAYS`. This is ADR 0034's
accrete-not-delete posture doing its job, not an exemption. Consequence,
accepted: if the *sync itself* silently stops, discovered-only assets age out
after the retention window — the same failure the #1091/#1052 staleness work
now makes visible for beat tasks generally.

**Provenance marker (#919 AC-1):** `connection_id` + `env` stamped on insert
(COALESCE semantics — never stolen from a datasource-resolved asset). No
"discovered" badge: ADR 0037 already renders unmonitored assets as neutral full
rows (zero suites, empty scorecard, every dimension uncovered) — that IS the
"known but unmonitored" rendering, and adding a second axis would re-litigate
0037. **No fake health** falls out of the same fact: a discovered asset has no
runs, so its scorecard shows coverage gaps, never a score.

## 5. Opt-in + bounds

- **Per-connection opt-in** (`inventory_sync: true` in the connection's JSONB
  config; a checkbox on the Snowflake/UC connection forms; default off). No
  migration — config is already free-form, and the flag is not a secret.
- **Cap:** `ASSET_INVENTORY_MAX_TABLES` (default 2000) per connection; when the
  enumeration exceeds it, sync the first N in catalog order and log the
  overflow loudly (`inventory_sync_truncated`, with counts) — a silent cap
  reads as "covered everything" (the no-silent-caps rule).
- **Fail-soft per connection**, like the lineage refresh: one unreachable
  warehouse logs and never aborts the sweep of the others.
- **UC grant prerequisite:** `system.information_schema` is not implicitly
  readable — the connection's PAT needs an explicit `SELECT` grant on it (the
  same class of prerequisite as the lineage pull's `system.access` grant). The
  toggle's form hint and deploy/README say so; a failed sync today is visible
  only in worker logs (`inventory_sync_connection_failed`) — a user-facing
  per-connection inventory health signal is filed as a follow-up, not silently
  absent.

## 6. S3-compatible namespace (#1064, decided here)

`_resolve_s3` keys the namespace on bucket alone (`s3://{bucket}`), which was
sound while every `s3` connection was AWS (bucket names globally unique). With
`endpoint_url` (#1063), bucket names are only unique per endpoint. Decision:

- **No `endpoint_url` (AWS):** namespace stays exactly `s3://{bucket}` — the
  OpenLineage spec form, already persisted in prod; stability is the constraint.
- **`endpoint_url` set:** namespace becomes `s3://{host[:port]}/{bucket}`
  (scheme-stripped authority, default ports elided). Distinct stores with the
  same bucket name resolve to distinct assets; the AWS form never changes.

There is nothing to migrate today (no S3-compatible connection exists yet);
that window closes the moment one is created, which is why the convention is
fixed now even though the inventory slice does not enumerate object stores.

## 7. Non-goals (recorded so they are decisions, not omissions)

- **Flat-file / Iceberg enumeration** — object-store "tables" are paths; path-
  grain inventory floods the asset view with objects (the #908 lesson). If
  wanted later it rides the same seam with its own scoping story.
- **Schema-level allow/deny lists** — scope stays "the connection's one
  database" (#911 precedent). A connection wanting narrower inventory scope is
  a future config addition, not implied here.
- **Live pickers (#466)** — the seam is shaped for it; the endpoint + UI are
  not this batch.
- **Inventory staleness surfacing** — a broken sync is visible via its beat
  task logs and, after retention, by assets aging out; a dedicated staleness
  banner (à la #1100) is deliberately deferred until inventory has usage.
