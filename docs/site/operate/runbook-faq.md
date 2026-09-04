# Runbook & FAQ

## Release checklist

- All CI gates green on `main`; no open release-blocking issues on the current milestone.
- **CHANGELOG updated at tag time** (minor/major releases; patch only if user-visible or
  security-relevant — see the policy in
  [CHANGELOG.md](https://github.com/TheurgicDuke771/DataQ/blob/main/CHANGELOG.md)):
  curate `git log v<prev>..HEAD --oneline` down to what a deployer/user acts on, and
  mirror the entry into the GitHub Release body.
- Backend image built + pushed to GHCR with an **immutable** tag (not `latest`).
- DB migrations are backward-compatible; the migrate job runs `alembic upgrade head`
  **before** the app rolls.
- Deploy via the **Deploy** workflow (`workflow_dispatch`); verify `/healthz` 200,
  `/api/v1/me` 401 (auth enforced), SPA + deep links load.
- Docs site published (this site) and linked from the README.

Full deploy steps + verification: the repository's **`deploy/README.md`**.

## Live smoke (deployed stack + harness data)

> Scale numbers are in [perf-baseline.md](../architecture/perf-baseline.md).

Automated, opt-in (never CI):

1. **API-level:** `DATAQ_API=https://<frontend-host> DATAQ_BEARER=<AAD token> python -m
   backend.scripts.e2e_smoke` — read + authoring round-trips against the live API
   (12 checks).
2. **Browser-level:** `E2E_LIVE_BASE_URL=https://<frontend-host> pnpm e2e` in
   `frontend/` — a headed one-time OIDC sign-in, then read-only specs (dashboard KPIs,
   live suite + checks, run-detail). See the repository's `frontend/e2e/README.md`.

Manual checklist (the mutating tail):

- Trigger a live suite run (Run now) on a harness suite → completes green on real
  warehouse/file data.
- Let a harness pipeline (ADF or Airflow) succeed → the bound suite auto-runs and
  correlates on **Results → Pipelines**.
- Force a failing run → the Teams/Slack/email alert arrives with the right severity;
  a repeat failure is deduped.
- MCP: point Claude Desktop at `https://<frontend-host>/mcp/` (trailing slash — see
  [AI assistants (MCP setup)](../guides/mcp-setup.md)) and run the 4 canonical queries
  (what failed today / run suite X / why did pipeline Y fail / add a null check).

## Known limitations

- **GX by default**, with platform-native engines connection-anchored per ADR 0036 — a
  Snowflake connection additionally offers the **DMF** engine (four expectation types
  currently); Databricks DQX and Dataplex are trigger-gated and not yet built. Batch-oriented
  (not streaming).
- **Single tenant**, suite-level access sharing **plus a stored workspace role** — Admin / Member / Viewer (ADR 0033); connection management is Admin-only. `WORKSPACE_ADMIN_EMAILS` is a bootstrap seed and lockout break-glass, not the day-to-day mechanism.
- Interactive **datasource browsing** (container browser, 3-level UC catalog picker) is
  deferred — you specify targets explicitly. JSON flat files deferred (CSV/Parquet in v1).
- Auth is one of **OIDC SSO (Azure AD or Cognito), email OTP (ADR 0032, IdP-less), or dev-bypass**,
  plus **PATs** (`dq_live_…`, ADR 0026) for headless/API/MCP clients — no
  username/password login, and no separate service-account principal yet (ADR 0026
  phase 2, deferred).

## FAQ

**Is ADF/Airflow a datasource?** No — they're orchestration providers DataQ monitors and
can trigger from. You never write checks against them. See **[Concepts](../get-started/concepts.md)**.

**Do I need Azure to run it locally?** No. The compose stacks sign you in with an emailed
one-time code and bundle the mailbox too (a local Mailpit inbox at `localhost:8025`), so
there is no IdP *and* no SMTP relay to bring — `scripts/setup.sh` just asks which address
may sign in. Dev-bypass is still there as an explicit downgrade (leave that answer blank).
Azure and AWS are both live deployment targets behind the app's seams (ADR 0010/0013), at the same level — neither is primary.

**Where do failed-row samples go?** Stored with the result, **PII-redacted**, and purged
after a retention window — never written to logs.

**Can an AI assistant use DataQ?** Yes — 47 MCP tools at `/mcp` (Claude Desktop / Claude.ai
/ Copilot / Cursor), OIDC-authenticated (Azure AD or Cognito) or via a PAT. See [AI assistants (MCP setup)](../guides/mcp-setup.md).

**An asset shows no lineage — is that right?** Maybe not. "No lineage recorded" can mean an
asset genuinely has no upstreams, or that DataQ has been unable to read your dbt artifacts —
check the connections list badge and the lineage panel warning before trusting an empty graph.
Test the dbt connection first: its secret is the artifacts-store read credential, and when it
expires the poll fails while your dbt builds keep succeeding. Note that fixing the credential
alone will **not** backfill — the poll's 15-minute lookback means every build produced during
the outage is already stranded, so you must re-run the dbt build to get a fresh artifact into
the window.
Full detail in [Orchestration → When lineage is empty](../guides/orchestration.md#when-lineage-is-empty-check-the-poll-before-you-check-the-graph).
