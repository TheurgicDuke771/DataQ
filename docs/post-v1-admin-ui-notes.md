# Post-v1 notes — Admin, access model & UI/IA (deferred design)

> **Status: deferred to post-v1.** Captured so the design intent isn't lost. We
> intentionally did **not** build the admin/IAM console in v1. For a single-tenant
> tool used by one trusted data team, a full RBAC console (admin write console,
> user-disable, per-connection ownership restrictions, an access matrix) is
> gold-plating. The market-leading DQ tools (Great Expectations, Soda, dbt tests,
> Monte Carlo) lead with **checks → results → trends → alerts**, not user-lifecycle
> management. v1 ships suite-level sharing + a read-only admin view; everything
> else is recorded here for later.
>
> Related issues: **#411** (admin workspace-wide view) and **#412** (admin write
> actions), both milestoned _Backlog (post-v1 / testing)_.

## v1 access model (what actually ships)

- **Per-suite access levels:** `view` / `edit` / `admin` / `owner` (suite-scoped sharing).
- **Workspace-admin:** a config allowlist `WORKSPACE_ADMIN_EMAILS` — a generic identity
  axis, **no** Azure/Entra claim read in route/service code, no migration. `dataq-admin`
  is the workspace-admin.
- **Normal users:** *owned-or-shared* scoping — Dashboard / Suites / Results show only
  suites they own or that are shared with them.
- **Workspace-admin in v1:** sees workspace-wide data **only** via the `/admin` page
  (Suites · Users · Access tabs, unscoped read). Dashboard / Suites / Results stay
  owned-or-shared scoped even for an admin — the gap #411 addresses.

## Post-v1: how Admins view the UI (→ #411)

- Today an admin's Dashboard/Results are near-empty (owned-or-shared scoped); they only
  see workspace-wide via `/admin`.
- **Intent:** give the workspace-admin a workspace-wide view on Dashboard + Results
  (a scope toggle, or implicit for admins) so the admin's home isn't blank.
- **Keep it small.** This is a "don't show an admin a blank dashboard" fix — **not** a
  launchpad for a write console.

## Post-v1: what access Admins should have (→ #412)

- Today `/admin` is **read-only** (view suites / users / access).
- **Envisioned write actions:** manage shares (grant/revoke per-suite access), manage
  suites (reassign owner, delete) from `/admin`.
- **Decision (per the strategic review): keep minimal for single-tenant.** Defer or cut:
  user-disable, per-connection ownership RBAC restrictions, a full access-matrix editor,
  and "bypass-everything" admin reads. These solve multi-tenant problems we don't have.
  The repeated patching (admin sees nothing → admin can't write → bypass-everything) is a
  smell that the access model is more elaborate than the single-tenant use case warrants.

## Post-v1: how normal users view the UI

- *Owned-or-shared* scoping stays — it's correct for v1 and beyond.
- Suite-level sharing (`view`/`edit`/`admin`/`owner`) is the access primitive, and it's
  sufficient for a single team. Don't grow a second, workspace-level RBAC layer unless the
  product actually goes multi-tenant (that's a BYOL/SaaS decision — see ADR 0013).

## Post-v1: Settings / Profile page

- The Week-6 prototype had **Profile** content (#374) + **Workspace Settings** (#375);
  several fields shipped as **clearly-labelled placeholders** (feature honesty).
- The Profile/Settings IA shuffle is **low-value polish right now — defer.** When picked up:
  - **Profile:** identity, the user's owned suites, an access summary.
  - **Workspace Settings:** notification channels (see below), run-history retention, and the
    admin allowlist surfaced **read-only**.
- Theme / timezone / dark-mode were **not** adopted in v1 (ADR 0022); revisit post-v1 only
  if users ask.

## What to keep building (not admin) — reusable notification channels

- "Define a Teams/Slack/email channel **once**, reference it from many suites + severities"
  is a real **platform** DQ feature — keep it, but build it as a notification feature, not
  part of an admin console. (Folds in #389: rename `teams_webhook_secret_name` →
  channel-neutral before a 2nd `ResultPublisher` ships.)

## Guiding principle for the post-v1 pickup

The foundations (deploy, connection adapters, run engine, alerting backend, the
check/`monitor-kind` model) are solid — aim them at the **data-quality loop**
(results → trends → freshness/volume monitors → alerts → MCP tools), not admin features.
Build admin UI only to the extent a single-tenant team needs: don't show a blank
dashboard (#411), grant/revoke a share (#412, minimal). Everything else admin/IAM = defer.
