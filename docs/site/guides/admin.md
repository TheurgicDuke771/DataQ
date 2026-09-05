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
| Overview | `/admin/overview` | Workspace counts — members, suites, open incidents, runs today — plus a "needs attention" feed and a workspace-health checklist, each row linking to the thing that needs fixing. |
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

## Overview

`/admin/overview` is the workspace's morning page: four counts, everything that currently
needs attention, and a checklist of the health signals themselves.

### The stat cards

| Card | The number | The line under it |
|---|---|---|
| Members | Every user row in the workspace. | How many were admitted but have not signed in yet — see below. |
| Suites | Every suite, unscoped by sharing. | How many **distinct connections** those suites target; a connection no suite uses is not counted. |
| Open incidents | Every unresolved incident. | How many of those have been acknowledged. Acknowledging silences nothing, so an acknowledged incident is still open and still counted. |
| Runs today | Runs created since the start of the current **UTC** day (not your local day). | Succeeded, failed and running. Queued and cancelled runs are in the total but not in those three, so they need not add up. |

**Members: "pending first sign-in is not tracked yet."** A user row is created *by* a first
successful sign-in, so someone admitted at the identity provider who has never signed in
leaves no trace in DataQ at all. The card says the number is untracked rather than showing
a `0` that would claim everybody admitted has arrived.

If a card cannot be loaded it shows an em dash and the failure, never a zero.

### Needs attention

One row per thing that is wrong or unknown, each with a verb that takes you to where it is
fixed — the connection, the suites list, or the health item further down the page.

| Row | What raised it |
|---|---|
| Polling stalled / failing | An orchestration connection is overdue for a poll, or is being polled on schedule and erroring. Pipeline completions may not be reaching DataQ. |
| Stored credential rejected | A datasource rejected the stored credential the last time real work used it. |
| Trigger env mismatch | A pipeline keeps succeeding in one environment while the only enabled binding targets another, so no suite is triggered. Only mismatches on suites you can see are listed. |
| Orphan secrets found | The last sweep found stored credentials that no connection references any more. |
| Anything marked **not monitored** | See below. |

An empty feed means nothing is wrong **among the signals listed in the health checklist**.
Anything not in that checklist is unmonitored, not clear.

### Workspace health

Four items, each with its own verb:

- **Audit chain** — *Verify now*. Never runs on page load, for the same reason as the
  Compliance card: it reads the whole hashed set. Until you ask, the chain state is
  unknown, and the item says so.
- **Scheduler & worker** — the beat heartbeat and the broker queue depths.
- **Secret store** — the last orphan-secret sweep, and *Run sweep* to start a new one. A
  manually started sweep is always **report-only**: it never deletes a credential. After
  starting it, the page re-reads the report for a short while; if the worker has not
  recorded one by then it says the run is still queued rather than showing the previous
  run's numbers as if they were the new ones.
- **Orchestration polling** — how many connections are unhealthy and how many have never
  been polled, with *Details* through to Integrations.

### What "unknown" and "not monitored" mean

These are the two answers that must never be read as good news:

- **Unknown** — the signal exists but could not be read this time. The queue depth when
  the broker is unreachable is unknown; it is **not** zero. A health request that failed
  makes poll staleness unknown; it does **not** mean everything is on cadence.
- **Not monitored** — the signal exists and has genuinely observed nothing. An
  orchestration connection that has never been polled, a scheduler that has never recorded
  a heartbeat tick, a datasource credential that no run, dry-run, profile or connection
  test has ever used, or a sweep that has never run. Nothing has been shown to work, which
  is not the same as being shown to be healthy.

Either way the row is **still shown**. A signal that cannot answer is listed with its
reason rather than dropped, so the page never answers "is anything wrong?" with silence it
has not earned.

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
