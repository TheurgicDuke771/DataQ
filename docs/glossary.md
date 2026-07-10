# Glossary

| Term | Meaning |
|---|---|
| **Datasource** | A store DataQ runs checks *against*: Snowflake, ADLS Gen2, S3, Unity Catalog, Apache Iceberg. |
| **Orchestration provider** | A workflow engine DataQ *observes* + triggers from: ADF, Airflow, dbt. Not a datasource. |
| **Connection** | Stored credentials + config for one datasource or orchestration provider. |
| **Suite** | A named set of checks that runs against one connection's target. |
| **Check** | One data-quality rule — in v1, a Great Expectations *expectation* (or a freshness/volume monitor). |
| **Expectation** | A Great Expectations assertion about data (e.g. "column not null"). |
| **Monitor** | A non-expectation check kind: *freshness* (staleness) or *volume* (row-count range). |
| **Run** | One execution of a suite. Lifecycle status: queued → running → succeeded/failed/cancelled. |
| **Result** | One check's outcome in a run — status **pass / warn / fail / critical** (or *skip* / *error*). |
| **Severity tier** | warn < fail < critical, derived from thresholds banding the unexpected-%. |
| **Health score** | Severity-weighted, SQL-normalised score (0–100) summarising workspace quality. |
| **Trigger binding** | A mapping `(provider, pipeline/DAG, env) → suite` that runs a suite when a pipeline succeeds. |
| **`pipeline_runs` vs `runs`** | Orchestration runs vs DataQ check runs — linked, never conflated. |
| **Secret store** | Where credentials live — Azure Key Vault (prod) or env/redis (dev), behind one seam. |
| **MCP** | Model Context Protocol — DataQ exposes 8 curated tools at `/mcp` for AI assistants. |
| **ADR** | Architecture Decision Record — `docs/adr/`, one markdown per significant decision. |

## Contact / ownership

Maintainer & docs owner: **@TheurgicDuke771**. File issues / PRs on
[GitHub](https://github.com/TheurgicDuke771/DataQ/issues).
