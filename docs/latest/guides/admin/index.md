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

## Members

The Members tab manages two different things, and it helps to keep them apart:

- **Workspace membership** — *who is allowed into this workspace at all.*
- **Roles and access grants** — *what someone who is already in can do.*

### Adding a member

**Add member** admits an email address. It does **not** create an account anywhere. If
your deployment signs people in through an identity provider, that account is still a
prerequisite: create it there first, or the person will have nothing to sign in with. The
add dialog says so in those modes.

You can also set an **initial role** at the same time, so somebody arrives as a Viewer or
an Admin instead of the default. That role is applied **once**, when their user record is
first created at their first sign-in. After that the role editor in the members table is
authoritative, and signing in again never overwrites a role you changed in the app.

A member who has been added but has never signed in shows as **pending first sign-in**.
That is a normal state, not a failure.

### The enforcement switch

Membership enforcement is **off** while the member list is empty, and turning it on is an
explicit act: **adding the first member turns it on.** Until then, who may sign in is
decided entirely by your deployment's allowlist settings, exactly as it was before this
feature existed. An empty members list therefore means *enforcement is off*, not *nobody
has access* — the page says which.

Once the list is non-empty, a person may sign in if they are **either** on the member list
**or** named by the deployment allowlist. Those settings are grant-only: they can admit
somebody the list does not name, and they can never remove somebody the list does. The
practical consequence is that **removing an address your deployment config lists still
takes a config edit and a restart** — so seed the minimum there and add the rest in the
app.

### Review imported members

Turning enforcement on can never evict somebody who is signed in right now. The first add
therefore admits **every existing user** in the same transaction, and the dialog states the
count before you commit to it.

Those rows are marked as imported and gathered under a **review imported members** banner,
because a user record proves somebody signed in once — not that they still belong here.
Nothing deletes user records, so the import re-admits everyone who ever signed in,
including people who left. Work through the banner and **Confirm** or **Remove** each row.
Confirming grants nothing new; it records that an admin looked at the row and meant to keep
it.

### Removing a member

Removal takes effect on that person's **next request**, whatever credential they are
holding:

- their browser session stops resolving, even though it has not expired;
- **every API key they own stops working**, without anyone revoking a token;
- a sign-in attempt is refused rather than re-provisioning them.

Two guards apply. You cannot remove the **last Admin** — promote somebody else first, or
the workspace would have nobody who can manage it. And removing **your own** membership
asks for an extra confirmation, because it signs you out and only another admin can add you
back.

### Offboarding

Removing a membership is one step of a departure. **Offboard** on a member's row does the
whole thing in one pass, in a fixed order, as a single transaction: either every step lands
or none of them do.

1. **Guards first.** The pass is refused outright for the last stored-role Admin, and the
   member's own email address has to be typed to confirm.
2. **Their suites change owner.** Every suite they own goes to the person you name. The
   departing member keeps no access to those suites — the opposite of the standalone
   transfer, where an owner handing something over usually stays involved. A workspace
   Viewer cannot inherit, because a Viewer cannot own a suite; change their role first.
3. **Their credentials stop working.** Every unexpired API key and every live browser
   session is revoked.
4. **Their membership is withdrawn**, which is what makes a fresh sign-in fail as well.

The preview you see before confirming is read-only and reserves nothing: it lists the suites
with their check and run counts, how many live keys and sessions there are, and whether
membership can be withdrawn at all.

**What is kept.** Offboarding is not erasure. The account row survives, and so does
everything their name is on — the connections they created, the check versions they edited,
and the runs and results underneath the suites they handed over. Erasing a person's data is
a separate, deliberate act on the Compliance page.

**The env-listed caveat.** Membership is the union of the member list and the deployment's
own allowlists, so an address named in `OIDC_ALLOWED_EMAILS`, `OIDC_ALLOWED_DOMAINS`,
`AUTH_OTP_ALLOWED_EMAILS`, `AUTH_OTP_ALLOWED_DOMAINS` or `WORKSPACE_ADMIN_EMAILS` still signs
in after their row is gone. Rather than delete a row and report a withdrawal that did not
happen, the pass **skips that step and names the variable** you have to edit. The same
applies when there is no membership row at all.

The closing receipt is the record: what was transferred, how many keys and sessions were
revoked, whether membership was withdrawn, and every step that did not run with its reason.
Each step also writes its own audit event, alongside one for the pass as a whole.

### Seed and emergency access

Two deployment settings stay available and are deliberately grant-only, as break-glass:

| Setting | What it does |
|---|---|
| `OIDC_ALLOWED_EMAILS` / `OIDC_ALLOWED_DOMAINS` | Admits addresses on an identity-provider deployment, whether or not the member list names them. |
| `AUTH_OTP_ALLOWED_EMAILS` / `AUTH_OTP_ALLOWED_DOMAINS` | The same for email-code sign-in. Still required at boot in that mode, since the first admin has to come from somewhere. |
| `WORKSPACE_ADMIN_EMAILS` | Grants the Admin role regardless of the stored one (see roles, below). |

If a membership change locks the workspace out, add the address to the matching setting and
restart. That path exists precisely so a mistake in the app is recoverable outside it.

Local and evaluation stacks that run without any identity provider are exempt from
membership enforcement entirely, so adding a member there cannot lock you out of your own
laptop.

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

## Settings — privacy & failing samples

**Zero-sample mode** stops failing-row samples from being stored at all: results, dry-runs,
incident evidence and alerts carry aggregates and metric values only. The toggle takes
effect on the next run — nothing has to restart. If the deployment pins the mode on in its
environment the toggle is shown pinned and can only be turned *on* from here, never off;
that is deliberate, so an operator's floor cannot be undone by a click. Every change is
audited with who made it and when. Samples stored before the switch are not deleted by it;
the retention sweep removes them on its schedule.

## Integrations — webhook auth

Each inbound webhook row states the auth mode it uses, because that determines how the URL
must be handled. The Azure Data Factory URL carries a **shared secret in the query string**
— Azure Monitor supports no other mode — so the URL *is* a credential and is masked behind
a reveal toggle. Airflow and dbt use an **HMAC signature header** instead, with the signing
key held in the secret store, so their URLs carry no secret.

### Regenerating a webhook secret

**Regenerate secret** (ADF) or **Regenerate key** (Airflow, dbt) mints a new value and shows
it **once** — no page or endpoint returns it again, so copy it before closing the dialog. The
previous value keeps working for a short grace window (15 minutes by default) so you can
update the provider side without a gap; after that, callbacks using the old value are
rejected. DataQ cannot see whether the provider side was updated, so the dialog states the
deadline rather than a confirmation. Each regeneration is audited with the provider and the
grace deadline, never the value.

### Polling health

The 10-minute poll is the fallback for a provider whose webhook is not firing. The table
shows each orchestration connection's last poll, status and next expected poll. *Unknown*
means the connection has never been polled; *stalled* means the last successful poll is
older than the cadence allows; *failing* means the last poll raised, with the classified
reason beside it. **Poll all now** queues an immediate sweep for every provider; the table
refreshes when the sweep has run.

### Warehouse inventory sync

A synced connection lists every table the warehouse has, so a table nobody monitors is
visible as unmonitored rather than absent. The toggle turns sync on or off per connection
(it goes through the same path as editing the connection, so it is versioned and audited),
**Run now** queues one sync, and the counts stay blank until a sync has actually run —
*never synced* is not zero tables.
