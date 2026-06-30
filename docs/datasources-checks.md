# Datasources & checks

## Supported datasources

| Datasource | Connection | Check authoring | Execution |
|---|---|---|---|
| Snowflake (DEV/QA/UAT) | account + user + key/PAT | ✅ | ✅ |
| ADLS Gen2 (flat files) | account URL + container, SAS | ✅ | ✅ |
| AWS S3 (flat files) | bucket + region, access key | ✅ | ✅ |
| Unity Catalog (Databricks) | workspace URL + warehouse + PAT | ✅ | ✅ |

## Add a connection

In the UI, **Connections → Add connection**, pick the datasource, fill the type-specific
fields, and **Test** it (a live reachability probe). Credentials are stored in the secret
store (Azure Key Vault in production), never in the database.

## Author a check

1. Create (or open) a **suite** and point it at a **target** — a table (Snowflake/UC), a
   file/path or batch pattern (ADLS/S3).
2. **Add a check.** Two paths:
   - **Form** — pick a Great Expectations expectation (e.g.
     `expect_column_values_to_not_be_null`) + column + optional warn/fail/critical
     thresholds.
   - **Custom SQL** — a read-only SQL rule (rows returned = failures), for SQL datasources.
   - **Monitors** — *freshness* (is the table stale?) and *volume* (row-count in range?)
     on SQL datasources.
3. **Dry-run** to preview pass/fail before saving; use the **column profiler** (nulls,
   distinct count, min/max, top values) to decide thresholds.

## Run it

Run a suite **now**, on a **cron schedule**, or **triggered** by a pipeline (see
**[Orchestration](orchestration.md)**). Results land on the **Results** page and the
**Dashboard** (health score + trends); failures alert per the suite's notification config.

Severity comes from thresholds banding the observed unexpected-percentage
(warn < fail < critical); see ADR 0005 / 0016 for the model.
