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
| Compliance | `/admin/compliance` | The audit log with its filters and retention disclosure, and the deployment / data-residency posture. |
| Integrations | `/admin/integrations` | Ready-to-paste inbound webhook URLs for each configured orchestration provider. |

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
