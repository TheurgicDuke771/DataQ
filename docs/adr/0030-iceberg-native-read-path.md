# ADR 0030 — Apache Iceberg support: native `pyiceberg` read (v2) behind a self-contained `iceberg` datasource

- **Status:** Accepted (spike decision, 2026-07-07; native impl deferred — see Consequences)
- **Date:** 2026-07-07
- **Deciders:** @TheurgicDuke771
- **Related:** [0003](0003-gx-only-for-v1.md) (GX-only; DQX swap seam), [0011](0011-extensibility-seams-for-deferred-integrations.md) (second-impl-deferred seams — `CheckRunner`/`ConnectionAdapter`), [0012](0012-monitor-kind-seam.md) (freshness/schema-drift monitor kinds this feeds), [0010](0010-provider-agnostic-infrastructure-seams.md)/[0013](0013-marketplace-distribution-and-anti-lock-in.md) (anti-lock-in), [0015](0015-two-connection-comparison-check-model.md) (two-connection model — settled 2026-07-11 at the *check* grain only; Option B's connection→connection ref stays deferred, see its §6). Issue [#286](https://github.com/TheurgicDuke771/DataQ/issues/286).

## Context

Apache Iceberg is increasingly the default open table format on Databricks (Unity Catalog managed tables), Snowflake, and object storage (S3 Tables / ADLS + Polaris). #286 asks whether DataQ should read Iceberg tables natively. The spike (this session) answered two prior questions first, because they collapse most of the scope:

1. **Does reading Iceberg through a query engine need new DataQ code?** No. `SnowflakeCheckRunner` (`add_table_asset` → SQL pushdown, `backend/app/datasources/snowflake.py`) and `UnityCatalogCheckRunner` (`pd.read_sql_table` over the SQL Warehouse, `backend/app/datasources/unity_catalog.py`) talk **SQL to the engine, never to the file format**. Whether an identifier resolves to a native FDN/Delta table or an Iceberg table (`CREATE ICEBERG TABLE … CATALOG_SOURCE=OBJECT_STORE`, or a Databricks foreign/UniForm table) is transparent to the connector, GX, and DataQ. A user can point a suite at an engine-registered Iceberg table **today, with zero new code**, under the existing `snowflake` / `unity_catalog` connection.

2. **Is an Iceberg table just a parquet file (reuse `FlatFileCheckRunner`)?** No. `FlatFileCheckRunner` reads **one object** (`pd.read_parquet`, `backend/app/datasources/flatfile.py:118`). An Iceberg table is a *table*: a metadata pointer → manifest list → manifests → many data files across snapshots/partitions. Reading the raw parquet files naïvely gives **wrong answers** — stale files from expired snapshots (over-count), ignored v2 delete files (rows that don't exist in the table), and per-file physical schema instead of the table's field-ID-reconciled logical schema. Correct reads require an Iceberg client (`pyiceberg`: current snapshot → apply deletes → reconcile schema-by-ID → DataFrame).

So the *only* genuinely new capability #286 can add is a **native read directly from object storage with no query engine in the loop** — `pyiceberg` scan → pandas DataFrame → the existing `gx_runner`, exactly the `FlatFileCheckRunner`/`UnityCatalogCheckRunner` output shape (and the same shape DQX consumes, ADR 0003).

## Decision

### 1. Add native Iceberg as a new datasource; keep engine-level reads as the free zero-code path

- **Engine-level (Snowflake iceberg table / Databricks foreign or UniForm catalog) — supported now, no code.** Documented as the "already works" path; it costs the engine's warehouse compute and only exposes what the engine surfaces as SQL.
- **Native `pyiceberg` — the new build.** `IcebergCheckRunner` reads a table via `pyiceberg` (`catalog.load_table(...).scan()`) and hands the frame to `gx_runner.run_expectations` — thin, like `UnityCatalogCheckRunner`. Materialize via `.to_arrow()` → Arrow-backed pandas (**not** the bare `.to_pandas()` shortcut, which drops to numpy dtypes — keep parity with `FlatFileCheckRunner`'s `dtype_backend="pyarrow"`) for the exact-expectation path; use `.to_arrow_batch_reader()` for the streamable monitor/aggregate path. See #716 for the materialization detail. Payoff over engine-level: **no warehouse compute**, and **direct access to snapshot + schema history** — the natural feed for the reserved `freshness` / `schema_drift` monitor kinds (ADR 0012).

Routing is unchanged in shape: `registry.build_check_runner` dispatches on `connection.type`. `iceberg` is a new sibling entry next to `snowflake` / `adls_gen2` / `s3` / `unity_catalog`. **csv/parquet flat-file reads are untouched** — a `.parquet` *object* stays flat-file; an Iceberg *table* is addressed as a table (namespace.table or metadata location), never inferred from an extension. No rerouting of parquet through Iceberg.

### 2. Format-version 2 baseline; v3 deferred behind a capability gate

**v2 now.** Iceberg format-version 2 is GA and read everywhere that matters — Snowflake Iceberg tables are GA at v2, Spark/Databricks read v2 universally, Delta UniForm emits v2 (`delta.enableIcebergCompatV2`), and `pyiceberg`'s v2 read path (position/equality deletes, schema-by-ID) is mature.

**v3 deferred.** Iceberg v3 (deletion vectors, row lineage, multi-arg/variant transforms) is new and **unevenly supported across engines**, and `pyiceberg`'s v3 support is still maturing. v3-only features go behind a capability flag when engine support converges; v2 stays the baseline. Revisit when the ecosystem catches up (own follow-up, not this build).

### 3. `iceberg` connection = self-contained (Option A), **not** a reference to an ADLS/S3 connection

An Iceberg connection needs two things the flat-file connection lacks: a **catalog pointer** (where the metadata lives) and **storage credentials** (to fetch data files). Two models were considered:

| | **A. Self-contained (chosen)** | **B. Two-connection reference** |
|---|---|---|
| Shape | `iceberg` connection carries catalog config **+ its own** storage credential (one `config` + one `secret_ref`) | `iceberg` connection carries catalog config **+ a reference** to an existing `adls_gen2`/`s3` connection for storage |
| Fits current code | **Yes** — drops into `build_check_runner` with no new plumbing; matches the "one `secret_ref` per connection" invariant every adapter already holds | **No** — the model has no connection→connection FK; runner builders take a single config/secret |
| Lifecycle | Independent — deleting the flat-file connection can't strand the Iceberg one | Coupled — shared credential + the cross-connection FK entangle lifecycles |
| Cost | Storage credential is duplicated if the user also has that ADLS/S3 connection | Cleaner credential reuse |

**Chosen: Option A.** It ships inside the existing `ConnectionAdapter`/`CheckRunner` seams with zero new cross-connection machinery, and it keeps lifecycles independent — which matches the real usage pattern: a user may register an ADLS path for **flat files and Iceberg tables both**, then later delete the flat-file connection and use that storage **only for Iceberg going forward**. Under Option A that deletion is clean — the `iceberg` connection owns its own storage credential and is unaffected, and the existing cascade-delete on the connection FK (ADR 0020) removes only the flat-file connection's own suites/checks. Under Option B the same action would either strand the Iceberg connection's storage credential or cascade into it. Credential duplication is the accepted cost.

**Option B is recorded as the future evolution**, deferred to **ADR 0015** (the pending two-connection model). ADR 0015 is currently scoped to source+target refs for `comparison` checks; generalising "a connection that references another connection" would let the Iceberg catalog credential and storage credential be separate connections. That generalisation is out of scope here.

### 4. Why we are NOT standing up Snowflake Iceberg tables / Databricks foreign catalog in this spike

Deliberately skipped. The SQL runners are **format-transparent by construction** (question 1 above — proven from the code, not by experiment): `add_table_asset` / `read_sql_table` push SQL to the engine and the engine resolves the format. Creating a Snowflake `EXTERNAL VOLUME` + object-store catalog integration, or a Databricks foreign/UniForm catalog, would only **re-confirm what the code already guarantees**, at the cost of live Snowflake/Databricks/Azure setup during the subscription wind-down (Snowflake trial + Azure both end ~2026-07-25; harness compute stopped by default — see project memory). Zero decision value for real setup cost. The engine-level path is documented as supported; its correctness rests on the same transparency the four existing SQL-datasource paths already rely on.

## Consequences

- **Positive:** DataQ "supports Iceberg" *today* at the engine level with no code. The native path, when built, is a thin runner behind the proven seams (more evidence ADR 0011 holds) and unlocks no-warehouse reads plus the snapshot/schema-history feed for `freshness`/`schema_drift` (ADR 0012). Delta **UniForm** tables come along for free: they publish Iceberg v2 metadata over the same parquet files, readable via Unity Catalog's Iceberg REST Catalog — so the native `pyiceberg` path reaches Delta tables (read-only) without a Databricks warehouse. Caveats for UniForm: Iceberg **v2 only**, **no deletion vectors** (mutually exclusive with UniForm), and **async metadata lag** (the Iceberg snapshot trails the latest Delta commit — `converted_delta_version`/`converted_delta_timestamp` track how far).
- **Negative / cost:** the native build adds a `pyiceberg` runtime dependency (CVE-surface + version pin to evaluate before it enters `requirements.txt`), a new `IcebergConnectionAdapter` + `IcebergCheckRunner`, a spec-driven connection form (catalog type + warehouse URL + auth), and duplicated storage credentials vs. an existing ADLS/S3 connection (the Option A trade). The exact-expectation path materializes the whole snapshot (via `.to_arrow()`), memory-bound like the flat-file/UC DataFrame paths — same scale ceiling (G-b scale-aware execution, post-v1); `.to_arrow_batch_reader()` is the streaming escape hatch for the monitor/aggregate path (#716).
- **Neutral / deferred:** the native runner is **not built this cycle**. This ADR records the decision and shape; implementation is a follow-up gated behind the v1.1 portability work and the ADR-0015 settlement (for the eventual Option B). Engine-level Iceberg remains the interim answer.

## Alternatives considered

- **Reuse `FlatFileCheckRunner` (treat Iceberg data files as parquet)** — rejected: wrong results (stale snapshot files, ignored v2 deletes, per-file schema). An Iceberg table is not a file.
- **Engine-level only; never build native** — viable and free, but forfeits the differentiators (no-warehouse reads, direct snapshot/schema metadata for auto-monitors). Kept as the *interim* answer, not the *end* state.
- **Option B two-connection reference now** — rejected for this build (needs connection→connection machinery DataQ lacks and ADR 0015 to settle); recorded as the future evolution.
- **Iceberg v3 baseline** — rejected: uneven engine support and immature `pyiceberg` v3 read; v2 is the portable baseline, v3 behind a later capability gate.
- **Iceberg as a capability flag on the `adls_gen2`/`s3` connection** (not a new type) — rejected: one connection with two runner behaviours fights the registry's "one type → one runner" invariant and muddies the connection model. A sibling `iceberg` type (as `unity_catalog` is its own type over cloud storage) is cleaner.

## Related

- Issue [#286](https://github.com/TheurgicDuke771/DataQ/issues/286) (this spike + the deferred native build).
- ADR 0012 (freshness/schema-drift monitor kinds — Iceberg snapshot metadata is their ideal source).
- [ADR 0015](0015-two-connection-comparison-check-model.md) — written 2026-07-11; settled the two-connection question at the *check* grain (comparison source ref) and explicitly did **not** generalize to connection→connection refs, so Option B remains deferred to its own future ADR.
