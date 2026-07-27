# Ops log — harness lifecycle + credential rotation

Append-only record of two things that are **invisible in code and expensive to
reconstruct**: when harness compute was started or stopped, and when a credential
was rotated and when it next expires.

## Why this file exists, and why it is git-tracked

On 2026-07-26 both prod Airflow connections showed ~280 consecutive poll failures.
Working out whether that was an incident or an intentionally-stopped harness took
several Azure queries, and I still got the attribution wrong twice — first
inventing an Airflow-3 API migration, then citing the wrong shutdown date. The
answer existed only as a `systemData.lastModifiedAt` timestamp on a Container App.

The harness repo (ADR 0021) is deliberately **not** git-tracked, so a log kept
there has no history, no review, and no diff. The whole value of this record is
history — which is what git is for. So it lives here, in the tracked repo, even
though it describes external infrastructure.

The same shape burned us before on credentials: one credential is copied into **N
per-connection Key Vault secrets**, and rotating some but not all left two
Snowflake connections silently dead for three weeks (root-caused 2026-07-25).
A rotation entry that lists *every* derived secret is the countermeasure.

## Rules

1. **Never record a secret value.** Identifiers, dates and owners only. A name
   like `conn-snowflake-retail` is a Key Vault *key*, not a credential — the
   value never appears here, in any form, redacted or otherwise.
2. **Absolute dates, always** (`YYYY-MM-DD HH:MM UTC`). "Yesterday" is useless in
   six weeks, and this file exists to be read cold.
3. **Say who and why.** "Stopped" answers almost nothing; "stopped, cost
   wind-down, expected down until the next test window" answers the question that
   actually gets asked.
4. **Rotating one credential means rotating every secret derived from it.** List
   them all in the entry, so a partial rotation is visible as a short list rather
   than discovered weeks later.
