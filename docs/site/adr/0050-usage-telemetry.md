# ADR 0050 — Opt-in product-usage telemetry over OpenTelemetry

- **Status:** Accepted (2026-10-20)
- **Date:** 2026-10-20
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0010](0010-provider-agnostic-infrastructure-seams.md) (vendor-neutral seams),
  ADR [0031](0031-oss-byol-distribution-licensing.md) (licensing, rule 40),
  ADR [0041](0041-history-audit-strategy.md) (audit events).

## Context

There is no signal on how DataQ is actually used: which pages people visit, which check types
they author, where authoring is abandoned. That signal is wanted before the first outside user
arrives, and it has to be gathered in a way a security reviewer would accept on sight. These
constraints are non-negotiable:

- **Default OFF**, with an explicit admin opt-in surfaced in Admin → Settings.
- **Event names and counts only** — never a table name, column name, SQL string, or credential.
- **A self-hostable sink or a plain log file first** — no third-party analytics dependency
  (licensing rule 40 and the no-egress posture).
- **The event list is documented and auditable.**
- **The deployment-posture page discloses the setting either way.**

## Decision

1. **The signals are OpenTelemetry.** Every occurrence is recorded twice through the existing
   exporter seam (`core/otel.py`):
    - one increment of the counter **`dataq.usage.events`** (unit `{event}`), whose attribute
      `event` names the event, plus that event's other attributes; and
    - one OTel **log event** named **`dataq.usage.<event>`**, carrying the same attributes.

    DataQ had traces and logs and no metrics signal; this adds a `MeterProvider` of its own
    (and a `LoggerProvider` of its own, so usage events never mix into the application log).
    Resource attribute `service.name` is `dataq-api` or `dataq-worker`. No analytics SDK is
    added: the OTLP metrics exporter ships in the `opentelemetry-exporter-otlp-proto-http`
    package DataQ already pins, and the Azure Monitor metric exporter in the
    `azure-monitor-opentelemetry-exporter` package it already pins.

2. **Two gates, both required.**
    - The operator's **`TELEMETRY_SINK`** (`off` · `file` · `otlp`, default `off`). `off`
      builds no providers at all, so an operator can hard-disable telemetry whatever the admin
      chooses.
    - The workspace admin's **opt-in**, one row in `telemetry_settings` (`CHECK (id = 1)`),
      default off, changed under Admin → Settings → *Usage telemetry* and audited as
      `telemetry_setting.update`. The emitter reads it per event through a 30-second
      per-process cache, so a change lands without a restart; a read that fails counts as off.

3. **Sinks.**
    - **`file`** — JSON lines appended to `TELEMETRY_FILE_PATH`: one `{"type": "event", …}`
      line per log event and one `{"type": "metric", …}` line per counter point (delta
      temporality, flushed every `TELEMETRY_EXPORT_INTERVAL_S`). It works with no collector
      and is what an air-gapped deployment uses.
    - **`otlp`** — the counter to `<OTEL_EXPORTER_OTLP_ENDPOINT>/v1/metrics` and the log events
      to `/v1/logs`, and/or to App Insights when `APPLICATIONINSIGHTS_CONNECTION_STRING` is
      set: the same destinations traces and logs already use. Settings refuse to boot `file`
      without a path or `otlp` without a destination.

    A sink that cannot be built (an unwritable path, SDK drift) logs a warning and leaves
    telemetry off. Emitting never raises into the request, run or tool call it rides on.

4. **The vocabulary is closed and enforced in code.** `core/usage_telemetry.EVENTS` declares
   every event and, per event, every attribute with either a fixed set of allowed strings or
   "non-negative integer". An event outside the registry, an attribute not declared for its
   event, or a value outside its set is **dropped, not exported**, and the rejection logs the
   attribute *key* only. String vocabularies are derived from the model's own enums where one
   exists (check kinds, engines, connection types), and the MCP tool vocabulary is the set of
   registered tool names. A test asserts that a table name, a SQL string, an email address and
   free text are refused on every string attribute of every event.

