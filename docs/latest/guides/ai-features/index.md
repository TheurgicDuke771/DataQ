# AI features (LLM)

DataQ can call a language model **you choose** for three jobs: turn a sentence into a
custom-SQL check, propose a starter set of checks for a table, and explain why a check
failed. All three are **off until an Admin configures a provider**, every call is recorded,
and no failing-sample row ever leaves your deployment. This page shows each one with what goes in, what
comes back, and what the model is never shown.

<video class="clip" autoplay loop muted playsinline poster="../../assets/videos/configure-llm.jpg">
  <source src="../../assets/videos/configure-llm.mp4" type="video/mp4">
</video>

*Admin → LLM provider: point DataQ at a model, press **Test**, save. Here it is a local
Ollama server; the same form takes Anthropic, Azure OpenAI, Bedrock or any
OpenAI-compatible endpoint.*

## Turn it on

**Admin → LLM provider.** Pick a provider, enter the model name and (for OpenAI-compatible
endpoints) the base URL, paste the API key, and press **Test** — it makes one tiny call
with the values in the form and reports the model and latency. Nothing is saved by Test,
but the probe itself is recorded like any other call, and it runs whether or not the
enable switch is on. Then **Save** and switch on *Enable outbound LLM calls*.

![The LLM provider panel on the Admin page: provider, model, base URL, API key, structured-output mode and the enable switch](../assets/screenshots/admin-llm-settings.png){ .screenshot }

*The key is write-only: it is stored in your secret store, never shown again, and never
forwarded if you later change the provider or endpoint — you re-enter it on purpose.*

| Provider | Base URL | Notes |
|---|---|---|
| **Anthropic** | leave blank | Native structured output. |
| **OpenAI-compatible** | required | Azure OpenAI (`…/openai/v1`), AWS Bedrock, vLLM, TGI, **Ollama** (`http://host:11434/v1`). |

**Structured output** decides how DataQ gets JSON back. *Native* uses the provider's own
schema or tool-calling support; *Prompt-JSON fallback* embeds the schema in the prompt and
repairs one parse failure — use it for small local models. Either way DataQ **re-validates
every response** against the same rules a human's input would face, so the mode changes
reliability, never safety.

