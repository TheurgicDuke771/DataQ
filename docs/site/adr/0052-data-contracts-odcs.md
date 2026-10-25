# ADR 0052 — Consumer-side data contracts: a `contracts` entity with ODCS v3 import and export

- **Status:** Accepted (2026-10-25)
- **Date:** 2026-10-25
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0012](0012-monitor-kind-seam.md) (monitor kinds the SLA block maps onto), [0016](0016-severity-derivation-semantics.md) (thresholds band the unexpected-%), [0014](0014-reconciliation-comparison-check-kind.md) / [0015](0015-two-connection-comparison-check-model.md) (the adjacent `comparison` kind — two datasets, not one contract), [0019](0019-custom-sql-check-kind.md) (custom SQL, which ODCS `sql` rules ride), [0027](0027-suite-permission-model-workspace-admin.md) / [0033](0033-workspace-roles-rbac.md) (the grants a contract inherits), [0034](0034-asset-entity-openlineage-identity-lineage-pull.md) (incidents and their evidence card), [0037](0037-workspace-visible-asset-identity.md) (`restricted_suite_count` on a grant-filtered rollup), [0036](0036-connection-anchored-check-engines.md) / [0038](0038-dq-dimension-classification.md) (engine and dimension on a check), [0041](0041-history-audit-strategy.md) (audit of the import), [0047](0047-automated-coverage-loop.md) (the reconciler shape re-used here), [0049](0049-profiler-pii-classification-source.md) (where a contract's `classification` lands). CONTRIBUTING rule 45 names ODCS as the required format.

## Context

A DataQ suite is a bag of checks a person authored against one target. Nothing records *why*
a check exists, and a team that publishes a dataset with a written contract (schema, quality
rules, SLA) has no way to hand that contract to the consumers' DataQ and get the checks that
enforce it, nor to read back "is clause X holding?" from a run. This decision scopes the
**consumer side only**: validate data already at rest against a contract, using the existing
suite/check execution. Producer-side (pre-publish, in the producer's own pipeline) validation
is out of scope, for the reason recorded when an embeddable client library was rejected — no
second producer asking for it.

The [Open Data Contract Standard](https://bitol-io.github.io/open-data-contract-standard/)
(Bitol, Linux Foundation AI & Data; Apache-2.0) is the only open, vendor-neutral contract
format with a published JSON Schema. Its current line is **v3** (`apiVersion: v3.2.0` at the
time of writing; v3.0.0 → v3.2.0 are additive minors). Rule 45 makes it mandatory here: a
contract is read and written as an ODCS document, and anything DataQ-specific goes into the
standard's `customProperties`, never into a parallel format.

What ODCS carries and what DataQ can execute do not line up one-to-one:

- ODCS describes a contract with **many schema objects** (tables); a DataQ suite has **one
  target**.
- ODCS `quality` rules are `library` metrics (`nullValues`, `missingValues`, `invalidValues`,
  `duplicateValues`, `rowCount`) with comparison operators, `sql` rules returning a scalar
  compared by an operator, `custom` rules with an `engine` + opaque `implementation`, and
  `text` rules that are prose. DataQ runs the GX expectations in `ALLOWED_EXPECTATIONS`, the
  monitor kinds (`freshness`, `volume`, `schema_drift`, `anomaly`, …) and validator-gated
  custom SQL that counts **unexpected rows**.
- ODCS `slaProperties` are a flat list (`property`, `value`, `unit`, `element`); only two of
  the twelve defined properties (`frequency`, `latency`) are a data-quality check at all.
- ODCS `dimension` has seven values (`accuracy`, `completeness`, `conformity`, `consistency`,
  `coverage`, `timeliness`, `uniqueness`); DataQ's closed vocabulary (ADR 0038) has seven that
  differ in two places (`validity`, `integrity` vs `conformity`, `coverage`).
- ODCS `severity` is free text with `info`/`warning`/`error` as examples; DataQ has
  `warn`/`fail`/`critical` thresholds.

## Decision

### 1. One `contracts` row per contract **version**; the document is stored verbatim

`contracts` (`id`, `odcs_id` — the document's required `id` —, `name`, `version`, `status`,
`domain`, `tenant`, `spec` JSONB, `unmapped` JSONB, `api_version`, `created_by`, timestamps;
unique `(odcs_id, version)`). The standard's `id` is the identity: two domains may each publish
an `orders` contract, and a renamed contract keeps its `id`, so re-import, versioning and the
reconciliation in §6 key on `(odcs_id, version)` and `name` is display only. `spec` is the ODCS document **as
imported**, every field preserved, including ones DataQ does not understand — the standard
says tools must preserve unknown `customProperties`, and the same courtesy is extended to the
whole document so a round-trip through DataQ loses nothing the producer wrote. `status` mirrors
the document's `status` (`draft`/`active`/`deprecated`/`retired` in practice; stored as text,
as the standard does). A new version of a contract is a **new row**; nothing is edited in place.

### 2. A suite links to **one** contract and **one** schema object; a contract fans out to N suites

`suites.contract_id` (FK → `contracts.id`, `ON DELETE SET NULL`) and `suites.contract_object`
(the ODCS schema object's `name`). A suite with `contract_id = NULL` behaves exactly as today.
Importing a contract with N schema objects creates (or reconciles) N suites on the connection
the caller names, one per object, each targeted at the object's `physicalName` (falling back to
`name`) resolved against the connection's namespace by the existing run-target resolver.
`contract_id` and `contract_object` are set and cleared **together** — the service clears both
when a contract is deleted (the FK's `SET NULL` is the backstop, not the mechanism) and
`PATCH /suites/{id}` accepts both or neither, so an object name never outlives its contract.
Deleting a contract unlinks its suites; it never deletes a suite, a check or a result.

### 3. The translation is a **fixed, documented mapping** with an explicit "not enforced" bucket

The importer never drops a clause silently. Every schema field and every quality / SLA rule
ends in exactly one of: a check it created, or an entry in the contract's **`unmapped`** list
(stored beside `spec`, surfaced on the compliance rollup as *not enforced*, with the reason).

| ODCS | DataQ |
|---|---|
| property `required: true` | `expect_column_values_to_not_be_null` |
| property `unique: true`; `primaryKey: true` on one property | `expect_column_values_to_be_unique` |
| `primaryKey: true` on several properties | `expect_compound_columns_to_be_unique` |
| `logicalTypeOptions.minimum` / `maximum`; `exclusiveMinimum` / `exclusiveMaximum` | `expect_column_values_to_be_between`, the exclusive forms as `strict_min` / `strict_max`; `multipleOf` → `unmapped` |
| `logicalTypeOptions.minLength` / `maxLength` | `expect_column_value_lengths_to_be_between` |
| `logicalTypeOptions.pattern` | `expect_column_values_to_match_regex` |
| property `enum` (v3.2.0+, an array of `{value, …}` objects — the `value`s are taken) / `invalidValues` with `validValues` | `expect_column_values_to_be_in_set` |
| `invalidValues` with `pattern` | `expect_column_values_to_match_regex` |
| `nullValues` with an operator | `expect_column_values_to_not_be_null`; a `percent` bound becomes the check's `fail_threshold` (ADR 0016 bands the unexpected-%), a `rows` bound is not expressible and goes to `unmapped` |
| `missingValues` (`arguments.missingValues`) | `expect_column_values_to_not_be_in_set` over the listed values (plus `not_be_null` when the list contains null) |
| `duplicateValues` at property level / at object level with `arguments.properties` | `expect_column_values_to_be_unique` / `expect_compound_columns_to_be_unique` |
| `rowCount` with operators | a **`volume`** monitor (`min_rows` / `max_rows`) — the monitor kind, not the GX table expectation, so `metric_value` trends and the anomaly baseline see it. The monitor gains **one-sided bounds** (at least one of `min_rows` / `max_rows`, which also fixes the standing refusal of a min-only volume check), so `mustBeGreaterThan: 1000` never invents a ceiling |
| the schema object's property list | one **`schema_drift`** check whose baseline is **declared** from the contract (column names, `physicalType` when given) instead of learned from the first run; declared names match the warehouse's case-insensitively, and a declared column with no type is never reported as `type_changed` |
| `type: sql` with `query` + operator | a custom-SQL check (ADR 0019, SQL datasources only) built as `SELECT 1 WHERE NOT COALESCE((<query>) <operator>, FALSE)` — one unexpected row when the scalar breaches **or is NULL** (an empty table must not pass a `mustBeGreaterThan`); `{object}` / `{property}` substituted with the suite target and the property name; the validator still has the last word and a refused query goes to `unmapped` |
| `type: custom`, `engine` ∈ {`great-expectations`, `greatExpectations`, `dataq`} | the `implementation` parsed as `{expectation_type, kwargs}` (GX) or as a DataQ check document (`dataq`); anything outside `ALLOWED_EXPECTATIONS` goes to `unmapped` |
| `type: custom` with another engine; `type: text` | `unmapped` (reason: engine not executable here / prose) |
| `slaProperties` `latency` with a time `unit` and an `element` naming a column | a **`freshness`** monitor on that column, `max_age_hours` from value + unit; without an `element` the suite's arrival-time freshness where the datasource supports it, else `unmapped` |
| `slaProperties` `frequency` | `unmapped` (reason: a refresh cadence is not a staleness ceiling — a daily feed checked at 24h flaps on every run); a producer who wants a ceiling states it as `latency` or as `customProperties[dataq.maxAgeHours]` on the object, which maps to the same freshness monitor |
| every other SLA property (`retention`, `availability`, `timeToRepair`, …) | `unmapped` (reason: not a data-quality check) |
| property `classification` ∈ {`confidential`, `restricted`, `pii`, `sensitive`, `secret`} | the suite's `column_policy.pii_columns` — the **policy** rung of the redaction ladder, so a contract's classification masks samples from the first run |
| rule `dimension` | `conformity` → `validity`; `coverage` → unclassified (NULL, ADR 0038's honest gap); the other five map by name; absent → DataQ's own derivation |
| rule `severity` | `info` → `warn`, `warning` → `warn`, `error` → `fail`, `critical` / `blocker` → `critical`; absent or unknown → `fail` — a contract clause is an assertion, and a producer who wants less says `warning`; overridable per rule by `customProperties[dataq.severity]` |
| `customProperties[dataq.schedule]` (a cron) on a schema object | one `schedules` row on the suite |
| rule-level `schedule` / `scheduler` | `unmapped` (reason: DataQ schedules a suite, not a rule; the rule's cron is recorded on the clause so the rollup can show it) |

### 4. DataQ-specific fields travel in `customProperties`, vendor `dataq`

On export every DataQ field with no ODCS home is written as a `customProperties` entry with
`vendor: dataq` and a camel-case `property`: per rule `dataq.checkId`, `dataq.kind`,
`dataq.engine`, `dataq.severity`, `dataq.warnThreshold` / `dataq.failThreshold` /
`dataq.criticalThreshold`, `dataq.enabled`; per object `dataq.suiteId`, `dataq.connectionType`;
on the contract `dataq.exportedAt`. A check with no ODCS-native shape (an `anomaly` or
`comparison` check, a GX expectation outside the table above) is exported as
`type: custom, engine: dataq` with the suite-document check as `implementation`, so
`import(export(suite))` recreates every check — the round-trip the acceptance criteria ask for.
On import those properties are honoured when present and ignored when absent; a document that
never saw DataQ imports cleanly.

### 5. Versions of the standard

The importer accepts `apiVersion` **v3.0.0 through the newest v3 minor it knows** (v3.2.0 today),
validated against the vendored ODCS JSON Schema for that version (Apache-2.0, pinned under
`backend/app/contracts/schema/`, bumped by hand like GX). The mapping in §3 is written in
v3.1+ vocabulary; a v3.0.x document is **normalised first** — the deprecated `rule` names
(`duplicateCount` → `duplicateValues`, `validValues` → `invalidValues` + `validValues`, `rowCount`)
become `metric`s and the `${object}` / `${property}` placeholders become `{object}` /
`{property}` — in the translation step only, never in the stored `spec`. A newer v3 minor than the vendored
schema is accepted with a warning and validated against the newest known schema — ODCS minors
are additive and unknown fields are preserved anyway. A **v2** document is refused with a
pointer to Bitol's migration notes; a **v4** document is refused until this ADR is amended,
because a major may rename the fields the mapping keys on. Export always writes the newest
known v3 minor.

### 6. Re-import reconciles; it never destroys a person's work

Importing a version that already exists is a 409 unless `replace: true`; importing a **new**
version of a known name creates the row and, when the caller names the suites to move,
re-points them (`contract_id`) and reconciles their checks with ADR 0047's rules: add the
checks for new clauses, update a clause-derived check **only if nobody edited it since the
import** (compared against the clause's derivation, the same fingerprint idea as
`check_suggestions`), **disable** (never delete — deleting a check rewrites run history) the checks whose clause disappeared,
and leave every hand-authored check alone. A clause-derived check that a person did edit is
reported on the rollup as **`drifted`**.

### 7. Traceability: a check remembers its clause, and the incident carries it

`checks.origin = 'contract'` joins `CHECK_ORIGINS`, and a new nullable `checks.contract_ref`
JSONB holds `{object, property, rule_id, field}` — the clause the check enforces, kept out of
`config` so it survives the editor's wholesale config assignment. The incident evidence card
gains a `contract` layer (`contract_id`, `name`, `version`, `clause`) read from the failing
check, so `get_incident` (REST and MCP), the RCA prompt and every alert renderer can say *"clause
`orders.customer_id` `required` of contract `seller_payments_v1` 1.1.0 broke"* instead of a
check name. The layer is built from the check at evidence time, never copied by hand.

### 8. Reads, writes and who may do them

- `POST /contracts/import` (ODCS YAML or JSON + `connection_id`; optional `suite_ids` to
  re-point on a new version) — Member or above, `edit` on the suites it touches, audited with
  the contract id, version and counts (never the document).
- `GET /contracts`, `GET /contracts/{id}` (with the compliance rollup), `GET /contracts/{id}/export`
  (ODCS YAML, `text/yaml`) — a contract is **visible** when the caller can `view` at least one
  linked suite or is a workspace admin; the rollup covers the visible suites and reports
  `restricted_suite_count`, as `get_asset` does (ADR 0037). A contract with no linked suite is
  visible to admins only.
- Linking an existing suite to a contract by hand: `PATCH /suites/{id}` with
  `contract: {contract_id, contract_object}` (`edit`; the contract must be visible and name the
  object), or `contract: null` to unlink. Linking adds no checks; an import derives them. `DELETE /contracts/{id}` is Member+,
  needs `edit` on every linked suite, and unlinks only.
- MCP: `list_contracts`, `get_contract`, `export_contract` — three **read-only** tools (ADR
  0008 amendment, 56 → 59). Import stays REST-only: it creates suites and checks under a
  connection and is the kind of bulk write an assistant should hand to a person.
- The compliance rollup is per contract: for each linked suite's latest run, each clause is
  `pass` / `warn` / `fail` / `critical` / `unverified` (error or skipped) / `not_run` (no result
  in that run) / `disabled` (switched off), with **`drifted`** as a separate flag (the check was
  edited since its import — it can drift and still pass), plus the suite-level `last_run_at` and
  `results_final`, so a rollup over a mid-run suite is never read as a verdict. The `unmapped`
  list is the `not_enforced` half, counted beside the clauses.

### 9. UI, phase 1

A read-only **Contracts** page (list, detail with the rollup and the unmapped list, upload a
document, download the export) and a contract chip on suite detail. No in-app contract editor
— the document is authored where the producer keeps it. Rule 44 applies to the new surfaces.

## Consequences

- One additive migration: `contracts`, `suites.contract_id` + `contract_object`,
  `checks.contract_ref`, `origin` value-set widened. Nothing existing changes shape.
- The `schema_drift` monitor learns a **declared baseline**; today's learned-on-first-run
  baseline stays the default for non-contract checks.
- The translation table above is **the contract between DataQ and ODCS** and lives in one module
  (`backend/app/contracts/`) with a table-driven test per row and a round-trip test; a new row is
  a PR against this ADR's table, not a special case elsewhere.
- Suites gain a second system origin (`contract`) beside `auto`; the reconciler rules are the
  ADR 0047 ones, so a person's edit is never overwritten by either.
- `unmapped` is a product fact, not a log line: a contract whose SLA block says `retention: 7y`
  will show that clause as *not enforced by DataQ* forever, which is honest and correct.
- The evidence card, alert renderers and MCP docstrings gain a contract layer and must say when
  it is absent (the check was not contract-derived) — the usual honesty rule for this surface.

## Alternatives considered

- **A DataQ-native contract format with an ODCS exporter.** Rejected by rule 45 and because
  two formats drift; storing the ODCS document verbatim is what makes "preserve what you
  don't understand" possible.
- **One suite per contract regardless of object count.** Rejected: a suite has one target, and
  a contract's objects are different tables, often on different schedules.
- **Executing ODCS `sql` rules through a new scalar-comparison check kind.** Rejected for now:
  the `WHERE NOT (<scalar> <op>)` wrapping makes every operator an unexpected-rows count, which
  ADR 0019's validator, pushdown and redaction already handle; a `metric_value` is lost (the
  scalar is not recorded), which is accepted and noted on the rollup.
- **Deleting the checks of a removed clause on re-import.** Rejected: deleting a check rewrites
  run history; disabling keeps the history and the audit trail.
- **Producer-side enforcement / an embeddable validator wheel.** Out of scope: a wheel is a
  release cadence and a compatibility promise, and no second producer has asked for one.
- **Microsoft Purview / a catalog push of the contract.** Not here; the governance-catalog seam
  is a separate decision that will reuse the `contracts` row as its payload.
