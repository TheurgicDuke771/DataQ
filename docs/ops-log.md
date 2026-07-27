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
