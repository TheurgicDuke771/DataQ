# Changelog

Notable, user-facing changes. Dates are the release/merge date. This is a curated summary —
the per-PR history lives in the repo's commit log and pull requests.

## Unreleased

### Added

- **Zero-sample mode is a workspace setting.** Admin → Settings → Privacy & failing samples
  turns it on without a restart; the environment variable stays the floor and cannot be
  turned off from the app. `GET /admin/deployment` now says which of the two is in force.

### Changed

- ⚠️ **Every DataQ user signs in.** The local stack has one sign-in mode, emailed
  codes, and a mis-set or empty sign-in configuration now stops the API with a message
  naming it instead of coming up open. Existing local setups: an `.env` with an empty
  `DATAQ_SIGNIN_EMAIL=` no longer boots — set an address or re-run `setup.sh`, which
  now re-asks.

- **The admin area is now six routed, deep-linkable pages.** `/admin` splits into
  `overview`, `members`, `suites`, `settings`, `compliance` and `integrations` —
  the tab you are on is the URL, so any tab can be bookmarked, shared or
  reloaded in place, and each page loads only its own data instead of one long
  scroll fetching everything. The standalone workspace **Settings** page folds
  in: `/settings` redirects to `/admin/settings`, and the sidebar carries a
  single **Admin** entry instead of two links to the same area. Every admin
  route is gated at the route, so a deep link (or a demoted user's bookmark)
  gets the Forbidden page and fetches nothing. See the
  [admin control centre guide](../guides/admin.md).

### Added

- **Admins can now operate the workspace, not just observe it.** **Admin → Members**
  gains **Revoke** on any per-suite access grant — previously only a suite's own owner
  could remove a share, so cleaning up after a departure meant first being granted access
  to every suite. **Admin → Suites** gains **Transfer**, the offboarding primitive: a suite
  moves to a new owner, who gets full control, while the previous owner keeps an editor
  grant unless you clear the checkbox (workspace viewers cannot own a suite and are not
  offered). It also gains **Delete** for any suite, behind a confirmation that states the
  exact number of checks, runs, results, schedules and trigger bindings the cascade would
  destroy and requires the suite's name to be typed. All three are audited with the
  admin-override recorded, and the delete's event carries the counts. See the
  [admin control centre guide](../guides/admin.md).

- **Workspace membership is managed in the app.** **Admin → Members** gains an
  **Add member** dialog (email plus an optional initial role) and per-row removal,
  so admitting or removing somebody no longer means editing deployment config and
  restarting. Removal takes effect on that person's **next request** for every
  credential kind — an identity-provider sign-in, a live browser session, and every
  API key they hold — which closes a gap where a departed member's API key kept
  working indefinitely. Adding a member does not create an account at your identity
  provider; that stays a prerequisite, and the dialog says so.

    ⚠️ **Adding the first member turns enforcement on for the whole workspace.**
    Until then nothing changes: who may sign in is decided entirely by your existing
    allowlist settings. The first add also admits every existing user in the same
    transaction, so nobody signed in is evicted — those rows are flagged under a
    **review imported members** banner to confirm or remove, because a user record
    proves somebody signed in once, not that they still belong. The allowlist
    settings stay available as grant-only break-glass. See the
    [admin control centre guide](../guides/admin.md).
- **Admin → Overview is now a workspace-health page.** Four counts — members, suites
  (and the distinct connections they target), open incidents with the acknowledged
  subset, and today's runs by status over the UTC day — above a **needs-attention**
  feed and a **workspace-health** checklist covering the audit chain, the scheduler
  heartbeat and queue depth, the orphan-secret sweep (with a report-only **Run sweep**),
  and orchestration polling. Every row links to the thing that fixes it. A signal that
  could not be read, or that has genuinely observed nothing — a connection never polled,
  a heartbeat that has never ticked, an unreachable broker, a sweep that has never run —
  renders as **unknown** or **not monitored** with the reason, never as a zero, a green
  tick, or a missing row. See the [admin control centre guide](../guides/admin.md).

- **Four admin capabilities that had no UI now have one.** On **Admin → Compliance**:
  audit-chain verification behind an explicit **Verify now** (it reads the whole hashed
  set, so it never runs on page load) reporting intact / broken-at-an-event / nothing-to-
  verify / not-verified as four distinct answers, plus the legacy-row count and whether an
  external anchor exists; and the **data-subject rights** tools — GDPR Art 15/20 export
  and Art 17 (CCPA delete) erasure over the samples DataQ has captured, with erasure gated
  on retyping the subject value exactly and both actions producing an on-screen receipt.
  On **Admin → Settings**, the email pre-flight result now stays on the card with the
  failing transport stage and the request ID instead of passing by in a toast. On
  **Admin → Integrations**, each webhook row states its auth mode, so it is obvious which
  URLs are themselves credentials. See the
  [admin control centre guide](../guides/admin.md) and the
  [data-subject-rights runbook](../security/compliance/data-subject-rights-runbook.md).
