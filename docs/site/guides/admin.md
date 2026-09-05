# Admin control centre

The admin area is where a **workspace Admin** sees the whole workspace and manages the
things that are shared rather than owned: membership, roles, access grants, workspace
settings, compliance evidence, and inbound integrations.

It lives at `/admin` and is split into six deep-linkable sub-pages. The tab you are on
**is** the URL, so any tab can be bookmarked, shared with another admin, or reloaded
without losing your place. `/admin` on its own lands on **Overview**.

## Page map

| Tab | URL | What it does |
|---|---|---|
| Overview | `/admin/overview` | Workspace counts — suites, members, access grants — plus a "needs attention" panel that links onward. |
| Members | `/admin/members` | Every user with their workspace role (editable in place) and every per-suite access grant. |
| Suites | `/admin/suites` | Every suite in the workspace, unscoped by sharing: owner, datasource, environment, check count, share count. |
| Settings | `/admin/settings` | Workspace facts and the sign-in method, the email pre-flight test, reusable notification channels, the LLM provider, the secret-store notice, and the danger zone. |
| Compliance | `/admin/compliance` | The audit log with its filters and retention disclosure, audit-chain verification, the data-subject-rights tools, and the deployment / data-residency posture. |
| Integrations | `/admin/integrations` | Ready-to-paste inbound webhook URLs for each configured orchestration provider, with the auth mode each one uses. |

The former standalone **Settings** page has moved here: `/settings` now redirects to
`/admin/settings`, and the sidebar carries a single **Admin** entry rather than two links
to the same area.

## Who can open it

Every admin route is gated **at the route**, not by hiding a button. A user who is not a
workspace Admin gets the Forbidden page — including on a direct deep link to a sub-page or
to `/settings`, and including after a demotion, since the role is resolved per request.
No admin data is fetched for them at all.

Admin is one of two authorization axes; see the
[features overview](features.md) for how workspace roles compose with per-suite sharing.

## What loads when

Each sub-page owns its own data. Opening **Compliance** fetches the audit log and the
posture and nothing else; the members and suites tables are not read until you open their
tab. Switching tabs is a navigation, so the page you leave stops holding data you are no
longer looking at.

## Overview and honest signals

The Overview's "needs attention" panel deliberately says **not monitored yet** where no
signal exists. Poll staleness, scheduler heartbeat, queue depth and datasource credential
health have no read API today, so an empty panel means nothing is being watched — not that
everything is healthy. Rows appear here as those signals ship.

## Members

### Roles and access grants

The **Members** table carries every user with their workspace role, editable in place. The
last stored-role Admin cannot be demoted: the workspace would have nobody able to manage
connections or membership, so the change is refused with the reason rather than accepted
and silently reverted. Admins granted only by the environment allowlist do not count
towards that guard — the allowlist is a recovery path, not the invariant.

Below it, **Access grants** lists every per-suite grant in the workspace: one row per
owner and one per share, unscoped by who owns what.

### Revoking any share

An Admin can revoke **any** per-suite share from this table, including on suites they
neither own nor have been shared. Until now that was possible only for the suite's own
owner, from the suite's sharing panel — which meant an Admin cleaning up after a departure
had to be granted access to each suite first.

The confirmation names the user, the level and the suite, because a grant row is read out
of context here. Revoking is audited with the admin-override flag and the grant that was
removed, so the trail survives the row.

An **owner** row is not a grant and has no Revoke: ownership is not something a share
carries, and it moves by transfer instead.

## Suites

### Transferring ownership

**Transfer** hands a suite to another user — the offboarding primitive. When somebody
leaves, their suites keep running, but nobody can manage sharing or delete them until an
owner exists again.

- The new owner gets full control: the suite, its checks and its history.
- The previous owner keeps an **editor** grant by default, so a handover does not lock the
  person still doing the work out of it. Clearing that checkbox removes their access
  entirely, which is what an offboarding wants.
- **Workspace viewers cannot own a suite.** Viewers are read-only everywhere, so they are
  not listed in the picker and the transfer is refused if attempted another way. Change
  their workspace role to member first.
- A previous owner who is themselves a viewer keeps **view**, not edit, for the same
  reason.

