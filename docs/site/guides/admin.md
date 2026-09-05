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

## Integrations — webhook auth

Each inbound webhook row states the auth mode it uses, because that determines how the URL
must be handled. The Azure Data Factory URL carries a **shared secret in the query string**
— Azure Monitor supports no other mode — so the URL *is* a credential and is masked behind
a reveal toggle. Airflow and dbt use an **HMAC signature header** instead, with the signing
key held in the secret store, so their URLs carry no secret.
