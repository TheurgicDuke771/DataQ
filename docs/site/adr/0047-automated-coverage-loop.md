# ADR 0047 — Automated coverage: system-owned suites over the asset inventory

- **Status:** Accepted (2026-09-30)
- **Date:** 2026-09-29
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0012](0012-monitor-kind-seam.md) (monitor kinds), [0027](0027-suite-permission-model-workspace-admin.md) (suite permissions), [0034](0034-asset-entity-openlineage-identity-lineage-pull.md) (assets, incidents), [0037](0037-workspace-visible-asset-identity.md) (workspace-true asset health), [0038](0038-dq-dimension-classification.md) (dimensions), [0040](0040-warehouse-inventory-sync-table-enumeration-seam.md) (inventory sync), [0042](0042-llm-provider-seam.md) (LLM suggestions).

## Context

DataQ runs what a human authors. A table nobody wrote a check for is not monitored, and a
change nobody predicted (a volume drop, a column that starts arriving empty, a cardinality
collapse, a load that stops) passes unseen. Inventory sync (ADR 0040) already lists every
table a warehouse connection can see, so DataQ knows what it is *not* watching; the anomaly
and aggregate monitor kinds already do the maths. What is missing is the loop
that turns the inventory into running monitors without a person per table.

The two halves differ in how much trust they need. A rolling baseline on a table's row count
asserts nothing about what the data *should* be; it only reports change. A rule such as
"`email` is never null" or "`status` is one of these four values" is a claim about the data,
and a wrong claim is a false alert on every run.

## Decision

1. **Coverage is a connection capability, switched on per connection.** A new
   `auto_coverage` config field (default off) beside `inventory_sync`, offered only on
   connection types with table enumeration (the ADR 0040 seam: Snowflake, Unity Catalog and
   the engine-generic SQL types). Every table the connection's inventory lists is covered,
   except assets marked `auto_coverage_excluded`. Switching it off pauses the connection's
   automatic suites (their schedules are disabled); it deletes nothing.

2. **Automatic monitors are ordinary suites, owned by the system.** One suite per covered
   asset, `suites.origin = 'auto'` (a new column, `'user'` for everything else; one automatic
   suite per asset, enforced by a partial unique index). It holds ordinary checks of existing
   kinds and runs through the same runner, results, incidents, severity routing, notifications,
   UI and MCP as any suite. There is no second result path. The suites have **no human owner**
   (`created_by` is NULL, and their creation is a machine write kept out of the audit log,
   ADR 0041 §2.1). Visibility follows ADR 0027: workspace admins see all of them and can share
   them like any suite, and the asset's health rollup stays workspace-true (ADR 0037), so every
   member sees the verdict. *(Amended 2026-09-30: the first draft named the admin who switched
   coverage on as owner; an owner who never created the suite made no sense for a system-owned
   one.)*

3. **Universal baselines start without approval.** Each automatic suite gets, where the table
   allows:
   - **volume**: an anomaly check on the row count, weekday seasonality on;
   - **freshness**: an anomaly check on the age of the newest value in a timestamp column the
     profiler identifies as a load or event time (by type and name); with none, the suite
     records the gap instead of guessing;
   - **schema shape**: a schema-drift check;
   - **column profile**: one anomaly check whose metric is new, `column_profile`, measuring
     every column's null percentage and distinct count in one pushdown query and baselining
     each series separately in the check's `monitor_baselines` row. Its result names the
     columns that moved; its `metric_value` is the largest deviation. One check per table, not
     two per column, so a 200-column table is one query and one row, not 400 checks.

   Every one of them assumes nothing about correct values: they report change against the
   table's own history, and skip until the history exists, as the anomaly kind already does on a cold start.

4. **Rules that assert something wait for a person.** Profiling and, when an LLM is
   configured, the LLM check suggester propose rules for the covered table (not-null where a column
   has never been null, unique on an identifier, accepted values on a low-cardinality column,
   ranges). They land in a review queue, `check_suggestions` (source `profile` or `llm`,
   status `pending`, `accepted`, `rejected`). Accepting creates the check in the automatic
   suite; rejecting is remembered and the same rule is not proposed again.

5. **Full severity.** Automatic checks warn, fail and go critical like authored ones and alert
   through the suite's notification settings, which default to the workspace channel.
   Defaults: anomaly checks warn at 3 standard deviations, fail at 4, critical at 6; a schema
   change fails. Admins tune them per connection, and a person can edit any check.

6. **The loop never overrides a person.** Checks carry `origin` (`user`, `auto`,
   `suggestion`). The reconciler only adds a universal check that is missing and never edits
   an existing one. A check a person deletes is recorded on the suite and not recreated. An
   edited threshold stays edited.

7. **The reconciler.** A daily beat task, after inventory sync, per covered connection:
   creates missing automatic suites and checks, pauses the suites of tables that aged out of
   the inventory, and refreshes the suggestion queue. Each automatic suite gets a daily
   schedule at a minute derived from the asset id, so a large inventory does not fire at once.
   `AUTO_COVERAGE_MAX_ASSETS` (default 500) caps the tables one connection covers, with loud
   truncation like the inventory's own cap, because every covered table is a daily warehouse
   query cost the customer pays.

8. **False positives are measured, because an alarm that cries wolf gets switched off.**
   Resolving an incident takes an optional `resolution` (`fixed`, `expected_change`,
   `false_positive`); the rate of `false_positive` among automatic-suite incidents is shown
   beside coverage. The coverage figure itself is the share of inventory assets watched by at
   least one suite that ran in the last seven days, split into assets watched only
   automatically and assets with authored checks.

9. **Surfaces.** The connection form gains the switch (admin-only, like every connection
   change); assets show whether they are covered and can be excluded; the review queue is a
   page per connection; the dashboard shows coverage and the false-positive rate. MCP gets read
   tools for coverage and the queue; accepting a suggestion over MCP follows the same `edit`
   gate as creating a check.

## Phases

1. Schema (`suites.origin`, `checks.origin`, `assets.auto_coverage_excluded`,
   `incidents.resolution`), the connection switch, the reconciler with volume, freshness and
   schema-drift checks, and origin badges in the UI.
2. The `column_profile` metric.
3. The review queue: profile and LLM suggestions, accept and reject, in the UI and API.
4. Coverage and false-positive figures on the dashboard, and the resolution choice on incidents.

## Consequences

- Coverage stops depending on someone writing a check per table, which is the gap the
  roadmap names first.
- Each covered table costs a few warehouse queries a day. The cap, the per-asset exclusion and
  the per-connection switch are the controls; the docs must state the cost plainly.
- Full severity means a noisy baseline pages people. The false-positive rate is the signal to
  tune, and it is visible from the first incident.
- Automatic suites are real suites, so every existing surface shows them without new work, and
  a person can take one over by editing it.

## Considered and rejected

- **Hidden baselines** (a separate engine with no visible checks): a second result path and a
  second UI to explain what is watched.
- **Approving every monitor:** coverage would again depend on someone clicking, which is the
  gap this closes.
- **Coverage per asset or workspace-wide:** per asset keeps it manual; workspace-wide takes the
  warehouse-cost decision away from whoever owns each connection.
- **Starting automatic monitors at warn only:** considered to limit early noise; the
  maintainer chose full severity, with the false-positive rate as the check on it.
- **Two checks per column** for null rate and cardinality: a wide table would become hundreds
  of checks and hundreds of queries.
