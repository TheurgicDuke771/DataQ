---
name: live-verify
description: Open an ad-hoc harness test window on Azure and verify a change against REAL datasources — wake the harness, run the flows, exercise the app (live-smoke Playwright lane / e2e_smoke.py bearer mode / MCP), record the readings, put the harness back to sleep. Use when a change touches a driver boundary (a value read from a DB connector, pyiceberg, pandas/pyarrow or a cloud SDK), before ticking any docs/feature-matrix cell, or when the user says "verify this live" / "run a harness window".
disable-model-invocation: true
---

# live-verify

## Purpose

Some claims cannot be settled by a unit test, because the thing that decides them is on the other side of a driver. CLAUDE.md §13 states it as a rule:

> **For anything whose type or value crosses a driver boundary, only a live run is evidence.**

The cost of ignoring it is recorded: **Unity Catalog freshness had never worked once** (#953) while three suites ran green, because the Databricks connector returns a TIMESTAMP's `MAX` as a `str`. Parquet freshness was broken on *every* Parquet file (#520) because Arrow-backed dtypes fail `is_datetime64_any_dtype`. Both survived a full CI suite. Both died in the first minute of a live run.

This skill is the repeatable procedure for getting that evidence.

## Cost gate — read before waking anything

- The harness compute has been **stopped by default since 2026-07-04** (#590). Awake it burns **~CAD 17/day (~0.70/hour)**; stopped it costs ~0.
- Azure was restored as **PayAsYouGo on 2026-07-17 with no spending limit** — awake resources bill real money, they do not fail closed.
- **Therefore: never leave a window open.** `stop` runs in every exit path, including when the verification fails. If you are interrupted, stop the harness first and report second.
- If the user has not clearly asked for a live run, ask before waking. A window is an outward-facing, billable action.

## The harness script

`~/Coding/Python/DataQ-harness/scripts/harness_window.sh` — harness-side, **not git-tracked** (ADR 0021). Read its header before the first use in a session; the leg semantics below are summarised from it and the script is the authority.

```bash
harness_window.sh status                                    # read-only: app/trigger/job states
harness_window.sh start                                     # wake: redis → Airflow → workers → trigger, ADF triggers on
harness_window.sh run [--adf] [--dags] [--dbt] [--iceberg]  # kick the flows as MANUAL executions, re-suspend after
harness_window.sh stop                                      # sleep: reverse order, crons disarmed
harness_window.sh window [...]                              # start → run → stop, one shot
```

Pick the legs by what you need to exercise — each one costs time and money:

| Leg | Exercises | Needs live Snowflake? |
|---|---|---|
| *(none)* | the 5 mockdata jobs — files land in ADLS/S3 | no |
| `--dags` | the 3 cron Airflow DAGs, REST-triggered (unpauses first) | `flow_a_snowflake_load` does |
| `--adf` | the Flow-A ADF pipelines (`pl_flow_a_customers`, `pl_flow_a_orders`) | **yes** |
| `--iceberg` | the `iceberg-writer` job → appends to `retail.purchase_orders` (Flow D) | no |
| `--dbt` | the `dbt-lineage` job → staging views + mart dynamic tables, artifacts to ADLS | **yes**; runs last so a same-window load is what the marts refresh from |

Full-cycle `window` takes ~11.5 min. `run` works standalone (the mockdata jobs do not depend on the Airflow apps).

**Suspended-job semantics:** an ACA job refuses manual starts, so `run` resumes each job, starts it, and re-suspends — nothing is left armed on a cron.

## Procedure

### 1. Decide what evidence you need — before waking anything

Write down, explicitly, the reading you expect to observe and what would falsify it. Vague intent produces vague evidence, and a green suite is the thing that already fooled us.

Good: *"UC freshness on `retail.orders` should return a float age in hours; a lower-case and an UPPER-case column name must return **identical** values."*
Bad: *"check that UC works."*

Choose the minimum legs that produce that reading.

### 2. Wake and run

```bash
~/Coding/Python/DataQ-harness/scripts/harness_window.sh status   # confirm the starting state
~/Coding/Python/DataQ-harness/scripts/harness_window.sh start
~/Coding/Python/DataQ-harness/scripts/harness_window.sh run [legs]
```

Run these in the **background** — a full window is ~11.5 min and macOS has no `timeout`.

### 3. Exercise the app against the live data

Pick what matches the change:

- **Browser (deployed stack):** `E2E_LIVE_BASE_URL=https://<frontend-host> pnpm e2e` in `frontend/` — the opt-in `frontend/e2e-live/` lane (#531). Never runs in CI.
- **API (deployed stack):**
  ```bash
  DATAQ_API=https://<frontend-host> DATAQ_BEARER=$(az account get-access-token \
      --resource api://<api-app-id> --query accessToken -o tsv) \
      python -m backend.scripts.e2e_smoke
  ```
  The frontend nginx proxies `/api` to the internal api. Note `e2e_smoke.py` proves the **app layer**, not the datasources — it is a precondition, not the evidence.
- **MCP:** the 4-query protocol smoke against live `/mcp/` (trailing slash matters — `docs/mcp-setup.md`).
- **The actual reading:** trigger the suite whose check you are verifying and read the **result row** — `metric_value`, `observed_value`, the run status. This is the evidence; everything above is scaffolding.

### 4. Judge the reading — the part that matters

For each expected reading:

- **Is there a value at all?** A check that reports "no reading" and stays green is the #953 failure mode. Absence is a red result, not a neutral one.
- **Is the value plausible?** #520's real signal was `531.8h` — the harness genuinely had stopped producing. A number you cannot explain is not a pass.
- **Do the case variants agree?** Run the identifier lower-case and UPPER-case. Identical values prove the dialect quoting is right (#937); differing values prove it is not.
- **Did it work for the right reason?** If a run turned green after a fix, confirm the fix is why. A dead credential also produces a suspiciously quiet failure — and a **datasource** connection with a dead credential has no visible state until a run fails (#954), so check worker logs before concluding.

### 5. Sleep — always

```bash
~/Coding/Python/DataQ-harness/scripts/harness_window.sh stop
~/Coding/Python/DataQ-harness/scripts/harness_window.sh status   # confirm everything is down
```

`stop` also disarms the mockdata crons and the `dbt-lineage` nightly cron. Verify with `status`; do not assume.

### 6. Record the evidence

A live run whose result is not written down has to be paid for twice.

- **`docs/feature-matrix.md`** — tick a cell **only** for a datasource you just observed working. A ✅ with no live run is a claim, not a fact.
- **CLAUDE.md §13** — the reading and the date, with the actual numbers (`"UC freshness 239.06h, lower/UPPER identical"` beats `"UC verified"`).
- **A GitHub issue** for anything the run found — working-agreement #3, never a silent fix.
- If the run **found a defect a test could not**, say so explicitly and name the boundary. That sentence is what turns one incident into a rule.

## Rules

- **Never print or write a secret.** Key Vault values go straight from command substitution into the consuming command — no masked echo, no scratch file. This is a standing constraint, not a preference.
- **Never leave the window open.** Stop before reporting.
- **Never tick a docs cell you did not observe.**
- The harness is demo-scoped. Credentials expire and self-signal via #419 alerting; recovery is re-mint + Key Vault update.

## See also

- `/agents driver-boundary-guard` — finds the boundaries *before* the live run does, and tells you which claims a unit test genuinely cannot settle
- `deploy/README.md` — pre-deploy and post-deploy smoke checklists
- `docs/runbook-faq.md` — the live-smoke runbook entry
- ADR [0021](../../../docs/adr/0021-demo-test-data-environment-strategy.md) — why the harness lives outside this repo