Both owners are recorded in the audit event.

### Deleting any suite

**Delete** removes any suite in the workspace. Before the confirmation appears, DataQ
counts exactly what the delete would destroy — checks, runs, results, and any schedules or
trigger bindings pointing at the suite — and states it plainly. The counts are exact, never
estimated; if they cannot be read, the confirmation says so rather than showing a number it
does not have, and the delete is still available.

Because this runs on a suite the Admin may not recognise, the confirmation requires the
suite's **name to be typed**. The delete cascades and **cannot be undone**: the run history,
results and monitor baselines go with the suite. The audit event records the counts, so
what was destroyed is still answerable afterwards.

If the goal is to stop a suite running rather than erase it, disable its schedules and
trigger bindings instead — that keeps the history.

## Compliance

### Audit chain

Every audit event is hashed over the one before it, so an edited or deleted row breaks the
chain. **Verify now** walks that chain and reports one of four answers:

| Answer | What it means |
|---|---|
| Intact | Every hashed row verified against its predecessor. |
| Broken | A mismatch was found. The card names the first blamed event, when it was recorded, and both hashes; rows after that point cannot be shown to be untampered. |
| Nothing to verify | No hashed row exists yet. This is **not** a clean bill of health — nothing was checked. |
| Not verified — the check failed | The verification itself did not complete. It says nothing about whether the chain is intact. |

Verification is never run when the page loads, and the card says so until you ask for it:
the check reads the whole hashed set into memory, which takes time on a large log.

The card also reports two things a bare pass would hide. **Not covered by the chain** counts
rows written before hashing shipped — real audit history that the chain simply does not
cover, never folded into the verified count. **External anchor** reports whether the chain
head is published outside the database: without one, the chain is only *internally*
consistent, because anyone able to rewrite the whole table could rewrite the hashes with it.

### Data-subject rights

The access/export and erasure tools answer a GDPR Article 15/20 or Article 17 (CCPA delete)
request against the sample data DataQ itself has captured. **They never touch your
warehouse**, which remains your system of record and your responsibility to act on
separately.

A subject is entered the way your warehouse identifies them — an **identifier column** and
its **value**, such as `email` and `alice@example.com`. DataQ holds no people-table, so
there is no user to pick from a list.

- **Export data** searches every suite in the workspace and returns each captured cell
  naming that pair, with the suite, check and run it came from. The result is deliberately
  **unredacted**: this is the subject's own access right, and the usual masking exists to
  protect *other* people from an unrelated viewer. The receipt is rendered on screen and
  offered as a download, so it is still readable where a browser blocks the download.
- **Erase subject** removes only the matching row or cell, from results and from stored
  incident evidence — not the surrounding sample, and not other subjects' rows. It runs
  synchronously, cannot be undone, and is therefore gated behind retyping the value exactly.
  The receipt reports matched and erased counts per store, so a partial erasure reads as a
  warning rather than as done.

An export that finds nothing means DataQ has captured nothing naming that subject. It is
not a statement about your warehouse, which was neither read nor changed.

Both actions write an audit event — `data_subject_request.export` (recorded with whether
anything was actually exposed) and `data_subject_request.erase` — visible in the audit log
on the same page. The erasure's event is written in the same transaction as the scrub, so
an applied erasure cannot go unrecorded.

## Settings — email pre-flight

**Send test email** sends a real message to *your own* address over the configured sign-in
mailer. It takes no recipient, so it cannot be used to relay mail. The outcome stays on the
card rather than passing by in a toast: a failure names the transport stage that broke —
connect, TLS, auth or send — and carries the request ID to search the server log by. A
success means the mailer accepted the message; if it then never arrives, the relay is the
next place to look, not this configuration.

## Integrations — webhook auth

Each inbound webhook row states the auth mode it uses, because that determines how the URL
must be handled. The Azure Data Factory URL carries a **shared secret in the query string**
— Azure Monitor supports no other mode — so the URL *is* a credential and is masked behind
a reveal toggle. Airflow and dbt use an **HMAC signature header** instead, with the signing
key held in the secret store, so their URLs carry no secret.
