# Data-subject-rights runbook — access, export, erasure

> **Who this is for:** whoever operates a DataQ deployment and receives a GDPR
> Art 15 (access) / Art 17 (erasure) / Art 20 (portability) or CCPA/CPRA
> know-or-delete request that touches data DataQ has captured. DataQ is
> customer-deployed (BYOL, ADR 0013) — **the deploying organization is the
> controller** and owns the response to the data subject; this runbook is the
> processor-side half: the mechanism DataQ provides and how to drive it.

## 0. What DataQ can and can't do here

DataQ has **no people-table**. The personal data it can hold incidentally lives in
three places: `results.sample_failures` and `results.observed_value` — small
evidence samples of failing rows, copied out of the controller's own warehouse
tables — and `incidents.evidence`, a stored snapshot of an incident's evidence
card whose `failing_result.observed_value` can carry the same kind of literal
warehouse cell value (see the [DPIA input sheet](dpia-input-sheet.md) for the
full inventory). **This runbook's access/erasure endpoints below cover all three**
(the incident snapshot is matched by the check's current tested column — see §5). A "data subject" in DataQ's own
data is identified the same way the warehouse identifies them: a **`(column,
value)` pair** — e.g. `column=email, value=alice@example.com` — not a DataQ user
id (that is Class 2, workspace-account data, and is handled by ordinary account
deletion/export, not this runbook).

**This runbook does not touch the controller's warehouse.** The warehouse tables
DataQ reads remain the controller's system of record and the controller's
responsibility to act on separately; DataQ's own erasure/export only covers what
it has itself captured and stored.

## 1. Who can do this

Both endpoints below are **Admin-only** (ADR 0033) — the same tier as a connection
credential, because an unredacted export of regulated data is exactly that
sensitive. There is no MCP tool for either capability, for the same reason MCP
carries no connection-credential tool at all.

## 2. Access / export (GDPR Art 15 / 20)

```
POST /api/v1/admin/data-subject-requests/export
{"column": "email", "value": "alice@example.com"}
```

Scans every suite in the workspace and returns every result whose captured
`sample_failures` or `observed_value` names that column/value pair — **unredacted**,
by design: this endpoint IS the subject's own access right, and the read-path
redaction ladder exists to protect *other* people's data from an unrelated viewer,
not this one. Each match carries the suite/check/run it came from and which JSONB
column(s) matched (`matched_in`), so the response is also a ready-made
"categories and volume of records" input for a notification or a portability
export.

Records one `audit_events` **access** event (`data_subject_request.export`,
`exposed: <bool>`) — the same accountability trail as any other regulated-data
read in DataQ.

## 3. Erasure (GDPR Art 17 / CCPA delete)

```
POST /api/v1/admin/data-subject-requests/erase
{"column": "email", "value": "alice@example.com"}
```

Runs the same workspace-wide match, then **surgically** removes only the matching
row/cell:

- from `sample_failures`, only the failing-row entries where `column == value` are
  dropped from whichever list-bearing key held them (`unexpected_index_list`,
  `partial_unexpected_list`, or a comparison bucket) — every other row, and every
  other key in the same blob (counts, summaries), is untouched;
- from `observed_value`'s `unparsed_value` shape, only the value is nulled — the
  `column` name it was captured against survives, since that is metadata about the
  check, not the subject;
- from `observed_value`'s list-shaped case (a set-oriented expectation's full
  distinct-value list), only the matching entry is removed from the
  list.

This is deliberately **not** the retention sweep's granularity (which nulls a whole
`sample_failures`/`observed_value` column once its age crosses
a clock). A GDPR erasure right does not license destroying data belonging to
unrelated rows or unrelated subjects, and an operator debugging *why a check
failed* still needs whatever of the sample isn't the erased subject's. If a
result's entire sample genuinely was one subject's data, the natural retention
clock (or deleting the covering suite/check) is the tool for erasing the rest.

Runs **synchronously** and returns `{matched_count, erased_count}` as totals over
results **and** incident evidence snapshots, with a per-store breakdown
(`matched_result_count` / `erased_result_count` / `matched_incident_count` /
`erased_incident_count`) so an erasure confirmation can say where the data was.
`matched_*` is how many rows contained the subject's data, `erased_*` how many were
actually modified (normally equal; they can diverge only if a match's shape has no
scrub path, which does not exist today but is reported honestly rather than
assumed). Records one `audit_events` **config** event
(`data_subject_request.erase`, carrying both counts) **inside the same transaction**
as the scrub, so a failed write leaves nothing behind and an applied one cannot go
unrecorded — the same pattern as every other audited admin mutation (ADR 0041).

## 4. Exercising it (demo/test verification)

To exercise erasure on a demo user before relying on it for a real request:

1. `POST /api/v1/admin/data-subject-requests/export` with a column/value you know
   is in a demo suite's failing samples; confirm it comes back in `matches`.
2. `POST .../erase` with the same pair; confirm `erased_count >= 1`.
3. Re-run the export — the subject's value should no longer appear, while
   `GET /api/v1/runs/{run_id}` still shows the result and its other data.
   If the failing check had opened an incident, `incident_matches` in step 1 lists
   it and `erased_incident_count` in step 2 counts it; `GET /api/v1/incidents/{id}`
   should show the evidence card with that one value gone and everything else intact.
4. Check `GET /api/v1/admin/audit-events?action=data_subject_request.erase` for
   the recorded event.

## 5. Limits, stated rather than left to be discovered

- **Matching is a full scan of the JSONB sample columns**, not an indexed lookup —
  there is no way to index an arbitrary column-name/value match inside a
  schemaless blob. This is an operator-triggered, low-frequency action, not a hot
  path, and is expected to take longer on a workspace with a large results table.
- **Only the shapes the redaction ladder already knows about are covered**:
  `unexpected_index_list` / `partial_unexpected_list` rows, the three comparison
  buckets (`mismatched`, `additional_in_source`, `additional_in_target`), and both
  `observed_value` PII-bearing shapes. A future sample shape needs its own match/
  scrub branch, mirroring how the retention sweep's own shapes are enumerated.
- **Value matching is string equality** (`str(cell) == value`). A numeric or
  differently-formatted cell that is semantically the same value but not the same
  string representation will not match — state that in the response to the
  subject if a match seems to be missing.
- **`incidents.evidence` is matched by the tested column as of the snapshot's write
  time, not the check's current one.** Both endpoints scan the incident evidence
  snapshot (see §0) as well as `results`. An incident keeps no `Result` row, so the
  column is resolved from the check's version history as of the incident's
  `last_seen_at` — the moment the evidence was last (re)written — the same rule the
  `results` scan applies per row. A check whose `column` was edited after the
  incident last fired therefore no longer hides its snapshot from the match. The one
  residual: a check with **no** version history (created before `check_versions`
  existed and never edited since) resolves to its current column, which for such a
  check is also the only column it has ever had.
- **This does not touch the controller's warehouse** — see §0.

Last reviewed: 2026-09-02.
