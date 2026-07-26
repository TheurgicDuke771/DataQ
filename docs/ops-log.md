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

---

## Harness lifecycle

Harness compute is **stopped by default** since the 2026-07-04 cost wind-down
(#590) — roughly CAD 17/day awake versus ~0 stopped. `harness_window.sh` opens a
test window (wake → run the flows → sleep again). Anything left running outside a
window is either deliberate or a mistake, and this log is how the two are told
apart.

| When (UTC) | Service | Action | By | Why / expected state |
|---|---|---|---|---|
| 2026-07-18 19:36 | `dataq-harness-airflow` (+ `-worker`, `-trigger`) | **Stopped** | royarijit04@outlook.com | Verified from `systemData.lastModifiedAt` on 2026-07-26, not from memory. Intentional. Consequence, now understood: DataQ polls it every 10 min, ACA's ingress answers 404 for a stopped app, and the connection accumulates failures — 282 by 2026-07-26. Expected to stay down until a test window needs Airflow. |
| 2026-07-26 06:16 | `dataq-app-{api,worker,frontend}` | Deployed `c401572d` | Deploy workflow | App stack, not harness. Recorded here because the roll restarted the worker and reset in-memory state. |

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

### Expiring soon

Keep this ordered by date. It is the one part of the file worth reading at a
glance, and #838's automated warning does **not** cover these — a Snowflake PAT
carries no readable expiry, so the product cannot know it (and
[#1024](https://github.com/TheurgicDuke771/DataQ/issues/1024) notes the SAS case
is inert for up to 24h after a deploy). Until that changes, this table is the
only warning.

| Expires | Credential | Action needed |
|---|---|---|
| **2026-07-29** | Snowflake `DATAQ_READER` / `DATAQ_LOADER` PATs | Re-mint and write **all three** `conn-snowflake-*` secrets, then test each connection. |
| unknown | ADLS SAS (`conn-adls-*`), Databricks PAT (`conn-unity-catalog-*`) | Expiry not recorded — capture it at the next rotation. A SAS carries `se=` in the token, so #838 can read it once the sweep runs. |
