# Your first alert

Ten minutes. You end with a message in Teams, Slack or your inbox the next time a check
breaches — and silence when nothing changed.

## 1. Decide where alerts go

Alerts are configured **per suite**, so the team that owns the data gets told about its data.
Workspace-wide defaults (a Slack webhook, an SMTP sender, a Teams webhook) are set by whoever
deployed DataQ through environment configuration; a suite can override the Teams webhook and
the email recipients.

![Admin → Settings → Notification channels explains that alert routing lives on each suite](../assets/screenshots/settings-notifications.png){ .screenshot }

*Workspace settings point you back to the suite: that is where the routing lives.*

## 2. Turn alerts on for the suite

Open the suite and scroll to **Notifications**:

![A suite's Notifications panel: the on/off switch, the alert threshold, optional Teams and Slack webhooks, and email recipients](../assets/screenshots/suite-notifications.png){ .screenshot }

*Everything an alert needs, on the suite itself.*

1. Switch **Send alerts for this suite** on.
2. Pick the **alert threshold**. The default, *On warn and worse*, is the right first choice:
   quiet for a passing run, loud the moment a check crosses a threshold you set.
3. Optionally paste a **Teams** or **Slack** webhook for this suite, or list **email
   recipients**. Leave a field blank to fall back to the workspace default. Webhook URLs are
   write-only: after saving, the tag reads *set* and the URL is never shown again.
4. **Save**.

## 3. Prove it fires

Edit a check so it must breach — set a `warn` threshold below today's unexpected-% you saw in
the dry-run — and click **Run**. Within seconds of the run finishing, the alert lands with the
suite name, the worst severity, the checks that breached and a link back to the run.

Put the threshold back afterwards. Now the same run is green and **nothing fires**: DataQ
alerts only when a run got *worse* than the previous one (a newly failing check, or an
escalation from warn to fail to critical), so a broken check on a 15-minute schedule reports
once, not 96 times a day.

## What else you get for free

- **Severity-aware delivery** — `critical` escalates (a channel mention on Teams), `warn` stays
  quiet.
- **Operational failures always alert** — a run that could not execute (dead credential,
  unreachable store) notifies regardless of the threshold, with a plain-language reason.
- **Snooze** — from the suite's check list, silence one check for N hours during a known
  upstream incident; it un-snoozes itself.
- **Incidents** — a critical breach opens an incident on the asset, with an evidence card you
  can acknowledge and resolve from the run page or over MCP.

Everything above, in depth: [Notifications & alerting](../guides/notifications.md).
