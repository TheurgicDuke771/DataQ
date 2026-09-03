# DataQ

**Know your data is right before anyone else finds out it isn't.** DataQ runs automated checks
against your tables and files — in Snowflake, Databricks Unity Catalog, Apache Iceberg, ADLS
Gen2, AWS S3 or any S3-compatible store — tells you when something is wrong, and alerts the
team that owns it. It watches your Azure Data Factory, Airflow and dbt pipelines and runs the
checks the moment a load finishes.

<video class="clip" autoplay loop muted playsinline poster="assets/videos/tour.jpg">
  <source src="assets/videos/tour.mp4" type="video/mp4">
</video>

*A ten-second tour: dashboard, assets, connections, suites, results.*

<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } **Try it in five minutes**

    ---

    One `docker compose up`, no cloud account, no identity provider. Sign in with an emailed
    code and explore seeded demo data.

    [:octicons-arrow-right-24: Install](get-started/install.md)

-   :material-school:{ .lg .middle } **Learn it in an hour**

    ---

    Four short tutorials: your first suite, your first alert, running it automatically, and
    asking an AI assistant.

    [:octicons-arrow-right-24: Get started](get-started/index.md)

-   :material-book-open-variant:{ .lg .middle } **Look something up**

    ---

    Every check type, every REST endpoint, every MCP tool — generated from the code, so it
    cannot drift.

    [:octicons-arrow-right-24: Reference](reference/index.md)

</div>

## What you can do with it

| | |
|---|---|
| **Catch the incidents that page people** | Freshness, volume and schema-drift monitors notice a load that did not arrive, arrived half-empty, or changed shape — before a value-level rule ever runs. |
| **Author precise checks without code** | Twenty-five vetted Great Expectations types, custom SQL with a live dry-run, comparisons against a second dataset, anomaly detection on a learned baseline. Let the built-in assistant suggest checks from a column profile. |
| **See the whole estate** | An asset view rolls health up per table, with lineage pulled from dbt, your catalog or the warehouse itself, and incidents anchored to the asset they hit. |
| **Alert the right people, once** | Teams, Slack, email and webhooks, routed by severity, de-duplicated so a broken check reports when it breaks, not on every run. |
| **Run it where the data lives** | Reference deployments on Azure Container Apps and AWS ECS; a single-host Docker stack for evaluation; bring your own identity provider or none at all. |
| **Let assistants do the work** | Forty-eight MCP tools expose the same actions to Claude, Copilot and Cursor, every one honest about what it cannot see. |

## Who it is for

- **Data engineers and SREs** author checks, wire pipelines, triage failures.
- **Analysts and QA** see what passed or failed and why, with redacted failing rows.
- **Stakeholders** get a health score and a trend, not a Slack thread.

## Quickstart

```bash
curl -O https://raw.githubusercontent.com/TheurgicDuke771/DataQ/main/docker-compose.ghcr.yml
export OPENBAO_TOKEN=$(openssl rand -hex 16)     # root token for the bundled vault
export DATAQ_SIGNIN_EMAIL=you@example.com        # the address allowed to sign in
docker compose -f docker-compose.ghcr.yml up
```

Open `http://localhost:3000`, type the address you exported, and read the six-digit code in the
bundled inbox at `http://localhost:8025`. The stack comes up migrated and seeded with demo
data; nothing leaves your machine. Full flow, other sign-in modes and the from-source path:
[Install](get-started/install.md).

## Where next

- Running it for real? [Deployment](operate/deployment.md), then
  [Security & data handling](security/overview.md) for the review that follows.
- Curious how it is built? [Architecture](architecture/overview.md) and the
  [decision records](adr/README.md) behind it.
- Scripting it? The [REST API](reference/rest-api.md), or an assistant over
  [MCP](guides/mcp-setup.md).
