# ADR 0038 — DQ-dimension classification on checks: seven canonical dimensions, derived default, stored and overridable

- **Status:** Accepted (§5 **amended 2026-07-19** — existing checks ARE backfilled; see the amendment in §5)
- **Date:** 2026-07-19
- **Deciders:** @TheurgicDuke771
- **Related:** [0005](0005-severity-tier-weights.md) (severity tiers + the health-score formula the scorecard reuses), [0012](0012-monitor-kind-seam.md) (`check.kind` — the axis this one is orthogonal to), [0015](0015-two-connection-comparison-check-model.md) (`comparison` kind), [0019](0019-custom-sql-check-kind.md) (custom SQL — the one authoring path with no derivable dimension), [0036](0036-connection-anchored-check-engines.md) (§4 names this work; see *Relationship to ADR 0036* below), [0037](0037-workspace-visible-asset-identity.md) (the workspace-true aggregate rule the scorecard must obey). Issues: [#124](https://github.com/TheurgicDuke771/DataQ/issues/124) (this ADR), [#889](https://github.com/TheurgicDuke771/DataQ/issues/889) (the asset scorecard that consumes it).

## Context

A check today carries two classification axes, and neither is a data-quality *dimension*:

| Field | Axis | Example |
|---|---|---|
| `kind` (ADR 0012) | *how* the monitor works | `expectation` / `freshness` / `comparison` |
| `expectation_type` | the specific GX rule | `expect_column_values_to_be_unique` |

A **DQ dimension** is a third, orthogonal axis: the *semantic category of quality* a check measures. It is what makes "we have 47 checks" legible as "your Timeliness is uncovered" — and it is the input the asset scorecard (#889) aggregates by.

The value is not primarily the score. It is **coverage**: "this asset has zero Timeliness and zero Uniqueness checks" is immediately actionable in a way a pass-rate never is, and CLAUDE.md §5 already records that most real incidents are freshness/volume — i.e. exactly the dimensions teams are least likely to have covered.

Unlike the ADR-0005 severity columns or ADR-0012's `kind`/`metric_value`, a `dimension` column is **pure widening** — nullable, additive, no data rewrite, no two-step deploy — so there was never schema-timing pressure to rush it. That is why #124 was deliberately held back from the Week-3 migration and gets a deliberate design pass now.

## Decision

### 1. Seven canonical dimensions, closed vocabulary

`accuracy`, `completeness`, `consistency`, `integrity`, `timeliness`, `uniqueness`, `validity`.

The canonical six (completeness, uniqueness, validity, accuracy, consistency, timeliness) plus **integrity** — referential/relational correctness, which the six fold into "consistency" and which DataQ can express distinctly once cross-dataset comparison exists (ADR 0015).

Stored as `VARCHAR(32)` with a table `CHECK` over the vocabulary, matching the `CHECK_KINDS` idiom (`models._in_check`) — **not** a Postgres `ENUM`. An enum type needs `ALTER TYPE` to extend, which is exactly the migration friction this column was deferred to avoid; a `CHECK` constraint is a one-line ALTER and stays visible in the model file next to the other vocabularies.

**Closed, not free-form.** A free-text dimension makes the scorecard's coverage view meaningless the first time someone types "Timeliness " with a trailing space — you cannot report "you have no Timeliness checks" against a set you don't control. The cost is that adding an eighth dimension is a migration; that is the right trade for a reporting axis.

### 2. Derived default, stored value, user-overridable at any time

Three properties, and all three are load-bearing:

- **Derived** — on create, if the check's type maps to a dimension, that is the default. Nobody should have to hand-classify `expect_column_values_to_not_be_null` as Completeness.
- **Stored** — the resolved value is written to `checks.dimension`, not recomputed on read. This is what lets #889 aggregate with a SQL `GROUP BY` instead of joining through a Python map, and what makes an override survive.
- **Overridable at any time** — at create *and* by later PATCH. Derivation is a good guess about intent, not a fact: the same `expect_column_values_to_be_between` is Validity when it bounds a percentage and Accuracy when it asserts a reconciled total.

Rejected: **pure derivation** (no column) — no override, and #889 loses SQL aggregation. Rejected: **pure user-set** — every existing check and most new ones land unclassified, so the coverage view is empty until someone backfills by hand, which nobody does.

### 3. Derivation is deliberately partial, and `NULL` is a real state

| Check type | Dimension | Why |
|---|---|---|
| `expect_column_values_to_not_be_null` | `completeness` | |
| `expect_table_row_count_to_be_between` | `completeness` | "is all the data here" |
| `monitor:volume` | `completeness` | a short load is missing data |
| `expect_column_values_to_be_unique` | `uniqueness` | |
| `expect_column_values_to_be_between` | `validity` | conforms to a rule |
| `expect_column_values_to_be_in_set` | `validity` | |
| `expect_column_values_to_match_regex` | `validity` | |
| `expect_column_value_lengths_to_be_between` | `validity` | |
| `expect_column_values_to_be_of_type` | `validity` | |
| `monitor:freshness` | `timeliness` | |
| `monitor:schema_drift` | `consistency` | structural stability over time |
| `comparison:records` / `comparison:columns` | `consistency` | cross-dataset agreement (ADR 0015) |
| custom SQL (`unexpected_rows_expectation`) | **none** | arbitrary predicate — unknowable |

**`accuracy` and `integrity` are never derived.** No generic GX expectation can tell you whether data matches reality, or whether a relationship holds. They exist for the author to select — most often on a custom-SQL check, which is precisely the path with no derivable answer. Pretending to derive them would populate the scorecard with confident nonsense, which is worse than an honest gap.

So `dimension` is **nullable**, and NULL means "unclassified" — a state the scorecard must render as a gap, never silently bucket. Existing checks keep NULL (see §5).

### 4. Orthogonal to `kind` and to `engine`

`kind` is *how the monitor works*, `dimension` is *what quality aspect it measures*, `engine` (ADR 0036) is *what evaluates it*. All three vary independently: a `freshness` kind is Timeliness whether GX or DMF runs it; a `comparison` kind is Consistency by default but Accuracy when the baseline is a source of truth.

The derivation map is therefore keyed on `expectation_type` **with a `kind` fallback**, not on `kind` alone — `expectation` covers a dozen different dimensions, so keying on kind would collapse them.

### 5. Backward compatibility

Additive nullable column on `checks` **and** on `check_versions` (history must round-trip the field, or restoring an old version silently reclassifies a check).

~~**Existing rows are left NULL, not backfilled.**~~ *(Superseded — see the amendment below.)* The original reasoning: a backfill would be indistinguishable from a deliberate user classification, so a later correction to the derivation map could never tell "the map said so" from "a human said so".

#### Amendment (2026-07-19): existing checks ARE backfilled

Migration `a7b8c9d0e1f2` fills `dimension` for every pre-existing check the map can classify.

The original position over-weighted its own concern and under-weighted the cost:

- **The ambiguity it feared is vacuous at the moment it matters.** The column is introduced one revision earlier, so when the backfill runs the set of checks carrying a *human-set* dimension is exactly **empty**. The backfill cannot overwrite anyone's decision, because nobody has made one yet. The original text reasoned as if derived and user-set values were being mixed, when in fact the user-set set is empty by construction.
- **The cost of not doing it is the feature.** Leaving them NULL makes the #889 scorecard report "unclassified" for every existing check — useless on day one, on precisely the workspaces with the most history worth reporting on. "Classified on next edit" assumed people re-save old checks, which they do not.

The residual risk is narrower than §5 claimed: only that a *future correction to the derivation map* cannot distinguish a backfilled row from a user's override. Mitigation is recorded in the migration — re-derive only rows whose stored value still equals that migration's map output, which it inlines for exactly this purpose.

The backfill deliberately writes **no `check_versions` rows**: it is a system classification, not an edit, and minting a version per check would fill every history drawer with a change nobody made. Unmapped types (custom SQL) stay NULL — §3's "unclassified is a real state" is unchanged.

### 6. Surfaces

- **Check editor** — a dimension select, pre-filled with the derived default, always editable. The derived default is shown as the selected value rather than as a placeholder, so what you see is what gets stored.
- **Export/import** — `dimension` rides the check document, and the two cases are distinguished: **absent** (an older export) → derived, exactly as if freshly authored; **present, including an explicit `null`** → taken verbatim, so an unclassified check stays unclassified. Conflating them would re-create §5's forbidden backfill by another door — export always emits the key, so every pre-migration check would be silently classified on the way through. The export version does **not** bump: adding an optional field is backward-compatible in both directions, and `EXPORT_VERSION` is documented to bump only on an *incompatible* shape change.
- **Changing a check's expectation type** re-derives the dimension in the editor (the create page already resets the whole form on a type change). Keeping the old value would leave the select showing a classification the help text simultaneously claims is "defaulted from the check type" — and it would then be sent explicitly, so a uniqueness check filed as completeness would look like a deliberate override forever. The backend does not re-derive on PATCH: it cannot tell a derived value from an override, so leaving it is the conservative choice, and the editor sends the corrected value.
- **Scorecard (#889)** — consumes this; not built here.

## Relationship to ADR 0036

ADR 0036 §4 asserts the dimension classification and an engine-aware **backend** expectation catalog are "one story… not two catalogs". This ADR **narrows that**: it lands the dimension axis and a backend derivation map — the seed of that catalog — without moving the frontend catalog server-side.

The reason is sequencing, not disagreement. The backend catalog's purpose is engine-awareness (which expectations a DMF or DQX connection offers), and **no native engine exists yet** — ADR 0036 §6 makes DMF the first build and trigger-gates the rest. Building a server catalog now would mean guessing at an interface with exactly one implementation, while #889 is blocked on nothing more than a column. When DMF lands, the derivation map is the thing it extends.

## Consequences

- Coverage reporting becomes possible at all, which is #889's actual value.
- One more field on the authoring form. Mitigated by the derived default: the common path is "don't touch it".
- The derivation map is a judgement call that will be argued with (`row_count` → Completeness rather than a Volume dimension; `schema_drift` → Consistency rather than Validity). That is why it is overridable, and why the map lives in one module rather than being scattered.
- Adding an eighth dimension is a migration. Accepted in §1.
- Two axes on the wire look similar (`kind` and `dimension`) and will be confused. The editor labels them distinctly and this ADR is the reference.

## Alternatives considered

- **Free-text dimension** — rejected in §1; unbounded values destroy coverage reporting.
- **Postgres ENUM** — rejected in §1; `ALTER TYPE` friction for no gain over a `CHECK`.
- **Derive at read time, no column** — rejected in §2; no override, and #889 loses SQL aggregation.
- **Backfill existing checks from the map** — rejected in §5; makes a derived guess indistinguishable from a human decision.
- **Multiple dimensions per check** (a check as both Validity and Accuracy) — rejected. A many-to-many turns "% of Completeness checks passing" into a question with no single answer, and every scorecard number would need a double-counting rule. One dimension per check; split the check if it genuinely measures two things.
