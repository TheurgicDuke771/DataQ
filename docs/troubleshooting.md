# Troubleshooting

Common problems and where to look. For deeper telemetry (logs, traces, where they land) see
[Observability](observability.md); for deploy-time issues see the
[Deployment](deployment.md) checklists.

## Connections

**"Test" fails on a connection.**

- Re-check the credential and that the account/host/warehouse names are exact.
- The credential must be reachable **from where DataQ runs** (network / firewall), not just
  from your laptop.
- Use **Rotate credential** (re-auth) if the secret expired.
- A missing/expired warehouse or role usually shows as a config/permission error.

## Runs

**A run shows `failed` (not just failing checks).**

`failed` means the run couldn't *execute* (distinct from a check failing). The run detail now
shows a **redaction-safe reason** — one of:

- *config* — the connection or run target looks misconfigured (missing warehouse/role, or a
  table/path that doesn't exist). Fix the connection or the suite's target.
- *connectivity* — the datasource couldn't be reached (network/DNS/TLS/timeout).
- *permission* — credentials rejected or a grant is missing. Rotate the credential / add the
  grant.

**A check is `error` or `skip`, not pass/fail.** These are *operational* statuses, kept
distinct from data failures: `error` = the check couldn't be evaluated (e.g. a missing
column); `skip` = a precondition wasn't met (e.g. a batch file hasn't landed yet).

**`checks_total` reads 0 / `—`.** A run that failed before any check executed evaluated
nothing, so its data-quality outcome is empty — that's truthful, distinct from the suite's
defined check count shown on the progress view.

**Dry-run returns a 502.** The datasource couldn't be reached or the query couldn't run; the
error detail carries a safe *reason* (config / connectivity / permission). The raw adapter
error is never echoed (it can carry credential fragments) — check the server logs for detail.

## Schedules

**A scheduled run didn't fire.** Confirm the schedule is **enabled**, the cron + timezone are
right, and remember DataQ does **no backfill** — a missed window isn't retried, the next one
runs. See [Scheduling](scheduling.md).

## Triggers / orchestration

**A pipeline succeeded but no suite ran.** Check there's an **enabled trigger binding** matching
`(provider, pipeline/DAG/job, env)` exactly, and that the webhook is configured (or the
10-minute poll fallback can reach the artifacts/REST API). Failure events **alert but never
trigger** a run — by design. See [Orchestration](orchestration.md).

## Alerts

**No alert arrived for a failing run.**

- Check the suite's **Notifications** threshold — the default is warn-and-worse; `fail-only`
  suppresses warns.
- **Dedup** is intentional: you're alerted on the first breach (and on escalation), not on
  every run. A red suite that's "quiet" is usually dedup working — the **Results** page is the
  ground truth.
- Check the channel isn't **snoozed** for that check.
- Confirm the channel secret/webhook is set (Settings → Webhooks / per-suite config).

## Sign-in & API

- **401 on the API or MCP** = no/invalid token. For scripts, mint a fresh [PAT](api-keys.md);
  it expires and can be revoked.
- **SSO won't complete** — confirm the IdP app registration + redirect URIs match the deployed
  frontend URL.

## AI assistants (MCP)

- **401 on `/mcp/`** = auth (expected without a token) — see [MCP setup](mcp-setup.md).
- **307** = missing trailing slash; use `/mcp/`.
- **421 Misdirected Request** = the transport's DNS-rebind Host guard rejecting the proxied
  Host — a deployment-side config issue (the server allow-lists the proxied hosts); it is not
  something a client can fix.

## Still stuck?

Check [Observability](observability.md) for where logs/traces land, and the
[Runbook & FAQ](runbook-faq.md) for known limitations.
