# Recommended usage

How to **activate** each DataQ feature and the **recommended** way to configure it. This is
the setup-time companion to [Features](features.md) (what exists) and
[Best practices](best-practices.md) (the run-time "signal, not noise" philosophy) — follow it
top to bottom the first time you set up a workspace.

Each step is **Do** (how to turn it on) → **Recommended** (the way that pays off later).

## 1. Connect a datasource

**Do:** Connections → **Add connection** → pick the type → fill the spec-driven form → **Test**.

**Recommended:**

- Use a **least-privilege, read-only** credential scoped to what the checks read.
- **Snowflake:** prefer **key-pair** auth over a password; use a dedicated role/warehouse.
- Create **one connection per environment** (`snowflake-dev`, `snowflake-prod`) so a suite's
  env is unambiguous and promotion is a re-point, not a rewrite.
- ADF / Airflow / dbt are **orchestration providers**, not datasources — add them here too,
  but you don't write checks against them (see step 5).

## 2. Author a suite and its first checks

**Do:** Suites → **New suite** → bind a connection + **target** (a table, or a file/batch
pattern) → add checks in the editor.

**Recommended:**

- **One suite = one target = one env.** Name it for what it watches (`orders — snowflake prod`).
- **Start with freshness + volume**, not value-level rules — most real incidents are
  "the load didn't run" or "half the rows are missing". Add a **freshness** monitor on the
  load/updated timestamp and a **volume** monitor before anything else.
- Use the **column profiler** and a **dry-run** to see today's numbers *before* writing a
  value check, so you set it against reality.
- Reach for **Custom SQL** only when a rule spans columns or needs a join — prefer a catalog
  expectation when one exists (cheaper, better samples). See
  [Custom SQL best practice](best-practices.md#custom-sql-the-escape-hatch-not-the-default).

Each check is classified by **DQ dimension** automatically (a not-null check is
Completeness, a freshness monitor is Timeliness). Leave it unless the check means something
else — the same range check is *Validity* when it bounds a percentage and *Accuracy* when it
asserts a reconciled total. Custom-SQL checks have no derivable dimension, so set one
yourself or leave them unclassified; the asset scorecard counts them separately rather than
filing them under a dimension they may not belong to.

## 3. Set severity thresholds that mean something

**Do:** on each check, set **warn / fail / critical** thresholds (or leave blank for binary
pass/fail).

**Recommended:** band from the **dry-run / profiler baseline** — set **warn** just above
today's observed unexpected-%, **fail** at an actionable breach, **critical** only for
page-worthy. The check should be **green on day one** and loud only when reality changes.
Full rationale: [Severity best practice](best-practices.md#severity-make-the-tiers-mean-something).

## 4. Protect failing-row samples (column policy)

**Do:** on the suite, open the **column policy** and accept the classifier's **Auto-detect**
suggestion (or set the identifier + PII columns by hand).

**Recommended:** always set a policy on suites over tables that can contain personal data —
it keeps non-sensitive breaches debuggable while masking PII in samples. If you **repoint the
suite to a new target**, re-run **Auto-detect** (DataQ won't clobber your policy
automatically; it logs `suite_policy_possibly_stale`). See
[Protect the samples](best-practices.md#protect-the-samples).

## 5. Automate runs — triggers first, schedules second

**Do (trigger):** register the orchestrator connection → add the provider's webhook
(**Settings → Webhooks** shows the URL) or the callback snippet
([`integrations/`](https://github.com/TheurgicDuke771/DataQ/tree/main/integrations)) → on the
suite, **Triggers** → bind `(provider, pipeline/DAG/job, env)`.

**Do (schedule):** suite → **Schedules** → a 5-field cron + timezone.

**Recommended:** if an orchestrator produces the data, **trigger** on its success — it *knows*
when the data is ready and correlates results to the pipeline run. **Schedule** only what has
no orchestrator (e.g. flat-file drops), at a cadence matched to arrival, not "every 5 minutes
to be safe". See [Prefer triggers over schedules](best-practices.md#prefer-triggers-over-schedules-where-an-orchestrator-exists).

## 6. Configure alerting

**Do:** Suite → **Notifications** → pick channels (Teams / Slack / email), the threshold, and
recipients. Store channel webhooks/keys as secrets (**Settings → Webhooks** for the inbound
ones).

**Recommended:**

- Keep the default **warn-and-worse** threshold; drop to **fail-only** for noisy exploratory
  suites rather than turning alerts off.
- **Snooze** a known-broken check during an incident instead of deleting it — the history and
  the re-fire on expiry are the point.
- Trust **dedup**: a red suite that's quiet is dedup working; the Results page is ground truth.

See [Alerting hygiene](best-practices.md#alerting-hygiene).

## 7. Wire an AI assistant (MCP)

**Do:** mint a **PAT** (Profile → API keys), then add DataQ's `/mcp` server to your client
(Claude Desktop / VS Code Copilot / Cursor) with the PAT as the bearer — full walkthrough in
[AI assistants (MCP setup)](mcp-setup.md).

**Recommended:** give the assistant a **scoped PAT** (a member, not an admin, unless it needs
workspace-wide reach), set a sensible expiry, and rotate it like any credential. The assistant
acts **as that user** — it sees exactly what the user can.

## 8. Set up access

**Do:** users sign in via **SSO**. Add workspace admins via the `WORKSPACE_ADMIN_EMAILS`
allowlist; share individual suites (**view / edit**) from the suite.

**Recommended:**

- **Share by default with `view`**; reserve `edit` for the owning team.
- **Keep `WORKSPACE_ADMIN_EMAILS` minimal** — a workspace admin can read *every* suite's
  results, including failing-row samples (the one place PII lands), and manage/delete any
  suite (ADR 0027). For a regulated/PHI deployment, treat the access-audit trail as a
  prerequisite before granting it broadly.

---

Once this is in place, the [Best practices](best-practices.md) page covers the ongoing
operating discipline that keeps the alerts meaningful.
