# Changelog

Notable, user-facing changes. Dates are the release/merge date. This is a curated summary —
the per-PR history lives in the repo's commit log and pull requests.

## Unreleased

### Changed

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