!!! info "What the model can and cannot see"
    Nothing sends failing-sample rows. Beyond that, each feature sends something different:

    - **SQL generation** — the target's column names, plus null and distinct counts per
      column if you ask for the profile. No values.
    - **Check suggestions** — column names with null/distinct counts, min/max, and the
      **five most frequent values** of each column. Columns your column policy or warehouse
      tags mark sensitive are blanked to a mask; an unclassified free-text column is not,
      so classify before you enable this on a table with one.
    - **Root-cause narrative** — the stored evidence card (check, asset, pipeline and
      downstream *identifiers*, statuses, metric values, the observed and expected values
      after the same column-policy / warehouse-tag redaction every results surface applies)
      plus up to 180 points of that check's result history from DataQ's own database. No
      column profile at all.

    The full transfer-vector accounting is in
    [Security & data handling](../security/overview.md#what-can-move-data-out), and the live
    list for *your* deployment is on the Admin page above the panel.

## Explain a failed check

When a check fails, DataQ opens an **incident** and captures an **evidence card** at that
moment: the failing result, the metric's recent trend, the other checks in the same run,
the upstream pipeline run if one triggered it, and the downstream tables that depend on the
asset. You can read the card yourself from the asset page.

![The incident evidence drawer on an asset: the failing result, expected vs observed values, the metric trend and the sibling checks from the same run](../assets/screenshots/incident-evidence.png){ .screenshot }

*The model is handed this card plus a longer result history for the check, both from
DataQ's own database — nothing is fetched fresh from the warehouse, so the narrative
describes the failure as it was captured, not as the table is now.*

Ask for the narrative with the incident id:

```bash
curl -X POST https://<your-dataq-host>/api/v1/llm/rca_narrative \
  -H "Authorization: Bearer dq_live_…" -H "Content-Type: application/json" \
  -d '{"incident_id": "669964c0-bf09-4e93-960d-7866f7e73b0f"}'
# → 202 {"invocation_id": "5e7c…", "status": "pending"}
```

The call is queued to a worker; poll `GET /api/v1/llm/invocations/{invocation_id}` until
`status` is `succeeded` or `failed`. This is what a 14-billion-parameter local model
returned for the incident above — a `status in set` check on an orders table where 18 % of
rows carried a status outside `new / paid / shipped / cancelled`, up from 6 % the run
before:

```json
{
  "summary": "The 'status in set' check likely failed because 18% of the order statuses in the ORDERS table do not match the expected set 'new', 'paid', 'shipped', 'cancelled'. Recent check failures show this percentage rising from 6% to 18%, indicating an issue with order status data integrity.",
  "ranked_hypotheses": [
    {
      "cause": "Data entry errors or updates involving unsupported values have been introduced in the status column.",
      "confidence": "high",
      "evidence_refs": ["check_history", "metric_trend", "failing_result"]
    },
    {
      "cause": "Upstream data sources or processes feeding status values have changed, introducing values outside the allowed set.",
      "confidence": "medium",
      "evidence_refs": ["kind_detail", "check_history", "metric_trend"]
    }
  ],
  "blind_spots": [
    "no upstream orchestration pipeline run is linked — either this run wasn't pipeline-triggered, or none could be matched",
    "no downstream lineage is recorded for this asset — cannot rule out downstream impact",
    "no before/after column profile — profile comparison isn't implemented yet"
  ],
  "suggested_next_checks": [
    "Examine recent changes in data entry practices or source data",
    "Verify if there have been changes in upstream systems providing order status updates"
  ]
}
```

Three things make this usable rather than merely plausible:

- **Every hypothesis cites evidence layers** from a closed list (`failing_result`,
  `metric_trend`, `sibling_checks`, `upstream_pipeline_run`, …). A hypothesis that cites
  none is dropped before you see it; if the model produces nothing citeable, the
  invocation **fails** instead of returning an empty story.
- **`blind_spots` is computed by DataQ, not the model.** It lists what the evidence card
  could not see — no linked pipeline run, no lineage — and the prompt forbids the model
  from asserting confidence over those gaps.
- **The narrative reaches the next alert.** Once one exists for an incident, the
  Teams / Slack / email / webhook line for that incident carries its one-line takeaway —
  unless other suites also check the same asset, in which case the alert withholds it, since
  the narrative may name a check the alert's audience is not granted to see:

```text
Incident 669964c0 (status in set) — critical, occurrence 2 · not pipeline-triggered (manual
or scheduled run); 2/3 other check(s) in this run also failing; no downstream lineage
recorded · AI summary: The 'status in set' check likely failed because 18% of the order
statuses in the ORDERS table do not match the expected set 'new', 'paid', 'shipped',
'cancelled'. Recent check failures show this percentage r…
```

(The clause is capped so a Slack section never overflows. The generic **webhook** channel
is the exception: its JSON payload carries the whole narrative object, so treat a webhook
receiver as a full reader of it.)

Generating the narrative needs `view` on the incident's suite. Reading the invocation back
is narrower: only the person who requested it, or a workspace Admin, can poll it — a
colleague with the same suite grant gets a 404 for your invocation id and has to request
their own. It never re-runs the check and never changes the suite.

## Write SQL from a sentence

Custom-SQL checks are the most flexible check type and the slowest to author. Describe the
rule in plain language against a suite whose target is a table on a SQL datasource
(Snowflake or Unity Catalog):

```bash
curl -X POST https://<your-dataq-host>/api/v1/llm/sql_generation \
  -H "Authorization: Bearer dq_live_…" -H "Content-Type: application/json" \
  -d '{
    "suite_id": "<suite id>",
    "description": "Every order must have a positive total amount, and no order may be dated in the future",
    "include_profile": true
  }'
```

DataQ lists the target's columns from the warehouse, adds masked profile statistics if you
asked for them, and asks for **one read-only `SELECT`**. The model's SQL then passes the
same validator a human's custom SQL does — a single `SELECT` / `WITH` statement, nothing
chained after a `;`, no write or DDL keywords — before it is ever stored. This is what a
live Snowflake connection and the same 14-billion-parameter local model returned for the
description above, against an orders table:

```json
{
  "sql": "SELECT order_number, order_total, order_ts FROM RETAIL.ORDERS_HEADER WHERE order_total <= 0 OR order_ts > CURRENT_TIMESTAMP()",
  "explanation": "Identifies orders with a non-positive total amount or with an order timestamp in the future."
}
```

Paste the result into the custom-SQL editor like any hand-written query and **dry-run it**
before saving — generation never creates a check by itself. For a rule that spans several
tables on the *same* connection, add `additional_tables` (up to four); cross-connection joins
are refused structurally, because reconciling two datasources is what the
[comparison check](datasources-checks.md) is for.

## Suggest checks for a table

For a suite with a table target and no checks yet, ask for a starter set:

```bash
curl -X POST https://<your-dataq-host>/api/v1/llm/check_suggestions \
  -H "Authorization: Bearer dq_live_…" -H "Content-Type: application/json" \
  -d '{"suite_id": "<suite id>"}'
```

DataQ profiles the table's columns live (masked statistics only), sends the profile with a
**closed vocabulary of check types** — a subset of the check editor's catalog: column-level
value, null, set, range, regex and uniqueness checks, but no row-count or cross-column
types — and gets back candidate checks, each with a name, a rationale and a full config. A
**freshness** suggestion is offered only when the suite has an enabled pipeline trigger
binding, because its threshold is grounded in that pipeline's observed cadence, not in the
column profile — when offered, it carries a `fail_threshold_hours` field instead of a
`config` threshold. Every candidate then goes through the validator `create_check` uses; a
suggestion naming a column the table doesn't have is refused the same way, and one that
fails is dropped and reported under `rejected` — if *all* fail, the invocation fails rather
than returning "nothing". This is what the same live connection and model returned for a
suite with no trigger binding (so no freshness candidate):

```json
{
  "suggestions": [
    {
      "expectation_type": "expect_column_values_to_not_be_null",
      "name": "store_id_not_null_for_store_orders",
      "rationale": "Store ID should not be null for orders placed in stores.",
      "config": {"column": "store_id", "mostly": 0.318},
      "dimension": "completeness"
    },
    {
      "expectation_type": "expect_column_distinct_values_to_be_in_set",
      "name": "valid_promo_ids",
      "rationale": "Ensure only valid promo IDs are in the table.",
      "config": {
        "column": "promo_id",
        "value_set": ["PROMO-0001", "PROMO-0002", "PROMO-0009", "PROMO-0011", "PROMO-0012"]
      },
      "dimension": "validity"
    },
    {
      "expectation_type": "expect_column_values_to_match_regex",
      "name": "order_number_format",
      "rationale": "Verify that all order numbers follow the expected format.",
      "config": {"column": "order_number", "regex": "^ORD-[0-9]{4}$"},
      "dimension": "validity"
    }
  ],
  "rejected": [],
  "coverage_warnings": []
}
```

`coverage_warnings` is computed by DataQ, not the model: it lists pipeline trigger
bindings that *nearly* matched this suite (right pipeline, wrong environment) — a gap no
column profile can reveal. Suggestions are proposals: nothing is created until you add them,
so one you disagree with costs a glance. Use them as the first pass on a new table, then read
the asset page's per-dimension coverage to see what they left out.

## What is recorded, what it costs, who may call

- **Every call is a row in `llm_invocations`** — kind, requester, suite, token counts,
  duration, the validated response or the reason it failed. The Admin **Test** probe is
  recorded too. Your own invocations are readable by you; all of them by an Admin.
- **Permissions follow the suite.** SQL generation and check suggestions need `edit` on the
  suite; the narrative needs `view` on the incident's suite. There is no LLM tool over MCP
  and no Admin-only feature — an assistant holding your token can do exactly what you can.
- **Rate-limited under its own class**, separate from the REST ceiling, so a burst of
  generations cannot crowd out normal API traffic — and vice-versa.
- **Off is really off.** With the switch disabled, all three endpoints return an
  *LLM not configured* error and nothing is sent anywhere — the worker re-checks the switch
  before every call, so disabling it mid-flight fails the queued generation too.

Design record: ADR [0042](../adr/0042-llm-provider-seam.md).
