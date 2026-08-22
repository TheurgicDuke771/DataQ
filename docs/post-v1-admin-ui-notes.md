# Post-v1 notes — Admin, access model & UI/IA (deferred design)

> ## ⚠️ Superseded 2026-08-16 by ADR [0033](adr/0033-workspace-roles-rbac.md)
>
> **The "keep it minimal / defer RBAC as gold-plating" decision below did not
> hold**, and this file is kept as a record of the earlier reasoning rather than
> current design intent. Two of the things it explicitly defers — a role-management
> console and per-connection ownership restrictions — are exactly what shipped,
> because the single-tenant premise was the problem rather than the mitigation:
> *any* authenticated user, including one holding a single `view` share, could
> delete or re-credential the connection every suite in the workspace ran on
> (gap G-e). Workspace roles (`admin | member | viewer`) are now stored on
> `users.role`, managed under **Admin → Members**, and connection mutations are
> Admin-only. Read the ADR, not this file, for the shipped model.
>
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
> actions) — **both since closed**, along with #389 (channel-neutral rename).
> The two items below still open are tracked as
> [#1514](https://github.com/TheurgicDuke771/DataQ/issues/1514) (reusable
> notification channels) and
> [#1516](https://github.com/TheurgicDuke771/DataQ/issues/1516) (the deferred
> Profile / Workspace-Settings IA pickup) as of 2026-08-21.

## v1 access model (what actually ships)

- **Per-suite access levels:** `view` / `edit` / `admin` / `owner` (suite-scoped sharing).
- **Workspace-admin:** ~~a config allowlist `WORKSPACE_ADMIN_EMAILS`~~ — **superseded by
  ADR 0033: a stored `users.role`, with the allowlist demoted to a grant-only bootstrap
  seed.** Still a generic identity axis: **no** Azure/Entra claim read in route/service
  code. `dataq-admin` is the workspace-admin.
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