5. **Append; never rewrite history.** A wrong entry gets a correcting entry
   beneath it, the way [#1023](https://github.com/TheurgicDuke771/DataQ/issues/1023)
   got a correction rather than a quiet edit.

## The hook

A `PostToolUse:Bash` hook in `.claude/settings.json` reminds whoever runs a
lifecycle or rotation command to write the entry. It matches `az containerapp
start|stop` (including `job`), `--min-replicas`, `harness_window.sh`, `az keyvault
secret set` and `/reauth`.

It matches an **invoked** command, not a mention. The first version grepped the
whole command string and fired on its own commit message — which mentioned
`harness_window.sh` — and on any `grep` for those terms. A reminder that fires
when nothing happened is one people learn to dismiss, so it now splits the command
on shell separators, anchors each trigger to the start of a segment, **and
requires whitespace-or-end after it**.

That last part took two attempts. Anchoring alone still fired on a commit message
whose prose happened to **word-wrap** so a line began `harness_window.sh.` — the
trailing period is what tells a mention from an invocation, since a real one is
always followed by arguments or nothing. Verified against six trigger and three
non-trigger commands, including that exact wrapped-prose message.

Residual, accepted: grep cannot parse shell quoting, so prose deliberately shaped
like a command invocation at the start of a line would still trip it. Rare, and it
errs toward reminding.

---

## Harness lifecycle

Harness compute is **stopped by default** since the 2026-07-04 cost wind-down
(#590) — roughly CAD 17/day awake versus ~0 stopped. `harness_window.sh` opens a
test window (wake → run the flows → sleep again). Anything left running outside a
window is either deliberate or a mistake, and this log is how the two are told
apart.

| When (UTC) | Service | Action | By | Why / expected state |
|---|---|---|---|---|
| 2026-07-27 07:50–08:30 | **LOCAL stack only** — `dataq-harness-local` compose (Airflow scheduler/webserver/Celery worker + Postgres + Redis + MinIO) on the developer machine. **No Azure resource started; all 5 harness apps stayed `Stopped`.** | **BUILD + full live validation** of the Azure-free alternative (`local/docker-compose.yml`, `local/harness_local.sh`) | Arijit (via Claude) | Readiness exercise, **not** a wind-down — Azure is untouched and keeps running. Mirrors `terraform/airflow.tf` (same image, same DAGs, CeleryExecutor, same env var names); MinIO replaces the ADLS landing zone and DataQ reads it through the ordinary `s3` connection with the new `endpoint_url` (#1063). **Verified end to end:** 4 DAGs parsed with 0 import errors · mockdata backfilled 24 datasets to `s3://landing` · DataQ connection test `{"ok":true}` · flat-file suite **4/4 pass** (643 rows, arrival-time freshness 0.21h off MinIO's `LastModified`) · **`flow_a_snowflake_load` SUCCESS** loading live Snowflake (34,680 ORDERS_HEADER) · DataQ ingested the local DAG runs into `pipeline_runs` (success **and** failure). **The one Azure dependency left:** the runtime Snowflake PAT is still read from Key Vault at `up` — see the finding below. |
| 2026-07-27 06:45 | Jobs only — 5× mockdata + `dbt-lineage` (+ ADF pipeline runs). **No container app started.** | **RUN (targeted)** — `harness_window.sh run --adf --dbt` | Arijit (via Claude) | Confirms the two grants applied at ~06:55 actually clear the ADF + dbt failures end to end — a privilege probe is not a live run. Deliberately skips `start`: neither ADF (managed) nor the `dbt-lineage` job depends on the Airflow apps, so all 5 apps stay **Stopped** throughout and only the jobs are briefly resumed. DAGs + iceberg are not re-run — they already passed at 06:22. **Expected state afterwards: jobs re-suspended, apps still Stopped, ADF triggers still Stopped** — verified independently after. |
| 2026-07-27 06:36 | Full harness — 5 apps + 2 ADF triggers + all 7 jobs | **STOP** — `harness_window.sh stop` | Arijit (via Claude) | Window closed. **Verified independently: all 5 apps `Stopped` with 0 live replicas, both ADF triggers `Stopped`, and suspend re-issued on all 7 jobs with 0 executions Running.** Jobs mattered here — unlike the 04:00 window, `run` genuinely resumed them, so a `set -e` abort mid-phase could have left a cron armed. Results: mockdata ×5 Succeeded, **all 3 Airflow DAGs success**, iceberg-writer Succeeded; **ADF ×2 and dbt Failed** — see the finding below. |
| 2026-07-27 06:15 | Full harness — 5 apps + 2 ADF triggers + all 7 jobs | **START + FULL RUN** — `harness_window.sh start` then `run --adf --dags --dbt --iceberg` | Arijit (via Claude) | **Deliberate, full-flow validation window.** First full cycle since the Airflow metadata-DB credential was fixed (05:15 row) — the 04:00 window could not exercise any flow because Airflow never served. Runs every flow: ADF Flow-A pipelines, the 3 cron DAGs, the dbt-lineage job, and the iceberg-writer job. **Expected state afterwards: everything back to Stopped/Suspended/Disabled the same session** — see the paired STOP row. Jobs are the risk here: `cmd_run` runs under `set -e`, so a mid-phase failure can leave a job RESUMED (cron armed) — suspension is therefore verified independently after, not taken from the script's exit. |
| 2026-07-27 05:15–05:45 | Shared Postgres `dataq-pg-wus3-3erlgd` (admin role only) + `dataq-harness-airflow` | **Long-term fix** — reset the server's `airflowadmin` password to Terraform's value, aligned every consumer, verified, stopped | Arijit (via Claude) | Removes the drift the 04:40 hotfix left behind. **Safety gate first: DataQ prod uses the separate `dataq_app` role** (checked the live `database-url` secret), so resetting the server ADMIN password cannot affect the product — verified 200 on prod `/healthz` and a control connection after. **Final state: all 5 harness apps `Stopped`, 0 replicas, both ADF triggers `Stopped`.** |
| 2026-07-27 04:40–05:05 | `dataq-harness-airflow` only | **FIX + verify window** — repointed the PG credential, started, verified, stopped | Arijit (via Claude) | Fixed the metadata-DB auth failure below. Started ONLY the airflow app (not the full harness) to keep the window minimal. `/health` 200 after ~2 min; both DataQ Airflow connections `{"ok":true}` from Key Vault AND from OpenBao. **Verified afterwards: all 5 apps `Stopped`, 0 live replicas.** |
| 2026-07-27 04:15 | All 5 harness apps + both ADF triggers + all 7 jobs | **STOP** — `harness_window.sh stop` | Arijit (via Claude) | **Window closed. Expected + VERIFIED state: every app `Stopped`, every job suspended, both ADF triggers `Stopped`, and — checked separately — 0 live replicas.** The script's own success message is not sufficient evidence: `cmd_stop` runs under `set -e` and calls `wait_app`, which returns 1 on timeout, so a single slow app would abort the loop and silently leave the rest running. Immediately after the script reported success, `replica list` still showed airflow=1, worker=2, marquez=1 (draining); polled to 0 before declaring the window closed. |
| 2026-07-27 03:57 | All 5 harness apps (marquez, redis, airflow, airflow-worker, airflow-trigger) + both ADF triggers | **START** — `harness_window.sh start` | Arijit (via Claude) | **Deliberate, short window.** Purpose: live-test the 13 renamed `conn-*` secrets through the real orchestration path. The two Airflow connections were the ONLY ones the 2026-07-27 02:45 rename could not verify — their connection test 502s whenever the harness is down, so a stopped harness and a broken credential look identical from DataQ. **Expected state afterwards: everything back to Stopped/Suspended/Disabled the same session** — see the paired STOP row below. If that row is missing, the window did not close cleanly and the harness is burning ~CAD 17/day. |
| 2026-07-18 19:36 | `dataq-harness-airflow` (+ `-worker`, `-trigger`) | **Stopped** | royarijit04@outlook.com | Verified from `systemData.lastModifiedAt` on 2026-07-26, not from memory. Intentional. **Why (recovered 2026-07-26 from the harness's own notes, not from Azure):** a `--dbt` window earlier that day hit Snowflake Enterprise's new **MFA-on-password-login** enforcement, killing every password-auth harness leg (`dbt-lineage` failed 19:21Z with `250001 (08001)`); the harness was stopped ~15 min after that session hit the wall. A loader PAT fixed the auth the same day, but a residual GRANT failure remains — now tracked as [#1030](https://github.com/TheurgicDuke771/DataQ/issues/1030) instead of living only in an untracked file. Consequence: DataQ polls every 10 min, ACA's ingress answers 404 for a stopped app, and the connection accumulates failures — 282 by 2026-07-26. Expected to stay down until a test window needs Airflow. |
| 2026-07-26 06:16 | `dataq-app-{api,worker,frontend}` | Deployed `c401572d` | Deploy workflow | App stack, not harness. Recorded here because the roll restarted the worker and reset in-memory state. |
| 2026-07-26 23:5x | harness Airflow + worker + `dbt-lineage` + `iceberg-writer` + ADF `ls_snowflake` | **terraform apply (targeted)** — credential propagation only | terraform | First apply since 2026-07-18. Ran `-target` on those five so the new `DATAQ_LOADER` PAT reaches the containers (#1032). Everything stayed **Stopped**; verified after. |

> **Deliberately NOT applied (2026-07-26).** The full plan wanted 8 changes; only
> 5 were applied. The other three would have been actively wrong right now:
>
> * `azurerm_data_factory_trigger_blob_event.orders_landed` and
>   `..._schedule.customers_daily` — both `activated = false -> true`. A blanket
>   apply **arms the ADF triggers**, starting pipelines against Snowflake on a
>   harness that is meant to be asleep. This is the specific outcome the
>   stop-everything rule exists to prevent, and it is invisible unless you read
>   the plan.
> * `snowflake_warehouse.dataq` — `min/max_cluster_count -> null`,
>   `query_acceleration_max_scale_factor 8 -> -1`. Provider-version drift, not an
>   intended change; applying it would silently alter the warehouse.
>
> Re-check these on any future apply: a plain `terraform apply` here is not safe.

> **Observed, unexplained (2026-07-26).** The mockdata / `dbt-lineage` /
> `iceberg-writer` ACA jobs still carry live cron expressions (`0 2 * * *` etc.),
> yet **no execution has run since 2026-07-18** on any of them. So nothing is
> costing anything — but "the cron is armed" and "the cron fires" evidently
> disagree, and I have not established why. Worth knowing before assuming a
> future window's schedule will fire on its own.

> **Note on how the 2026-07-18 "why" was recovered:** Azure told us *when* and
> *who*, and nothing about *why*. The reason lived in `HARNESS_TODO.md` in the
> untracked harness repo — one file, one machine, no backup. That is the argument
> for this log in one sentence: the timestamp was recoverable, the intent very
> nearly was not.
>
> That file has since been **removed** (2026-07-26). Reading it before deleting
> turned up a second open item nobody had tracked — the deployed Terraform still
> injects `SNOWFLAKE_PASSWORD`, which is dead under MFA enforcement — so both its
> live items became issues first ([#1030](https://github.com/TheurgicDuke771/DataQ/issues/1030),
> [#1032](https://github.com/TheurgicDuke771/DataQ/issues/1032)), with the
> original archived verbatim in #1032. **Anything still open belongs in the
> tracker; only settled working notes belong harness-side.**
>
> **Unresolved as of 2026-07-26:** 282 failures at a 10-minute cadence is ~47h,
> but the app has been stopped since 2026-07-18 (~192h) — about a quarter of the
> expected count. Either beat is not ticking at its scheduled rate (see
> [#905](https://github.com/TheurgicDuke771/DataQ/issues/905)), the counter does
> not increment on every attempt, or something reset the streak. Not yet
> diagnosed; `pipeline_runs` + `connections.last_polled_at` in the prod DB would
> settle it.

---

### Finding 2026-07-27 — harness Airflow cannot reach its metadata DB (pre-dates the rename)

Airflow never served during the 04:00 window. Root cause, from the container log:

    psycopg2.OperationalError: FATAL: password authentication failed for user "airflowadmin"

**Not the DataQ secret rename.** Every secret on `dataq-harness-airflow` is an
*inline* container-app secret (`keyVaultUrl = None`) — the app references no Key
Vault secret at all, so nothing deleted at 02:45–03:10 could reach it. The active
revision `--0000010` was created **2026-07-27 00:07:08 UTC**, ~2.5 h before the
first vault operation (00:39).

**Actual cause: the 2026-07-26 23:5x targeted apply (#1032, row above).**
`local.airflow_pg_conn` (airflow.tf:26) is built from `random_password.pg.result`,
and the apply was `-target`ed at the container apps — **not** at
`azurerm_postgresql_flexible_server.airflow`. So the new revision's `pg-conn`
carries Terraform's password while the server still has its previous one. One side
updated, the other not: the #954 shape, in the harness this time.

This went unnoticed because the harness is stopped by default — nothing exercises
Airflow between windows, so a broken metadata DB looks exactly like a sleeping one.

**FIXED 2026-07-27 04:40.** The obvious fix — reset the server's password to
`random_password.pg.result` — would have been **wrong, and would have broken a
working connection.** DataQ's iceberg connection authenticates as `airflowadmin`
using KV `iceberg-catalog-password` and passes, which makes *that* value the
server's truth and Terraform's the drifted one. Direction established by comparing
hashes, never values:

    server truth (iceberg-catalog-password) : 42b5d90e7cd6
    what the containers held                : dcb6371cdbb5   MISMATCH

So the containers were repointed at the server's password, not the reverse. Three
carried the bad value — `dataq-harness-airflow`/`pg-conn`,
`dataq-harness-airflow-worker`/`pg-conn`, and the **`iceberg-writer` job**'s
`iceberg-catalog-uri`, which had been silently broken since the apply (last run
2026-07-12) with nothing to notice it. Password percent-encoded on the way in:
Terraform concatenates it raw, so a special character corrupts the DSN silently
rather than failing loudly. Verified: Airflow `/health` 200, and both DataQ Airflow
connections green from Key Vault *and* OpenBao.

**Drift RESOLVED 2026-07-27 05:15** — the server was reset to Terraform's value and
every consumer aligned, so the two sides now agree and an apply is a no-op rather
than a re-break. Done in this order, to keep the broken window to seconds:

1. **Safety gate.** DataQ prod authenticates as `dataq_app`, not `airflowadmin`
   (checked the live `database-url` secret) — so resetting the server ADMIN
   password cannot reach the product. Confirmed after: prod `/healthz` 200.
2. Server `airflowadmin` password → `random_password.pg.result`.
3. KV `iceberg-catalog-password` → same value (DataQ reads Key Vault at runtime,
   so its iceberg connection recovered with no restart — verified `{"ok":true}`).
4. The three container secrets → same value, by swapping the password *into* the
   existing DSN rather than rebuilding it, so the rest stays byte-identical to
   Terraform's output. `random_password.pg` is `special = false`, so the raw
   concatenation Terraform performs is safe and no percent-encoding is introduced
   — encoding it would itself have shown up as drift on the next plan.
5. OpenBao re-synced, or the two stores would have silently diverged again.

Verified end to end: Airflow `/health` 200 **on Terraform's password**, and both
DataQ Airflow connections green from Key Vault *and* OpenBao.

### Finding 2026-07-27 — the two Snowflake PATs are not interchangeable, and the wrong one fails as a *grant* error

Building the local stack, every `flow_a_snowflake_load` task failed with:

```
250001 (08001): Role 'DATAQ_LOADER' specified in the connect string is not
granted to this user, or is not permitted for the credentials being used.
```

That reads as a missing grant, and it is not one. `SHOW GRANTS TO USER ROYARIJIT04`
confirms `DATAQ_LOADER` **is** granted (2026-06-27, by ACCOUNTADMIN). The cause is
that a Snowflake **PAT is bound to a role**, and the harness has two of them for the
same user — the split recorded in the 2026-07-26 23:53 rotation rows:

| Credential | Scope | Purpose |
|---|---|---|
| `../secrets.sh` → `SNOWFLAKE_PASSWORD` | **ACCOUNTADMIN** only | Terraform provider (creates account roles; `DATAQ_LOADER` cannot create itself) |
| KV `snowflake-password-harness` | **DATAQ_LOADER** only | The runtime credential ACA's Airflow + dbt job read |

Verified both directions: the secrets.sh PAT authenticates as ACCOUNTADMIN and is
refused for `DATAQ_LOADER` *and* `DATAQ_READER`; the Key Vault PAT is the mirror
image. The local stack had picked up secrets.sh's copy simply because you must
source that file to get `DATABRICKS_TOKEN`.

**Why this is worth writing down.** The split is deliberate and already logged, but
the failure it produces names the wrong thing. Anyone hitting that message will go
hunting for a missing grant — which is exactly what #1030/#1032 were — and find one
that is already there. The rule: **when a role error contradicts `SHOW GRANTS`,
suspect the credential's scope, not the grant.**

`local/harness_local.sh` now takes the Key Vault credential in preference to any
inherited `$SNOWFLAKE_PASSWORD`, precisely so sourcing `secrets.sh` cannot
reintroduce it. That is also the **one Azure dependency remaining** in the
otherwise Azure-free local stack: before a real wind-down the DATAQ_LOADER PAT must
be moved into `local/.env` by hand, or a local-use PAT minted. The script
deliberately does not mint or persist credentials on its own.

### Finding 2026-07-27 — #1032's credential swap left `DATAQ_LOADER` short two grants

The first full-flow window since the Airflow fix ran every flow. Airflow is healthy:
all three DAGs (`flow_a_snowflake_load`, `flow_a_uc_reference`, `flow_b_medallion`)
**succeeded**, `flow_a_payments_event` fired naturally off the Event Grid blob
trigger, the 5 mockdata jobs and `iceberg-writer` succeeded, and DataQ ingested
runs from **both** providers through the renamed `conn-*` secrets on the freshly
deployed image.

Two Snowflake-**writing** paths failed, and for the same reason — not a credential
fault, an authorisation one. Both authenticated fine as `DATAQ_LOADER`:

| Path | Missing grant |
|---|---|
| ADF `pl_flow_a_customers` / `pl_flow_a_orders` | `CREATE STAGE` on `SCHEMA DATAQ_DB.RETAIL` (the Copy activity stages through an internal stage) |
| `dbt-lineage` job | `MANAGE GRANTS` on `ACCOUNT IWB83668` (the `on-run-end` grant hook) |

dbt's models themselves built — `PASS=14 ERROR=1 SKIP=2` — so only the trailing
grant hook failed, not the transformation.

**Cause:** #1032 replaced the ADF/dbt Snowflake password with `DATAQ_LOADER_PAT`.
The previous principal carried privileges `DATAQ_LOADER` does not, so the swap
silently narrowed what those two paths could do. Nothing surfaced it until a flow
actually ran, because the harness is stopped by default — the same invisibility
that hid the Airflow metadata-DB break.

**GRANTED 2026-07-27 ~06:55 (by @TheurgicDuke771):**

```sql
GRANT CREATE STAGE ON SCHEMA DATAQ_DB.RETAIL TO ROLE DATAQ_LOADER;
GRANT MANAGE GRANTS ON ACCOUNT TO ROLE DATAQ_LOADER;
```

Verified **effective**, not merely present: `SHOW GRANTS TO ROLE DATAQ_LOADER`
returns both rows, and — because a grant row is not proof the privilege applies —
each was exercised as `DATAQ_LOADER` against the exact operation that failed. A
temporary stage was created and dropped in `DATAQ_DB.RETAIL`, and a `GRANT`
statement executed successfully.

**CONFIRMED end to end 2026-07-27 06:46–06:52** by re-running `--adf --dbt`,
because a privilege probe proves the privilege and not that the flow completes:

| Flow | 06:32–06:34 (before) | 06:46–06:52 (after) |
|---|---|---|
| `pl_flow_a_customers` | Failed | **Succeeded** |
| `pl_flow_a_orders` | Failed | **Succeeded** |
| `dbt-lineage` | Failed | **Succeeded** |

DataQ then ingested both ADF runs as `succeeded` on the next 10-minute poll —
the full chain (grant → flow → orchestration poll → `pipeline_runs`) verified
through the product, on the freshly deployed image and the renamed secrets.

The confirmation run started **no container app**: neither ADF (managed) nor the
`dbt-lineage` job depends on the Airflow apps, so all five stayed `Stopped`
throughout and only jobs were briefly resumed.

> Still worth doing: `MANAGE GRANTS` is account-wide and broad. Narrowing dbt's
> `on-run-end` grant hook so the role does not need it remains the tighter
> long-term fix.

### Finding 2026-07-27 — a Snowflake emulator spike, and what it says about evidence

Readiness exercise, not a wind-down: **no Azure resource was touched and all five
harness apps stayed `Stopped`.** Question asked — can the local stack keep a
warehouse when Snowflake lapses, the way MinIO keeps the landing zone when Azure
does. Answered by spiking before building, which was the right order.

**LocalStack for Snowflake was evaluated and REJECTED on licensing.**
`localstack/snowflake:2026.6.0` exits **55, "License activation failed"** with no
token; there is no community tier, only a free non-commercial OSS licence by
application. Its fidelity was therefore never observed — it cannot start.

That produced a **standing rule, adopted 2026-07-27: nothing in the harness may
require a commercial licence**, even though the harness is untracked and
undistributed. CONTRIBUTING rule 40 / ADR 0031 govern what DataQ *ships*; this is
the stricter harness-side rule. The reasoning is that a licence gate on a
contingency stack defeats the contingency — the point of an offline harness is to
keep running when accounts lapse, and a licence is one more account that can
lapse. Trading an Azure subscription for a LocalStack one is not a wind-down.
Full evaluation record, including the two integration details worth keeping, is
in the harness README.

The spike was therefore split into **our plumbing** vs **their fidelity**, and the
first half was settled for free against **fakesnow** (Apache-2.0, DuckDB-backed) —
which is now the only stand-in.
`local/snowflake_probe.py` runs DataQ's *real* functions, not equivalents —
**6/6**: driver, DataQ's DSN through `connect_args`, volume + freshness monitors,
the profiler aggregate, and the full GX `add_snowflake` → `run_expectations`
chain with three expectations.

What that fixes in advance: the redirect **must** ride in SQLAlchemy
`connect_args`. snowflake-sqlalchemy blocks `host` and `protocol` as URL query
params ("they change the connection target"), and GX threads
`kwargs['connect_args']` into its own `create_engine`. Anyone reaching for a
query-string override would have lost a day to it.

**The probe found a live defect no test could — [#1067](https://github.com/TheurgicDuke771/DataQ/issues/1067).**
GX's `REQUIRED_QUERY_PARAMS` is `{"warehouse", "role"}`, but DataQ makes `role`
optional for password auth and the connection test is deliberately GX-free. A
role-less Snowflake connection therefore **tests green and fails every suite
run** — and only the expectation half, since monitors never touch GX, so it looks
partly alive. This is the #828/#954 blindness again: a connection whose state the
product reports as healthy while it cannot do its job.

**The rule this leaves behind — an emulator is CONTINUITY, not COVERAGE.** A green
suite against one is not evidence the Snowflake integration works: its
`information_schema`, timestamp types and identifier folding are its own
implementation. That is exactly the driver-boundary shape that hid UC freshness
returning a `str` (#953) and Parquet's Arrow dtypes (#520) — and it is *worse*
than a fixture, because it looks like a live run. Anything sourced from an
emulator gets labelled as such, in the probe, the compose file and the README.

Also recorded: `docker compose` interpolates **every** service regardless of
profile, so a `${VAR:?}` required-guard on an opt-in service fails the plain
`up` for the whole stack. Caught by testing the default path after adding the
profile, not by reasoning about it.

## Credential rotation

One credential typically becomes **several** Key Vault secrets — one per
connection that uses it. The "Secrets written" column must list every one, or the
next reader cannot tell a complete rotation from a partial one.

| Rotated (UTC) | Credential | Secrets written | Expires | Notes |
|---|---|---|---|---|
| 2026-07-05 | Snowflake `DATAQ_READER` / `DATAQ_LOADER` PATs | `conn-snowflake-retail` only | — | **Partial — this is the incident.** `conn-snowflake-orders` and `conn-snowflake-payments` were left on the 2026-06-28 value and stayed dead until 2026-07-25. Logged retrospectively as the worked example of why this table has a "Secrets written" column. |
| 2026-07-25 | Snowflake `DATAQ_READER` / `DATAQ_LOADER` PATs | `conn-snowflake-retail`, `conn-snowflake-orders`, `conn-snowflake-payments` | **2026-07-29** | All three verified with a connection test after writing. `SecretStore` reads Key Vault at runtime, so **no container restart is needed** — the opposite of env-injected secrets. |
| 2026-07-26 19:43 | Snowflake **`DATAQ_READER_PAT`** (re-minted) | `conn-snowflake-retail`, `conn-snowflake-orders`, `conn-snowflake-payments` | **2026-08-20** | All three connection-tested after writing: 200/200/200. No restart needed (runtime Key Vault read). |
| 2026-07-26 19:43 | Snowflake **`DATAQ_LOADER_PAT`** (re-minted) | `snowflake-loader-pat` | **2026-08-06** | Harness-side loader credential. `snowflake-password-harness` deliberately NOT rotated — it is the password the MFA enforcement killed, and retiring it is #1032, not a rotation. |
| 2026-07-26 23:53 | Snowflake **ACCOUNTADMIN PAT** (new) | harness `secrets.sh` → `SNOWFLAKE_PASSWORD` (Terraform provider only) | **2026-08-10** | Replaces the password MFA enforcement killed on 2026-07-18. Needed because the provider creates account ROLES and grants — `DATAQ_LOADER` cannot create itself. `SNOWFLAKE_ROLE` set to ACCOUNTADMIN to match. Verified: authenticates as ACCOUNTADMIN. **Short-lived by design — 15 days.** |
| 2026-07-26 23:53 | Snowflake **`DATAQ_LOADER_PAT`** | `snowflake-password-harness` (the KV secret Airflow + the dbt job read) | 2026-08-06 | The RUNTIME half of #1032. Verified: authenticates as DATAQ_LOADER. **Not live until `terraform apply`** — the ACA container secret is materialised from this KV value at apply time, so the containers still hold the old password until then. |
| 2026-07-27 02:45–03:10 | **No credential changed — a RENAME of all 13 `conn-*` Key Vault keys** | old → new: `conn-snowflake-retail`→`conn-snowflake-retail-dev-6729c4f9`, `conn-snowflake-orders`→`conn-snowflake-orders-dev-1c62b0c3`, `conn-snowflake-payments`→`conn-snowflake-payments-dev-f53de47d`, `conn-adls-landing`→`conn-adls-landing-dev-47161adc`, `conn-adls-raw`→`conn-adls-raw-dev-c6af82cf`, `conn-unity-catalog-retail`→`conn-unity-catalog-dataq-retail-dev-ae7b09b7`, `conn-unity-catalog-qa`→`conn-unity-catalog-qa-5135eb21`, `conn-adf-factory`→`conn-adf-dev-5f2a3c17`, `conn-adf-qa`→`conn-adf-qa-e032e40b`, `conn-airflow`→`conn-airflow-dev-b2a13125`, `conn-airflow-qa`→`conn-airflow-qa-94c4894a`, `conn-97324ba4-…`→`conn-iceberg-harness-dev-97324ba4`, `conn-bcdcad4f-…`→`conn-dbt-retail-lineage-dev-bcdcad4f` | unchanged | **Values are untouched — every expiry above still applies.** Naming converged on one generated convention (ADR 0039 / #1060): `conn-<type>-<qualifier>-<env>-<shortid>`. Prod had been running two conventions — 11 hand-named, 2 app-generated UUIDs. Order per key: copy → **read-back verify** → repoint `connections.secret_ref` → re-test → purge old. Piloted on `conn-snowflake-orders` alone and verified end-to-end before the other 12. **Verified after: 11/11 reachable connections `{"ok":true}`.** Airflow ×2 not testable — the harness Container App was already `Stopped` at 00:07 UTC, *before* this work (`systemData.lastModifiedAt`), so its 502 is the stopped harness, not this change. Old names are soft-deleted, so recoverable. |

### Expiring soon

Keep this ordered by date. **Partly automated as of 2026-07-26:** #838 reads the
expiry of any credential that states one, #1035 makes it run at worker start
rather than only daily, and #1024 distinguishes "checked, none stated" from "not
looked yet". Verified live after the Tier-2 deploy — all 13 prod connections
checked, 3 with a real expiry where every one had been NULL that morning.

So the SAS rows below now maintain themselves. What still needs a human is every
credential whose expiry is NOT in the credential — Snowflake PATs above being the
case that bites, since the product cannot know them and will never warn.

| Expires | Credential | Action needed |
|---|---|---|
| **2026-08-06** | Snowflake `DATAQ_LOADER_PAT` | Re-mint; write **both** `snowflake-loader-pat` and `snowflake-password-harness`, then `terraform apply` so the containers pick it up. |
| **2026-08-10** | Snowflake **ACCOUNTADMIN PAT** | Deliberately short (15d). Re-mint into harness `secrets.sh` only if a `terraform apply` is needed; otherwise let it lapse — nothing runs on it day to day, and `harness_window.sh` only calls `terraform output`, which needs no credential. |
| **2026-08-20** | Snowflake `DATAQ_READER_PAT` | Re-mint; write **all three**: `conn-snowflake-retail-dev-6729c4f9`, `conn-snowflake-orders-dev-1c62b0c3`, `conn-snowflake-payments-dev-f53de47d` — then test each connection. (Renamed 2026-07-27; the old `conn-snowflake-*` keys no longer exist.) |
| **2027-06-28** | ADLS SAS (`ADLS — Raw`, `ADLS — landing`) | Read automatically by #838 once the sweep ran — the `se=` in the token. No manual capture needed; this row is now maintained by the product. |
| **2027-07-12** | dbt artifacts SAS (`dbt — Retail Lineage`) | Same — read from the token. |
| n/a | Databricks PAT (`conn-unity-catalog-*`), Snowflake key-pair, S3 keys | **Checked, and genuinely stateless** — these credential types carry no readable expiry, so #838 is correctly silent rather than unknown (#1024 made that distinction visible). Their expiry, where one exists, lives only in the issuing console. |

> The two PATs now expire **two weeks apart**, which is worth noticing: rotating
> one is no longer an occasion to rotate the other, so the "rotate everything at
> once" habit that used to cover the gap no longer applies. Check this table, not
> memory.
