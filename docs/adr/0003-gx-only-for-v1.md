# ADR 0003 — GX-only for v1; DQX deferred to v1.1 for DLT/streaming

- **Status:** Accepted
- **Date:** 2026-05-24
- **Deciders:** @TheurgicDuke771

## Context

DataQ v1 must run data quality checks across four datasource types: Snowflake, ADLS Gen2, AWS S3, and Unity Catalog (Databricks). Two viable DQ frameworks were considered:

- **Great Expectations (GX Core v1)** — mature, multi-backend, supports Snowflake / Pandas (for flat files) / Spark via DataFrame datasources. Unified suite/expectation/result model. Batch-only.
- **Databricks Labs DQX** — newer, native to Databricks runtime. First-class streaming and Delta Live Tables (DLT) support. Lower runtime overhead on Spark. Unity Catalog-aware. Not multi-backend.

The roadmap defers DLT / streaming functionality to v1.1.

## Decision

**Use Great Expectations (GX Core v1) as the sole DQ framework for v1, across all four datasource types. Defer DQX to v1.1 for the streaming / DLT use case.**

To enable the v1.1 swap cleanly:

- Wrap the Unity Catalog GX execution behind a `UnityCatalogCheckRunner` interface in Week 3.
- DQX in v1.1 will implement the same interface as a `DqxCheckRunner` alongside the existing GX runner.
- A result-shape adapter will normalise DQX output into DataQ's existing `check_results` schema.
- UI exposes `engine: gx | dqx` toggle on UC suites in v1.1.
- Batch UC tables via SQL Warehouse remain on GX even after v1.1 (no migration of working code).

## Consequences

**Positive**
- One framework → one result schema, one suite model, one check-editor UI, one MCP tool contract across all four datasources.
- The 8-week timeline is feasible with a single framework; two would not be.
- The interface-first approach in Week 3 makes the v1.1 DQX add additive, not migrative.

**Negative**
- GX has no streaming support — streaming/DLT DQ is impossible in v1 (acceptable; deferred to v1.1).
- GX-on-Spark for Unity Catalog is higher-overhead than DQX on the same workload. Acceptable for v1 batch volumes.
- GX Core v1 API has drifted across point releases — must pin the version in `environment.yml` and avoid tracking latest.

## Alternatives considered

- **GX for Snowflake / flat files, DQX for Unity Catalog from v1** — rejected. Mixing frameworks in v1 doubles the result-shape handling and forces a check-editor variant per framework; estimated 1 extra week of work for ~0 user-visible benefit in v1 (no streaming requirement yet).
- **DQX-only** — rejected. DQX is Databricks-native and does not cover Snowflake / S3 / ADLS as first-class targets.
- **Build a custom DQ framework** — rejected outright. Out of scope; both GX and DQX are mature.

## Related

- v1.1 streaming/DLT entry in `context/DataQ_platform_roadmap.md` "Deferred to v1.1".
- `UnityCatalogCheckRunner` interface (Week 3 deliverable).
