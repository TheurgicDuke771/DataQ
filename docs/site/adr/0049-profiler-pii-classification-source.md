# ADR 0049 — Profiler-detected PII as a classification source

- **Status:** Accepted (2026-10-20)
- **Date:** 2026-10-20
- **Deciders:** @TheurgicDuke771
- **Related:** G3 / #433 (warehouse column tags, the governance floor), ADR [0034](0034-asset-entity-openlineage-identity-lineage-pull.md) (assets; the 2026-09-27 amendment on classification propagation), ADR [0041](0041-history-audit-strategy.md) (audit events), ADR [0012](0012-monitor-kind-seam.md) (the `pii_drift` monitor kind that consumes this, #1675). Issue #1674.

## Context

Failing-row samples are masked by a ladder of authorities: a column's own warehouse tag
(`dataq_classification`, read into `assets.column_tags`), `sensitive` inherited through recorded
column lineage, the suite's own `column_policy` (`pii_columns` masks, `identifier_column` is the
one shown locator), and finally a live name-and-value classifier over the sample itself, with
an unclassifiable column masked by default. Everything above the live classifier is persisted;
the classifier's own verdicts are not.

The profiler already visits every column of a table and sees its values (the most frequent
values on SQL datasources, a row sample on files and Iceberg). `column_classification` already
carries two value detectors that fire on data rather than on a column name: email addresses and
encoded blobs. Today their verdicts are used once, at suite creation, to derive a policy
(`auto_classify_columns`), and then forgotten: a column that starts carrying email addresses
after the suite was created is masked only if the failing sample happens to contain enough of
them. The warehouse-tag store cannot hold these verdicts. `assets.column_tags` is overwritten
from the warehouse on every refresh, its keys suppress lineage inheritance, its values travel
downstream as warehouse truth, and a `public` value in it satisfies fail-closed clearance.

Four datasource families (PostgreSQL and the engine-generic SQL types, flat files, Iceberg)
have no warehouse tag at all, so for them a persisted verdict is the only durable classification
DataQ can hold.

## Decision

1. **A classification table, not a column.** `asset_column_classifications` holds one row per
   `(asset, column, source)`: `verdict`, `detector`, `evidence` (counts only, never a value),
   `first_detected_at`, `last_detected_at`, `dismissed_at`, `dismissed_by`. `source` is
   `profiler` today; the governance-catalog pull (#1690) is the next source and gets its own
   value rather than a second table. `assets.detected_classifications_refreshed_at` records the
   last profile that was checked, so "looked and found none" differs from "never looked", as it
   does for warehouse tags.

2. **Additive only.** Only `sensitive` is ever persisted. A detected verdict can add masking;
   it can never clear a column, never satisfies fail-closed (`require_classification`)
   clearance, and is never written to `assets.column_tags`.

3. **One new rung, read through the existing choke point.** `effective_column_tags` overlays
   active detections onto the map every redaction read already consumes (samples, incident
   evidence, alert cards, the LLM prompt context, MCP), under the value `detected`. That value is
   in neither the sensitive-tag set nor the clearing set, so the rung sits where it belongs: a
   warehouse verdict (`sensitive` or `public`) on the same column wins at the merge, inherited
   `sensitive` wins, the suite's `pii_columns` masks regardless, and the suite's
   `identifier_column` clears a detection. Below it, the live classifier and the default mask
   are unchanged. On datasources with no warehouse tag, `identifier_column` is the one escape
   hatch a false positive has (hash-like ids trip the encoded detector), which is why it ranks
   above the detection.

4. **A detection is cleared by a person, never by a quiet re-profile.** The evidence on SQL is
   the top twenty values; a later profile that does not see them proves little, so a re-profile
   that finds nothing leaves the row alone. A workspace Admin dismisses a detection (audited,
   `asset.classification_update`); a dismissal survives re-detection (`last_detected_at` keeps
   moving so the page can say "still detected, dismissed on …") and can be restored. There is no
   MCP tool for this: dismissal loosens masking and is Admin-only, and MCP exposes no Admin-only
   capability by standing invariant.

5. **Profiler verdicts do not propagate along lineage.** Only warehouse verdicts travel
   (`inherited_sensitive` reads `assets.column_tags` alone). A heuristic that flows downstream
   would mask a whole lineage from one false positive with no tag to clear it.

6. **Detector scope: email and encoded, value-based only.** The two detectors that exist, with
   their existing ratios. A name signal is already applied live at every rung, so persisting it
   adds nothing. Phone, national-id, card and IP detectors are a separate issue; each needs its
   own false-positive study across the SQL top-values basis and the file sample basis.

7. **Written on every profile of the suite's own target.** `profile_connection` takes the asset
   and records detections when the probe targets that asset (never on an explicit table or path
   override, whose columns may collide by name). The write is fail-soft: a failure to persist is
   logged and never fails the profile.

## Phases

1. This ADR, the table and migration, the write path, the rung, the asset page and API, docs.
2. `pii_drift` (#1675): a monitor kind whose `metric_value` is the count of detections first
   seen since the last run on columns with no warehouse tag and no policy entry. Needs the
   `kind_valid` constraints widened first, in their own migration.
3. The governance-catalog source (#1690) as `source = 'catalog'` rows.

## Consequences

- A column that starts carrying email addresses is masked everywhere after the next profile,
  on every datasource, including the four families with no warehouse tag.
- One more query per redaction read, on the same path that already reads lineage inheritance.
- A false positive costs one Admin click, and the dismissal is audited. Until then it masks,
  which is the safe direction.
- Every redaction surface inherits the rung without a code change, because they all read one
  map. A surface that reads `assets.column_tags` directly is the one way to miss it; the tests
  pin the overlay at the choke point.

## Considered and rejected

- **JSONB on `assets`** beside `column_tags`: matches the warehouse-tag precedent and avoids a
  join, but a second source per column (the catalog pull) is already planned, and a table with
  a `source` column is where two sources per column compete. Decided by the maintainer
  2026-10-20.
- **Writing detections into `assets.column_tags`:** every property of that column is wrong
  for a heuristic (see Context).
- **Auto-clearing a detection when a re-profile finds nothing:** turns a weak absence of
  evidence into an un-masking, on the surface where a wrong answer shows a person's data.
- **A separate `detections` argument threaded through every redaction call:** the same rung,
  reached by changing six call sites and every future one, instead of one function.
- **Adding new detectors in this change:** each is new detection with its own false-positive
  surface, in a cycle scoped to no net-new features.