5. **The browser never talks to a collector.** The SPA posts `page_viewed` and
   `check_editor_abandoned` to `POST /api/v1/telemetry/events` (signed-in users only, at most 20
   events per post, its own rate-limit class `telemetry`, 60/minute per principal and sharing the
   default per-IP ceiling), where they
   are validated against the same registry; the server is the only emitter. The SPA reports a
   route *name* (`suite_detail`), never a URL, so no id or query string can leave.

6. **Disclosure.** `GET /api/v1/admin/deployment` always lists a `usage_telemetry` transfer,
   off or on; when on, its detail names the sink (the file path, or the OTel exporters); when
   off it says why — no opt-in, no sink, or a sink that is configured but failed to start. The
   Admin settings page shows the effective state, the sink, and the full event list inline.

## The events

| Event | Emitted when | Attributes |
|---|---|---|
| `app_started` | the api or a worker starts | `service`: `api` · `worker` |
| `user_signed_in` | a user's first `GET /me` in an 8-hour window, per api process (a session, not a page load) | `auth_mode`: `pat` · `session` · `oidc` · `bypass` |
| `page_viewed` | the SPA navigates (browser-reported) | `route`: a route name such as `dashboard`, `suite_detail`, `check_new`, `admin_settings`, `other` |
| `check_created` | a check is saved, including each check of an imported suite | `kind`, `engine`, `datasource_type`, `origin` (`user` · `auto` · `suggestion` · `import`) |
| `check_editor_abandoned` | the new-check editor is left without saving (browser-reported) | `step`: `expectation` · `config` |
| `suite_run_triggered` | a run is dispatched | `trigger`: `manual` · `schedule` · `mcp` · `probe` · `adf` · `airflow` · `dbt` · `other` |
| `run_completed` | the worker finishes a run | `outcome`, `datasource_type`, `checks` (count), `duration_ms` (omitted when the run has no start or end time) |
| `connection_created` | a connection is saved | `type` |
| `mcp_tool_called` | an MCP tool is called (a call naming no registered tool is not counted) | `tool` (a registered tool name), `outcome`: `ok` · `error` · `cancelled` |
| `llm_feature_used` | an LLM feature is invoked | `feature`: `sql_generation` · `check_suggestion` · `rca_narrative` |
| `alert_delivered` | an alert reaches a channel | `channel`: `teams` · `slack` · `email` · `webhook` |
| `coverage_auto_check_created` | automatic coverage adds a check | `kind` |

The authoritative list, with every allowed value, is the one the Admin settings page shows
(`GET /api/v1/admin/telemetry`), which is read from the same registry the emitter enforces.

## Pointing it at a collector

```bash
TELEMETRY_SINK=otlp
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
```

then opt in under Admin → Settings → *Usage telemetry*. Any OTLP/HTTP receiver works: query
`dataq.usage.events` grouped by `event` for counts, or the `dataq.usage.*` log events for the
per-occurrence stream. With no collector, `TELEMETRY_SINK=file` and
`TELEMETRY_FILE_PATH=/var/lib/dataq/usage-events.jsonl` give the same data as a file.

## Consequences

- DataQ's authors still receive nothing: every destination is the operator's own.
- Adding an event is a code change to the registry, so it shows up in review and in the
  published list; there is no way to emit an unlisted value at runtime.
- The opt-in cache means a switch-off can take up to 30 seconds to stop emission in a
  worker process that has no request session to read through.

## Alternatives rejected

- **A third-party product-analytics SDK** (PostHog, Segment, Mixpanel): a new data processor,
  egress by default, and licences outside rule 40 in several cases.
- **Reusing the application log** for usage events: it would mix opt-in data into a stream that
  is always on, and the PII redactor is a backstop, not a vocabulary.
- **Browser-side OTel export**: it would put a collector endpoint and a second validation path
  in the SPA; the server already has both.
