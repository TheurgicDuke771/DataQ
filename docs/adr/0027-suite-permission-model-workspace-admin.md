# ADR 0027 — Suite permission model: workspace-admin as implicit suite-admin; drop grantable suite-admin

- **Status:** Accepted
- **Date:** 2026-06-30
- **Deciders:** @TheurgicDuke771
- **Note:** the suite permission tiers were never formalised in an ADR — they were established directly in `suite_authz.py`; this ADR records and revises that model (so there is no prior ADR to mark `Superseded`). Folds in the workspace-admin scope decisions tracked as #411 / #412.
- **Related:** ADR [0010](0010-provider-agnostic-infrastructure-seams.md) (the generic `get_current_user` identity seam — Azure is one impl), [0020](0020-history-and-audit-strategy.md) (audit), [0026](0026-auth-api-keys-and-principal-seam.md) (principal/identity seam), compliance posture (#431 data-access audit)
- **Issue:** [#482](https://github.com/TheurgicDuke771/DataQ/issues/482) (supersedes [#411](https://github.com/TheurgicDuke771/DataQ/issues/411), [#412](https://github.com/TheurgicDuke771/DataQ/issues/412))

## Context

Suite authorization (`backend/app/services/suite_authz.py`) ranks four tiers —
`view < edit < admin < owner` — where a user's effective level is the highest of
**owner** (implicit: they are `suite.created_by`) or a **share** row
(`view`/`edit`/`admin`). The capability ladder:

| Tier | Adds |
|---|---|
| view | read suite, checks, results |
| edit | + create/update/delete checks, update suite, trigger runs |
| admin | + manage shares (grant/revoke) **and** delete the suite |
| owner | (identical capabilities to admin) — but the immutable creator |

Two problems with this shape:

1. **`owner` and `admin` are capability-identical.** The only difference is
   lifecycle (owner is the immutable creator; admin is a grantable/revocable
   share). Users can't tell what `admin` means versus `owner`, and the tier earns
   its keep only as a *delegation* mechanism.
2. **The delegation it enables is the wrong shape for a single-tenant product.**
   `admin` is grantable to any peer, so the most privileged capabilities
   (manage who-can-see-this, delete-the-suite) can be handed around, including an
   admin-revokes-another-admin escalation edge. Meanwhile the **workspace-admin**
   role (`WORKSPACE_ADMIN_EMAILS` → `is_workspace_admin`, ADR-era #289) is *read
   only*: it can see every suite/user/grant on `/admin` but cannot act — it can
   spot an orphaned or junk suite and do nothing about it. The product has a
   governance role that can't govern, and a per-suite admin tier that
   over-delegates.

DataQ is **single-tenant with suite-level access sharing** (CLAUDE.md §1). The
natural role model for that is *resource owner + platform admin (superuser) +
collaborators* — the shape GitHub-org and Google-Workspace use.

## Decision

Redefine the tiers so each maps to a distinct purpose, and make the
workspace-admin the governance actor instead of a grantable per-suite tier.

- **owner** — the creator; exactly one per suite; full control; immutable
  (cannot be revoked, demoted, or granted as a share).
- **admin** — **the workspace-admin(s), implicit on every suite** (computed from
  `is_workspace_admin`, never a `shares` row). Same capabilities as owner: manage
  shares, delete, edit, run, read. This is the break-glass / owner-on-leave /
  governance path.
- **edit / view** — the only permissions **grantable to normal users**. A normal
  user can no longer be made `admin` of a suite.

**Visibility (option a, decided):** workspace-admins get a **workspace-wide view
in the normal product surface** — Dashboard, Suites, Results — not just the
`/admin` page. This is consistent with being implicit admin everywhere, and it
**supersedes #411** (workspace-wide Dashboard/Results) and **#412**
(workspace-admin write actions), which this decision absorbs.

Net per suite: **one owner + workspace-admin-as-admin + edit/view collaborators.**

## Consequences

**Positive**
- Roles map to purpose: creator / platform-governance / collaborator. The
  confusing redundant tier is gone.
- The governance role can finally act — delete a junk suite, re-share an orphaned
  one — without first being granted access to it.
- Collaborators are strictly least-privilege: `edit` is the ceiling; they can't
  delete the suite or change its sharing. The admin-revokes-admin escalation edge
  disappears (there are no grantable admins).

**Costs / risks (accepted)**
- **Workspace-admin becomes a write superuser over all suites** — manage shares,
  delete, edit, and **read every suite's results including failing-sample data**
  (which can be sensitive; PII redaction still applies at the logger/sample
  layer). This is a deliberate expansion from today's read-only oversight and
  wants a security note; the data-access audit trail (#431) should record
  workspace-admin reads. Hold the workspace-admin allowlist tightly.
- **No peer-to-peer delegation of suite management.** Management/deletion by a
  non-owner now funnels to workspace-admins. Mitigation: the allowlist already
  supports **multiple** workspace-admins — use that to avoid a bottleneck. (For
  most teams, "manage access" and "delete" are governance actions, so centralising
  them is acceptable.)
- **`owner` remains immutable / non-reassignable.** Workspace-admin is now the
  succession path for an orphaned suite; explicit owner-reassignment stays a
  separate future item.

**Implementation shape** (full plan in #482)
- `effective_permission` / `effective_permissions` return `admin` when the user
  is a workspace-admin (computed, not a `shares` lookup).
- `require_permission` takes an `is_workspace_admin` signal — today it is a pure
  `session + user_id` primitive; the flag is resolved from the allowlist at the
  `/me`/API layer and threaded in (keeps it unit-testable).
- The share validator accepts only `view`/`edit` (rejects `admin`).
- Suite list/read scoping, Dashboard, and Results include all suites for
  workspace-admins (the #411/#412 surface).
- **Backward-compatible migration:** downgrade existing `shares.permission =
  'admin'` rows → `edit` (two-step deploy per the migration rule; comms to anyone
  currently holding an admin share).

## Alternatives considered

- **Keep the status quo (grantable suite-admin).** Rejected: the redundant
  owner/admin tier confuses users, over-delegates the privileged capabilities,
  and leaves the workspace-admin role unable to govern. Its one real benefit —
  peer delegation of one suite without granting global admin — is outweighed in a
  single-tenant workspace, and is recoverable via multiple workspace-admins.
- **Restrict who can *grant* admin (owner-only) + make `delete` owner-only.** A
  smaller change that fixes the escalation edge while keeping peer admin. Rejected
  in favour of the cleaner role model, but noted as the minimal fallback if the
  superuser expansion proves too broad.
- **Reserve all manage/delete to workspace-admins, drop owner control too.**
  Rejected: removes the creator's authority over their own suite and maximises the
  bottleneck.

## Related

- Supersedes #411 (workspace-wide Dashboard/Results view) and #412
  (workspace-admin write actions from `/admin`).
- #431 — data-access audit trail (workspace-admin reads of results/samples should
  be auditable under this expanded access).
- ADR 0010 — workspace-admin is derived from the generic identity seam
  (`is_workspace_admin` off a config allowlist), not from Azure/Entra claims.
