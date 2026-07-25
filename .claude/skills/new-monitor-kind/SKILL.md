---
name: new-monitor-kind
description: Add a new `check.kind` (monitor kind) end-to-end — registry, run-path routing, authoring gates, dimension derivation, dry-run, export/import, MCP, frontend catalog, docs and tests. Walks the exact traversal used by freshness/volume (#426/#437), comparison (ADR 0015, #791–#795) and schema_drift (#592), with the traps each one hit. Use when adding a kind from the ADR 0012 reserved set (`anomaly` is next) or when the user says "add a new monitor kind".
disable-model-invocation: true
---

# new-monitor-kind

## Purpose

`check.kind` is the ADR [0012](../../../docs/adr/0012-monitor-kind-seam.md) monitor-kind seam. Adding one is a **wide, ordered traversal** — registry → run path → authoring → classification → surfaces → docs — that is easy to half-finish. A half-finished kind is worse than none: it is dispatchable but unauthorable, or authorable but silently unclassified.

This repo has now walked it four times. This skill is that traversal, plus the trap each walk hit.

**Reserved and remaining:** `anomaly` (v1.1 W5). Shipped: `freshness`/`volume` (#426/#437), `comparison` (#791–#795), `schema_drift` (#592).

## Before you start

1. Read ADR [0012](../../../docs/adr/0012-monitor-kind-seam.md), and the ADR for the kind if one exists (`comparison` → [0014](../../../docs/adr/0014-reconciliation-comparison-check-kind.md)/[0015](../../../docs/adr/0015-two-connection-comparison-check-model.md)). If the kind has no ADR and is non-trivial, write one first — use `/adr-create`.
2. Read the closest precedent's PR diff end-to-end. Pick by shape:
   - **scalar** (one SQL aggregate → a badness number) → `freshness`/`volume`
   - **stateful** (needs a stored baseline to compare against) → `schema_drift` (#592)
   - **two-dataset** (source + target) → `comparison` (#791–#795)
3. Decide the shape first. It determines whether `build_statement` is a real function or `None`, and that single choice drives the run-path routing for free.

## The traversal

### 1. Does it need a migration? Usually no — check first

`backend/app/db/models.py` `CHECK_KINDS` backs a real DB constraint (`_in_check("kind", CHECK_KINDS, "kind_valid")` on both `checks` and `monitor_baselines`).

- If your kind is **already in `CHECK_KINDS`** — `anomaly` is — **no migration is needed.** The constraint already admits it.
- If it is not, you need an Alembic migration to widen the constraint, and it must land in its **own PR before** the code that writes the new value (working-agreement #30, two-step). Run `/agents migration-safety` on it.

### 2. Register the strategy — the one required step

`backend/app/datasources/monitors.py`:

```python
MY_KIND = "my_kind"

def _validate_my_kind(config: dict[str, Any]) -> None: ...
def _my_kind_outcome(scalar, config, now) -> CheckOutcome: ...
def _my_kind_statement(table, config) -> Select[Any]: ...   # or omit for a stateful kind

MONITOR_KIND_REGISTRY[MY_KIND] = MonitorKindStrategy(
    MY_KIND, _validate_my_kind, _my_kind_outcome, _my_kind_statement,
)
```

**Register in the dict literal, not at runtime.** The module comment is explicit: every derived value (`MONITOR_KINDS`, `SCALAR_MONITOR_KINDS`, `STATEFUL_MONITOR_KINDS`, the authoring allowlist, runners' capability sets) **snapshots at import**. A late registration is half-visible — dispatchable but unauthorable. Tests may monkeypatch; production code must not.

`build_statement=None` is what routes a kind to the stateful path. You do not write routing code; you choose a shape.

**Semantics to lock in the module docstring** (this is where the kind's contract lives):
- what the **`metric_value`** is, in one sentence, and that **higher = worse** (ADR 0016 bands it)
- the exact `config` keys and their units
- what happens on the empty/absent case

### 3. Run path

- **Scalar kinds** flow through the runners' `run_monitors` (`run_monitor_specs` / `run_monitors_over_engine`) with no new code — but each runner must **advertise** the kind: `supported_monitor_kinds` in `snowflake.py`, `unity_catalog.py`, `iceberg.py`, `flatfile.py`. A kind absent from a runner's set is refused at author time for that datasource, which is the correct default — widen deliberately, per datasource, with evidence it works there.
- **Stateful kinds** need an executor the worker injects. See `backend/app/services/schema_drift.py` (owns the baseline store and its session) and its wiring in `backend/app/worker/tasks.py` + the partition doc in `backend/app/services/run_service.py`.

### 4. Authoring gates — `backend/app/services/check_service.py`

`_V1_SUPPORTED_KINDS` derives from `MONITOR_KINDS`, so registration widens it automatically. What is **not** automatic:

- the per-datasource capability set your kind keys off (`MONITOR_CAPABLE_TYPES`, or a kind-specific one like `SCHEMA_DRIFT_CAPABLE_TYPES`)
- any config guardrail that needs the DB (a real column, a resolvable target)
- the `expectation_type` pairing — use `monitor_expectation_type(MY_KIND)` → `monitor:my_kind`. Never hand-write the string; the author path asserts kind↔type and the frontend catalog mirrors it.

### 5. Dimension — ADR [0038](../../../docs/adr/0038-dq-dimension-classification.md). **The #124 trap lives here.**

Add an entry to `_BY_KIND` in `backend/app/services/check_dimension.py` **only if the kind's dimension is genuinely derivable from its shape**:

```python
_BY_KIND = {"freshness": TIMELINESS, "volume": COMPLETENESS, "schema_drift": CONSISTENCY, ...}
```

If it is not derivable, **return `None` and leave it out**. `None` is a real answer that renders as a coverage gap; a plausible-looking guess fills the #889 scorecard with confident nonsense. `accuracy` and `integrity` are never derivable.

**The trap:** #124's export/import path silently classified every check in prod — an ADR violation inside the PR that added the ADR — and the tests missed it *by construction*. One test POPPED the `dimension` key instead of setting it to null, and the only null that round-tripped was custom SQL, where derivation *also* returns `None`. So:

> Write the round-trip test with `dimension: null` **present** in the payload, on a check whose type **is** derivable. Present-and-null must stay null; absent must derive.

Check `backend/app/services/suite_io_service.py` — the `if "dimension" in c` branch is exactly this distinction.

### 6. Remaining backend surfaces

| Surface | File | Needed? |
|---|---|---|
| Dry-run preview | `services/dryrun_service.py` | Only if the kind can be previewed without a run. It currently supports `expectation` + `schema_drift`; unsupported kinds must raise `DryRunUnsupportedError`, not fall through. |
| Export / import | `services/suite_io_service.py` | Validation is atomic — every kind validated before any row is written. Add the kind's guardrail alongside the `MONITOR_KINDS` / `COMPARISON_KIND` branches. |
| Check API | `api/v1/checks.py` | Only for a kind-specific endpoint (e.g. schema_drift's rebaseline). |
| MCP tools | `mcp/server.py` | `kind` is a passthrough string — update the **tool description** so an LLM knows the kind exists and when to pick it. Descriptions are LLM-facing (CLAUDE.md §10). |

### 7. Frontend

`frontend/src/components/checks/expectationCatalog.ts`:
- widen the `CheckKind` union
- add the catalog spec: `type: 'monitor:my_kind'`, `kind: 'my_kind'`, the input fields, threshold requirements (`freshness` requires a fail/critical threshold because it has no in-config bound — if your kind is unbounded, it needs the same), and help text stating the metric's direction
- check the per-datasource adjustment hook (the flat-file freshness special case) if your kind behaves differently by connection type

`frontend/src/api/suites.ts` carries `kind` as a string — update the doc comments so the contract stays readable.

### 8. Docs

- `docs/feature-matrix.md` — a row per datasource. **Do not tick a cell you have not run live.** #953 was a ✅ that had never once worked.
- `CLAUDE.md` §5 — move the kind from "reserved" to shipped, with the PR number.
- `docs/progress.md` — the cycle-plan row.
- ADR 0012 — amend its status list.
- `docs/datasources-checks.md` / `docs/concepts.md` — user-facing description.

### 9. Tests

- **Unit**, in `backend/tests/`: `validate_config` rejects each malformed shape; `outcome` bands correctly at each threshold boundary; `build_statement` renders through the Core layer.
- **Identifier quoting**: assert a **mixed-case** and an **already-quoted** column both work, and that the CONNECTION's dialect does the quoting. Never hand-roll `"` — Unity Catalog uses backticks (#937).
- **The empty/absent case**: no rows, no baseline, a `None` scalar. What does the check report? It must not silently pass.
- **Dimension round-trip**: present-and-null vs absent (see §5).
- **Adversarial types**: the scalar arrives from a **driver**. Feed `str`, `Decimal`, tz-naive and tz-aware datetimes, Arrow-backed values. Run `/agents driver-boundary-guard` on the diff — this kind's whole job is to consume a driver value.
- **Mutation-check the regression tests**: `/agents regression-mutation-verifier`.

### 10. Live-verify before you tick anything

Run `/live-verify` against every datasource whose feature-matrix cell you are about to tick. Unit tests cannot settle a driver-typed value — that is the standing lesson (CLAUDE.md §13), and it is why UC freshness shipped broken for weeks.

## Definition of done

- [ ] Registered in `MONITOR_KIND_REGISTRY` at import time
- [ ] `CHECK_KINDS` admits the value (migration filed separately if it did not)
- [ ] Each runner that should support it advertises it in `supported_monitor_kinds`
- [ ] Authoring gate + capability set updated; `expectation_type` via `monitor_expectation_type`
- [ ] Dimension derived **or** deliberately left `None`, with the present-null round-trip test
- [ ] Dry-run either supports it or raises `DryRunUnsupportedError`
- [ ] Export/import validates it atomically
- [ ] Frontend catalog spec + `CheckKind` union
- [ ] MCP tool description mentions it
- [ ] Docs: feature matrix (live-verified cells only), CLAUDE.md §5, progress.md, ADR 0012
- [ ] Tests: config, banding, quoting, empty case, dimension, adversarial types — all mutation-checked
- [ ] `/agents driver-boundary-guard` + `/agents qa-verifier` clean
- [ ] `/code-review` run on the PR, findings fixed or filed as issues
