# MCP tools

Every tool DataQ's MCP server exposes at `/mcp`, generated from the server itself — the
gate column is the authorization each tool declares (the same table the RBAC test sweeps
run against), and the description is the opening of what an AI assistant is shown.

| | Count |
|---|---|
| Tools | 48 |
| Read-only | 25 |
| Changes state | 18 |
| Live probe | 5 |

No tool creates, edits or re-credentials a connection: every Admin-only capability is a
connection mutation, and a credential must never transit an LLM.

## Read-only

| Tool | Who can call it | What it does |
|---|---|---|
| `export_suite` | `view` on the suite | Export a suite as a portable document you can review or re-create elsewhere. |
| `get_adf_pipeline_status` | any signed-in user | Deprecated alias for ``get_pipeline_status`` — use that tool instead. |
| `get_asset` | any signed-in user | Get one asset's health, the suites that check it, and its lineage neighbours. |
| `get_check` | `view` on the suite | Get one check's full definition by id. |
| `get_check_history` | `view` on the suite | Get one check's recent result history — how it has behaved run over run. |
| `get_column_policy` | `view` on the suite | Get the suite's failing-sample redaction policy — which columns are masked. |
| `get_doc` | any signed-in user | Read a published DataQ user-facing doc page, verbatim. |
| `get_health_score` | any signed-in user | Get the workspace data-quality health score and its trend. |
| `get_incident` | `view` on the incident's suite | Get one incident with its evidence card — why it opened and what else broke. |
| `get_near_misses` | any signed-in user; suite-scoped when a suite is named | Find orchestration triggers that are silently never firing. |
| `get_notification_config` | `view` on the suite | Get a suite's alert notification settings. |
| `get_pipeline_status` | any signed-in user | Get recent orchestration pipeline/DAG runs with their correlated DQ result. |
| `get_run_results` | `view` on the suite | Get the per-check results of one specific run, by run id. |
| `get_run_status` | `view` on the suite | Poll the live, check-by-check progress of a suite run. |
| `get_suite_performance` | any signed-in user | Rank the suites by data-quality health, worst first. |
| `get_suite_results` | `view` on the suite | Get the latest data-quality run results for one suite. |
| `list_assets` | any signed-in user | List the data assets (tables, views, files) DataQ knows about, with health. |
| `list_check_versions` | `view` on the suite | Get one check's edit history — how its definition has changed over time. |
| `list_checks` | `view` on the suite | List the checks (rules and monitors) configured on one suite. |
| `list_connections` | any signed-in user | List the configured datasource and orchestration connections, with health. |
| `list_incidents` | any signed-in user; suite-scoped when a suite is named | List data-quality incidents — what is unresolved *right now*, and since when. |
| `list_runs` | any signed-in user; suite-scoped when a suite is named | List recent suite runs, newest first, with each run's data-quality outcome. |
| `list_schedules` | any signed-in user; suite-scoped when a suite is named | List the cron schedules that run suites automatically. |
| `list_suites` | any signed-in user | List the data-quality suites the current user can access. |
| `list_trigger_bindings` | any signed-in user; suite-scoped when a suite is named | List the orchestration triggers that run a suite when a pipeline succeeds. |

## Changes state

| Tool | Who can call it | What it does |
|---|---|---|
| `ack_incident` | `edit` on the incident's suite | Acknowledge an incident — record that someone is looking at it. |
| `cancel_run` | `edit` on the suite | Cancel a queued or still-running suite run. |
| `create_check` | `edit` on the suite | Add a new check (a Great Expectations expectation) to a suite. Requires edit access to the suite. Returns the created check's id. |
| `create_schedule` | `edit` on the suite | Schedule a suite to run automatically on a cron expression. |
| `create_trigger_binding` | `edit` on the suite | Run a suite automatically whenever an orchestrator pipeline succeeds. |
| `delete_check` | `edit` on the suite | Permanently delete a check from a suite — **and every result it ever recorded**. |
| `delete_schedule` | `edit` on the suite | Delete a suite's cron schedule so it stops running automatically. |
| `delete_trigger_binding` | `edit` on the suite | Delete an orchestration trigger so a pipeline stops running its suite. |
| `import_suite` | workspace Member or Admin | Create a whole suite in one call, from an exported suite document. |
| `resolve_incident` | `edit` on the incident's suite | Resolve an incident — declare the problem over. |
| `restore_check_version` | `edit` on the suite | Put a check back to one of its earlier versions. |
| `set_column_policy` | `edit` on the suite | Set which columns are masked in this suite's failing-sample rows. |
| `snooze_check` | `edit` on the suite | Mute a check's alerts for a while — or un-mute it now. |
| `trigger_suite_run` | `edit` on the suite | Trigger an asynchronous run of a suite's checks; returns a run id to poll. |
| `update_check` | `edit` on the suite | Change an existing check's definition — a partial update. |
| `update_schedule` | `edit` on the suite | Change a suite's cron schedule — its cadence, its timezone, or pause/resume it. |
| `update_suite` | `edit` on the suite | Change a suite's name, description, or **what it runs against**. |
| `update_trigger_binding` | `edit` on the suite | Enable or disable an orchestration trigger without deleting it. |

## Live probe (persists nothing, opens a datasource — gated like a write)

| Tool | Who can call it | What it does |
|---|---|---|
| `dryrun_check` | `edit` on the suite | Preview a check against live data WITHOUT saving it. |
| `list_columns` | `edit` on the suite | List the column names of a suite's table or file. |
| `profile_column` | `edit` on the suite | Profile one or more columns of a table or file on a suite's connection. |
| `suggest_column_policy` | `edit` on the suite | Suggest which of a table's columns hold PII, by profiling it live. |
| `test_connection` | workspace Member or Admin | Check whether a stored connection can actually reach its datasource. |

---

*Generated by `backend/scripts/export_docs_reference.py` — edit the tool docstrings, not this page.*
