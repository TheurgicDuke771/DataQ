# Concepts

A few terms make the rest of the docs click.

## The one distinction: datasource vs orchestration

- **Datasources** are stores you write data-quality checks *against*: **Snowflake**,
  **ADLS Gen2**, **AWS S3**, **Unity Catalog (Databricks)**.
- **Orchestration providers** are workflow engines DataQ *observes* — **Azure Data
  Factory (ADF)** and **Apache Airflow**. DataQ does three things with them: monitor
  pipeline/DAG runs, detect failures in near-real-time, and **trigger a check suite when a
  pipeline finishes successfully**. They are **not** datasources — you never write checks
  against ADF/Airflow.

!!! note
    This split is load-bearing throughout the product. Orchestration runs live in
    `pipeline_runs`; data-quality runs live in `runs`. They're linked, never conflated.

## Core objects

- **Connection** — credentials + config for one datasource or orchestration provider.
- **Suite** — a named collection of checks that runs against one connection's target
  (a table, a file/path, or a Unity Catalog table).
- **Check** — a single data-quality rule. In v1 every check is a **Great Expectations
  expectation** (e.g. "this column is never null", or a custom SQL rule). Freshness and
  volume **monitors** are also supported.
- **Run** — one execution of a suite. Each check produces a **result** with a status:
  **pass / warn / fail / critical** (plus *skip* / *error* for operational outcomes).
- **Severity & health score** — warn/fail/critical are weighted into a workspace **health
  score** and trend so you can see quality moving over time.

## How a check runs

You author a suite → run it manually, on a **schedule**, or **triggered** by a pipeline →
a Celery worker executes the checks via Great Expectations against the datasource →
results are stored, the health score updates, and alerts fire (Teams / Slack / email) for
failures. See **[Architecture](architecture.md)** for the flow.
