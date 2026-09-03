# Scheduling suite runs

Three ways to run a suite: **manually** (Run now), on a **cron schedule** (this page),
or **triggered by a pipeline** (see [Orchestration](orchestration.md)). Prefer a
trigger binding when an orchestrator loads the data — the checks run right after the
load, not at a guessed time; schedule when there's no orchestrator (e.g. flat-file
drops on ADLS/S3).

## Add a schedule

Open a suite → **Schedules** panel → enter a cron expression + timezone → **Add**.
A suite can hold several schedules (e.g. hourly on weekdays + a daily deep pass).

- **Cron** is standard 5-field (`minute hour day-of-month month day-of-week`):
  - `0 9 * * 1-5` — 09:00 on weekdays
  - `*/30 * * * *` — every 30 minutes
  - `0 6 1 * *` — 06:00 on the 1st of each month
  Invalid expressions are rejected on save.
- **Timezone** is a full IANA zone (`Europe/London`, `Asia/Kolkata`, …), so `0 9 * * *`
  means 9 AM *local to that zone* — **DST-aware** (the schedule shifts with the zone's
  clock changes, not with UTC).

## Semantics worth knowing

- **Minute granularity.** A dispatcher ticks every 60 s and fires schedules whose
  precomputed next-run time has passed — sub-minute cadences aren't supported.
- **No backfill.** If the platform was down across N ticks, those runs are **not**
  replayed on startup; the schedule simply resumes at its next occurrence. (Missed
  *orchestration events* are different — those have gap recovery.)
- **Pause / resume** with the status switch — the schedule and its cadence are kept,
  delivery stops.
- **Stuck-run safety net.** A run orphaned in `queued`/`running` (dead worker, broker
  hiccup) is failed by a reaper after a threshold, so a schedule can't wedge the suite.

## Where results land

Scheduled runs appear on **Results** and the **Dashboard** like any other run, with
`triggered_by` marking the schedule. Alerting applies per the suite's
[notification config](notifications.md).
