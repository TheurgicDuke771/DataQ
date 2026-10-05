# Notifications & alerting

DataQ alerts on run outcomes over **Microsoft Teams**, **Slack**, **email** and generic
HMAC-signed **webhooks** — all
behind one `ResultPublisher` seam, so every channel gets the same severity-aware
behaviour. Alerts fire from the worker as soon as a run reaches a terminal state.

## Channels

Alert destinations are **channels**: a Teams webhook, a Slack webhook, an email recipient
list or a generic HMAC-signed webhook, each defined **once by a workspace Admin** under
**Admin → Settings → Notification channels** and then linked to any number of suites. A
suite's own panel offers only the channels that exist — nobody types a webhook URL into a
suite. Webhook URLs are validated against a **per-channel** host allow-list (Teams:
`webhook.office.com` / `logic.azure.com`; Slack: `hooks.slack.com`) so a typo cannot
exfiltrate alerts to an arbitrary endpoint, and they are stored in the secret store, never
echoed back.

Workspace-level defaults still exist, enabled by environment configuration
(`TEAMS_WEBHOOK_SECRET_NAME`, `SLACK_WEBHOOK_SECRET_NAME`, `EMAIL_*` — see the
[env-var reference](https://github.com/TheurgicDuke771/DataQ/blob/main/.env.app.example)).
A suite with no channel linked alerts through those; a suite with channels linked alerts
through **both** — delivery is additive, not either/or.

**Legacy inline destinations.** Before channels existed a suite could carry its own Teams
or Slack webhook or recipient list. Those keep delivering, and the suite's panel shows them
as a separate *Legacy inline destinations* card with a **Clear** per entry so an editor can
move the suite onto a channel and retire the override. Setting a new inline destination
is not possible any more — not from the app and not from the API, for anyone: the request
is refused with the field named. A suite's destinations are the channels, full stop.

### What an alert email says

Alert emails are written for someone who doesn't know the check engine. Each failing
check gets one plain sentence ("34,480 rows (99.42%) have a duplicate order_number", "The
newest record is 61.95 days old"), a few already-redacted example values, and the open
incident's context as short labelled facts: how long it has been failing, what triggered
the run, what else failed with it, and which downstream tables may be affected. When a
lineage source behind that last fact is failing, the email says the list may be
incomplete rather than presenting it as the full picture. The raw expected/observed detail
engineers want stays underneath in small print. Numbers are rounded to two decimals
everywhere, in Slack and Teams too.

## Per-suite configuration

Open a suite → **Notifications** panel:

- **Send alerts for this suite** — on/off.
- **Alert threshold** — `On fail / critical` · `On warn and worse` (default) · `Always
  (every run)`.
- **Channels** — the admin-configured channels this suite alerts through; pick from the
  list. Viewers see the linked names only.

## Severity-aware routing

The run's **worst severity** decides how loudly the alert lands: `warn` renders quiet,
`fail` standard, and `critical` escalates (channel mention on Teams). A run that
**failed to execute** (the datasource was unreachable, the adapter raised) always
alerts regardless of the suite's threshold — an operational failure is never filtered
as "no warn-level breach".

## Dedup — first failure, not every run

A broken check on a 15-minute schedule would otherwise page you 96 times a day. DataQ
compares each run's failing checks to the suite's **previous terminal run** and alerts
only when something got **worse**: a check newly failing, or escalating severity
(warn → fail → critical). A clean run resets the baseline, so the *next* regression
re-fires. No configuration needed.

## Snooze / suppression

Snooze a specific check's alerts for N hours from the suite's check list (e.g. during a
known upstream incident). A run alerts only if at least one **un-snoozed** check is
failing; when every failing check is snoozed, the alert is suppressed. Snoozes expire
automatically.

## Connection poll-health alerts

Run alerts tell you a **check** broke. This one tells you the **pipe** broke.

An orchestration connection (ADF / Airflow / dbt) is polled every 10 minutes. When that
poll starts failing — an expired credential, a revoked token, an orchestrator that moved
— DataQ stops ingesting pipeline runs, stops firing the suites bound to them, and stops
refreshing any lineage the connection feeds. Nothing is *failing*; things are simply not
*happening*, which is far easier to miss. Prod lineage was dark for six days on exactly
this before the alert existed.

So after **3 consecutive failed polls** (~30 minutes — enough to ride out a restarting
orchestrator or a transient 502), DataQ pushes an alert through the same channels as run
alerts, carrying the connection, the classified reason, and how long it has been down.

- **It fires on the crossing, and only the crossing.** A connection dead for a week
  alerts once, not a thousand times — an alert you have to mute is an alert that stops
  working.
- **Recovery is signalled too**, so the loop closes without you going to look.
- **The reason is classified, never the raw error** (`auth_failed`, `not_found`, …). The
  real prod incident above carried the SAS token inside its exception message, and an
  alert is the one place that string would leave DataQ.
- No per-suite config applies — a connection has no suite, so these go to the
  **workspace** channel (`TEAMS_WEBHOOK_SECRET_NAME` / `SLACK_WEBHOOK_SECRET_NAME` /
  `EMAIL_TO`).

Tune with `ORCHESTRATION_POLL_FAILURE_ALERT_THRESHOLD` (default `3`; `0` disables the
push). Disabling the push does **not** blind the UI: the connections list still badges a
failing poll with its failure count, and the lineage panel still warns rather than
showing a confident empty graph.

## Pipeline duration and overdue alerts

A poll-health alert says DataQ can't see the pipeline. This one says it can, and the pipeline
itself looks wrong, usually before any check on its data has failed.

Every 10 minutes DataQ looks at each pipeline or DAG that a trigger binding points a suite at,
and compares it with that pipeline's own history in the same environment:

- **Unusual duration.** The latest successful run is scored against the previous 20 successful
  runs. At 3 or more standard deviations from their mean, in either direction, DataQ alerts.
  A run that finished far faster than usual often loaded less data than usual. It needs at
  least 5 earlier runs before it scores anything, and a pipeline that always takes exactly the
  same time is not flagged for a second's wobble: the spread is never taken as less than 10% of
  the mean.
- **Overdue.** No successful run within the pipeline's usual cadence, which is its largest gap
  between successful runs plus 25%, the same threshold DataQ suggests for a freshness check on a
  bound suite. The suites it triggers have not run, so their data is going stale before any
  freshness check says so. While DataQ can't poll the pipeline's connection, a missing run
  proves nothing, so no overdue alert is raised; the poll-health alerts above cover that case.

For example, `load_orders` usually takes 12 to 14 minutes. Today's run took 41 minutes, so DataQ
sends *pipeline ran much slower than usual: airflow load_orders (prod)* with the run's duration,
the usual duration and how far out it was. When the next run is back to normal, a recovery
message follows.

Like poll-health alerts, these go to the **workspace** channel, fire once on the crossing, send
recovery, and are retried until a channel actually delivers them. Disabling the binding, or
the check, closes an outstanding alert without claiming the pipeline recovered. Tune with
`PIPELINE_BASELINE_Z_THRESHOLD` (default `3.0`; `0` turns the check off). The incident evidence
card shows the same comparison for the pipeline run that triggered a failing suite.

## Troubleshooting

| Symptom | Check |
|---|---|
| No alert on a failing run | Suite panel: enabled? threshold covers the severity? Dedup: did the *same* checks already fail in the previous run? All failing checks snoozed? |
| Teams/Slack alert rejected | Webhook host must be on the allowed-hosts list; the URL secret must exist in the secret store. |
| Alert on every run wanted | Set the suite's threshold to **Always (every run)** — dedup still applies to failures, but clean runs report too. |
