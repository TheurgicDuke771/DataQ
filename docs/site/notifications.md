# Notifications & alerting

DataQ alerts on run outcomes over **Microsoft Teams**, **Slack**, and **email** — all
behind one `ResultPublisher` seam, so every channel gets the same severity-aware
behaviour. Alerts fire from the worker as soon as a run reaches a terminal state.

## Channels

| Channel | Configured by | How |
|---|---|---|
| Microsoft Teams | Workspace default **or per-suite override** | Incoming-webhook URL; the per-suite URL is set on the suite's **Notifications** panel (write-only — stored in the secret store, never echoed back) |
| Slack | Workspace | Incoming-webhook URL (secret store) |
| Email (SMTP) | Workspace | SMTP host/port + from/to + password secret |

Workspace-level channels are enabled by environment configuration
(`TEAMS_WEBHOOK_SECRET_NAME`, `SLACK_WEBHOOK_SECRET_NAME`, `EMAIL_*` — see the
[env-var reference](https://github.com/TheurgicDuke771/DataQ/blob/main/.env.app.example)).
A channel with no configuration is simply skipped; configuring none disables alerting.
Webhook URLs are validated against a **per-channel** host allow-list (Teams:
`webhook.office.com` / `logic.azure.com`; Slack: `hooks.slack.com`) so a typo can't
exfiltrate alerts to an arbitrary endpoint. Only Teams has a per-suite override; Slack
and email are workspace-wide.

## Per-suite configuration

Open a suite → **Notifications** panel:

- **Send alerts for this suite** — on/off.
- **Alert threshold** — `On fail / critical` · `On warn and worse` (default) · `Always
  (every run)`.
- **Teams webhook** — optional per-suite override of the workspace webhook. Write-only:
  the tag shows *set / not set*, the URL is never displayed again.

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
this ([#828](https://github.com/TheurgicDuke771/DataQ/issues/828)).

So after **3 consecutive failed polls** (~30 minutes — enough to ride out a restarting
orchestrator or a transient 502), DataQ pushes an alert through the same channels as run
alerts, carrying the connection, the classified reason, and how long it has been down.

- **It fires on the crossing, and only the crossing.** A connection dead for a week
  alerts once, not a thousand times — an alert you have to mute is an alert that stops
  working.
- **Recovery is signalled too**, so the loop closes without you going to look.
- **The reason is classified, never the raw error** (`auth_failed`, `not_found`, …). The
  real #828 exception carried the SAS token inside its message, and an alert is the one
  place that string would leave DataQ.
- No per-suite config applies — a connection has no suite, so these go to the
  **workspace** channel (`TEAMS_WEBHOOK_SECRET_NAME` / `SLACK_WEBHOOK_SECRET_NAME` /
  `EMAIL_TO`).

Tune with `ORCHESTRATION_POLL_FAILURE_ALERT_THRESHOLD` (default `3`; `0` disables the
push). Disabling the push does **not** blind the UI: the connections list still badges a
failing poll with its failure count, and the lineage panel still warns rather than
showing a confident empty graph.

## Troubleshooting

| Symptom | Check |
|---|---|
| No alert on a failing run | Suite panel: enabled? threshold covers the severity? Dedup: did the *same* checks already fail in the previous run? All failing checks snoozed? |
| Teams/Slack alert rejected | Webhook host must be on the allowed-hosts list; the URL secret must exist in the secret store. |
| Alert on every run wanted | Set the suite's threshold to **Always (every run)** — dedup still applies to failures, but clean runs report too. |
