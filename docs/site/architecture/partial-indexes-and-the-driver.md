# Partial and expression indexes, and the database driver

DataQ's backend talks to PostgreSQL through **psycopg2**, which puts each bind parameter into the
statement text on the client before it is sent. The planner therefore sees constants. Several
indexes depend on that: a partial index is used only when the planner can *prove* the query's
`WHERE` implies the index's predicate, and an expression index only when the expression matches
the query's text.

A driver that binds on the server (psycopg3) sends `$1` instead, and the planner may then choose
a generic plan in which every parameter is unknown. Against such a plan some partial indexes
become unprovable. The query still returns the right rows by another route, which is why nothing
turns red. This page records, index by index, which queries that would affect, so swapping the
driver is a decision and not an accident.

**Today nothing is affected:** the driver is pinned (`psycopg2-binary` in
`backend/requirements.txt`), and one shared test guard (`tests/support/driver_guard.py`) fails
every test that depends on this the moment the driver changes, with a message that points here.

## What defeats a partial index, and what does not

Checked with `EXPLAIN (GENERIC_PLAN)` (PostgreSQL 16 and later), which plans a statement as a
server-binding driver would send it:

| Query shape | Generic plan |
|---|---|
| `status = $1` against a predicate `status IN ('pending', 'running')` or `status = 'succeeded'` | **Misses the index:** a parameter cannot be proved equal to a constant |
| `status IN ('pending', 'running')`, `status = 'succeeded'` written as literals in the SQL | Reaches it |
| `col IS NULL` / `col IS NOT NULL` against a predicate on the same | Reaches it |
| `triggered_by = ANY($1)` against `triggered_by IS NOT NULL` | Reaches it (`=` is strict, so a match implies not null) |
| `jsonb_typeof(x -> 'k') = 'array'` and similar, with the JSON path written in the SQL | Reaches it |
| `lower(email) = lower($1)`, or `= ANY($1)` against an expression whose constants are written as literals | Reaches it (the expression matches; only the compared value is a parameter) |

The first row is the one that matters. A test per row of this table runs the generic plan and
holds the verdict (`tests/db/test_index_binding_audit.py`), so the table cannot drift from what
the planner does.

## The indexes

A unique partial index is a **constraint**, not an access path: `INSERT … ON CONFLICT` names its
predicate in the statement text, which is a constant under any driver, so these are unaffected.

| Index | Table | What it is for | Under server-side binding | If the driver changes |
|---|---|---|---|---|
| `ix_llm_invocations_status_open` | `llm_invocations` | The reaper's scan of invocations still `pending` or `running` | **Affected** if the status is sent as a parameter | Write the two statuses as literals in the statement (`literal_column`), the reaper's two `status ==` statements are affected the same way |
| `ix_llm_invocations_rca_incident` | `llm_invocations` | The latest RCA narrative for an incident (`kind`, `status` and `request ->> 'incident_id'`) | **Affected**: the kind, the status and the JSON key are all parameters | Compile the closed-vocabulary `kind` and `status`, and the JSON key, as literals |
| `ix_runs_results_unrolled` | `runs` | Succeeded runs not yet counted in the result summary. No query reads it yet: the readers arrive with the summary's sweep | **Affected** wherever `status` is a parameter | Every reader must write `'succeeded'` as a literal |
| `ix_runs_triggered_by` | `runs` | The lookup of runs by orchestration marker | Reached | None |
| `ix_notifications_user_unread` | `notifications` | A user's unread notifications, newest first | Reached | None |
| `ix_results_unpurged_created` | `results` | The retention sweep of failing-row samples | **Affected**: the type name `'null'` is a parameter | Compile it as a literal |
| `ix_results_unpurged_observed` | `results` | The retention sweep of list-shaped observed values | **Affected**: the JSON key and the type name are parameters | Compile both as literals |
| `ix_results_unpurged_unparsed` | `results` | The retention sweep of unparsed monitor values | **Affected**: the `?` operand is a parameter | Compile the key as a literal |
| `ix_pipeline_runs_marker` | `pipeline_runs` | The marker a pipeline run triggers suite runs by | **Affected**: the `:` separators are parameters, so the expression no longer matches the index | Compile the separators as literals (`literal_column`) |
| `uq_users_email_lower` | `users` | One account per address, and the sign-in lookup by `lower(email)` | Reached | None |
| `uq_workspace_members_email_lower` | `workspace_members` | One membership per address | Constraint | None |
| `uq_asset_column_classifications_cell` | `asset_column_classifications` | One classification per asset, column and source | Constraint | None |
| `uq_seasonal_periods_name_lower` | `seasonal_periods` | One calendar entry per name | Constraint | None |
| `uq_connections_orchestrator_type_env` | `connections` | One orchestrator connection per type and environment | Constraint | None |
| `uq_incidents_active_asset_check` | `incidents` | One live incident per asset and check | Constraint | None |
| `uq_lineage_edges_up_down_source_nullconn` | `lineage_edges` | One edge per endpoints and source when the connection is null | Constraint | None |
| `uq_notifications_user_run` | `notifications` | One notification per user and run | Constraint | None |
| `uq_runs_suite_triggered_by` | `runs` | One suite run per orchestration event | Constraint | None |
| `uq_suites_auto_per_asset` | `suites` | One automatic suite per asset | Constraint | None |

## Adding an index like these

A new partial or expression index needs a row above. The completeness test fails until it has
one, and for an index an app query should reach, add a case to the generic-plan list in
`tests/db/test_index_binding_audit.py` with the statement as the app sends it. Where a
predicate compares a column with a constant, write that constant as a literal in the query as
well, and the index works under either driver.

## Swapping the driver

Run the audit tests: the shared guard fails first and names this page. Then, for each index
marked affected, make the change in its last column, and change the driver in
`backend/requirements.txt` and the shared guard together. The generic-plan list then says which
queries still miss.
