# Glossary

| Term | Meaning |
|---|---|
| **Datasource** | A store DataQ runs checks *against*: Snowflake, ADLS Gen2, S3, Unity Catalog, Apache Iceberg. |
| **Orchestration provider** | A workflow engine DataQ *observes* + triggers from: ADF, Airflow, dbt. Not a datasource. |
| **Connection** | Stored credentials + config for one datasource or orchestration provider. |
| **Suite** | A named set of checks that runs against one connection's target. |
| **Check** | One data-quality rule — a Great Expectations *expectation*, or a monitor kind (below). |
| **Expectation** | A Great Expectations assertion about data (e.g. "column not null"). |
| **Monitor** | A non-expectation check kind: *freshness* (staleness), *volume* (row-count range), *schema_drift* (column add/drop/retype vs a baseline), *anomaly* (rolling z-score over metric history), or *comparison* (cross-dataset reconciliation). |
| **Dimension** | A check's DQ-dimension classification (ADR 0038): accuracy / completeness / consistency / integrity / timeliness / uniqueness / validity. NULL = unclassified, rendered as a coverage gap. |
| **Run** | One execution of a suite. Lifecycle status: queued → running → succeeded/failed/cancelled. |
| **Result** | One check's outcome in a run — status **pass / warn / fail / critical** (or *skip* / *error*). |
| **Severity tier** | warn < fail < critical, derived from thresholds banding the unexpected-%. |
| **Health score** | Severity-weighted, SQL-normalised score (0–100) summarising workspace quality. |
| **Trigger binding** | A mapping `(provider, pipeline/DAG, env) → suite` that runs a suite when a pipeline succeeds. |
| **Asset** | The table/path a suite targets, as a first-class entity — OpenLineage `(namespace, name)` identity (ADR 0034); workspace-visible, with suite-derived detail behind grants (ADR 0037). |
| **Lineage edge** | A cached upstream→downstream asset dependency pulled from systems that already know it (dbt manifest, warehouse APIs) — never authored in DataQ. |
| **Incident** | The stateful, deduped object failing results roll up into: `open → acknowledged → resolved`, ≤1 active per (asset, check), evidence card attached (ADR 0034). |
| **`pipeline_runs` vs `runs`** | Orchestration runs vs DataQ check runs — linked, never conflated. |
| **Secret store** | Where credentials live — Azure Key Vault / AWS Secrets Manager / OpenBao (prod) or env/redis (dev), behind one seam. |
| **MCP** | Model Context Protocol — DataQ exposes 47 curated tools at `/mcp` for AI assistants (24 read-only, 18 that change state, 5 live-probe tools gated like writes). |
| **ADR** | Architecture Decision Record — `docs/adr/`, one markdown per significant decision. |

## Contact / ownership

Maintainer & docs owner: **@TheurgicDuke771**. File issues / PRs on
[GitHub](https://github.com/TheurgicDuke771/DataQ/issues).
