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

Snowflake supports two auth modes: **password** and **key pair (RSA)**. For key pair,
paste the PEM private key; if the key is passphrase-protected (PKCS#8), fill the optional
**Key passphrase** field — both parts are stored together as one secret and rotate
atomically via **Re-auth**. Leave the passphrase blank for an unencrypted key.

## Author a check

1. Create (or open) a **suite** and point it at a **target** — a table (Snowflake/UC), a
   file/path or batch pattern (ADLS/S3).
2. **Add check** opens a dedicated page (`/suites/<id>/checks/new`): pick a **category**,
   then the check type, then fill its config. The four authoring paths:

### GX expectation (all datasources)

Pick a *Column values* / *Table shape* expectation (e.g. `Column values not null`), name
it, set the column, and optionally band severity with **Warn ≥ / Fail ≥ / Critical ≥**
thresholds over the unexpected-%. Leave thresholds blank for binary pass/fail.

### Custom SQL (Snowflake / Unity Catalog — ADR 0019)

A read-only SQL rule in the Monaco editor: **any rows returned are failures**. Use
`{batch}` as a placeholder for the suite's target table
(`SELECT * FROM {batch} WHERE amount < 0`). Single read-only statement enforced
server-side.

### Freshness monitor (SQL datasources — ADR 0012)

*How stale is the table?* Point it at the load/updated **timestamp column**; the check
measures hours since `MAX(column)` and bands that age with the thresholds. A **fail or
critical threshold is required** — without one, a freshness check could never fail.

### Volume monitor (SQL datasources — ADR 0012)

*Did the load deliver?* Set the expected **min/max row count**; thresholds optionally
band the % by which the count falls outside the range (a spike can exceed 100%), or
leave them blank for binary in-range pass/fail.

Before saving any of them: **Dry-run** previews pass/fail against live data, and the
**column profiler** (nulls, distinct count, min/max, top values) helps place thresholds.

## Run it

Run a suite **now**, on a **[cron schedule](scheduling.md)**, or **triggered** by a
pipeline (see **[Orchestration](orchestration.md)**). Results land on the **Results**
page and the **Dashboard** (health score + trends); failures alert per the suite's
**[notification config](notifications.md)**.

Severity comes from thresholds banding the observed unexpected-percentage
(warn < fail < critical); see ADR 0005 / 0016 for the model, and
**[Best practices](best-practices.md)** for how to pick the bands.
