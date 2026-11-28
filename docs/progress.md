# DataQ — Progress tracker (v1.4 cycle)

> The **live task tracker**, active since `v1.3.0` (2026-11-28). The completed cycle
> ledgers are archived frozen at [progress-v1.md](progress-v1.md),
> [progress-v1.1.md](progress-v1.1.md), [progress-v1.2.md](progress-v1.2.md) and
> [progress-v1.3.md](progress-v1.3.md); companions: [retro-v1.md](retro-v1.md),
> [retro-v1.1.md](retro-v1.1.md), [retro-v1.2.md](retro-v1.2.md), [retro-v1.3.md](retro-v1.3.md).
> **Updated at the end of every PR** — the PR template has a checkbox to enforce.
> Source of truth for "what's done vs. what's left" in the current cycle. CLAUDE.md §13
> carries only the headline.

## Status legend

| Symbol | Meaning |
|---|---|
| ✅ | Done — PR merged to `main` |
| 🟡 | In progress — open PR or partially shipped |
| ⬜ | Not started |
| 🔵 | Deferred / scope-changed (with note) |
| ➡️ | Rolled to another milestone, with the reason |

---

## Snapshot

| | |
|---|---|
| **v1 baseline** | `v1.0.0` tagged 2026-07-04; ledger at [progress-v1.md](progress-v1.md) |
| **v1.1 baseline** | `v1.1.0` — 2026-07-04 → 2026-08-21; ledger at [progress-v1.1.md](progress-v1.1.md) |
| **v1.2 baseline** | `v1.2.0` — 2026-08-22 → 2026-10-04; ledger at [progress-v1.2.md](progress-v1.2.md), retro at [retro-v1.2.md](retro-v1.2.md) |
| **v1.3 baseline** | `v1.3.0` — 2026-10-05 → 2026-11-28, no net-new features (one exception, ADR 0048): ISSUESCLOSED issues closed across eight weekly milestones, ADRs 0048–0053, datasources 11 → 13, MCP tools 54 → 59; ledger at [progress-v1.3.md](progress-v1.3.md), retro at [retro-v1.3.md](retro-v1.3.md) |
| **Current cycle** | **v1.4 — not yet planned.** Inputs: the v1.3 retro, the open backlog, and [context/roadmap-v1.2-v1.3.md](../context/roadmap-v1.2-v1.3.md). |
| **Environment** | No live cloud environment (estate retired 2026-10-03). Work is verified on the local stacks; live verification against Snowflake, Unity Catalog, Azure or AWS waits on [#2224](https://github.com/TheurgicDuke771/DataQ/issues/2224). |
| **Open issues** | **OPENCOUNT** open repo-wide (2026-11-28, live re-count at the v1.3 close), all in `v1.3 Backlog`. |
| **Open PRs** | **1 open** (2026-11-28): the v1.3 close-out. |

---

## Carried over from v1.3

Named so nothing rolls silently (see the retro's "What rolls"):

- **Waiting on a cloud instance ([#2224](https://github.com/TheurgicDuke771/DataQ/issues/2224)):** [#2275](https://github.com/TheurgicDuke771/DataQ/issues/2275) SQLAlchemy 2.1 via snowflake-connector 5 · [#2575](https://github.com/TheurgicDuke771/DataQ/issues/2575) UC on connector 4.8 + thrift 0.25 · [#505](https://github.com/TheurgicDuke771/DataQ/issues/505) AWS + GCP deploy IaC · [#2309](https://github.com/TheurgicDuke771/DataQ/issues/2309) keyless auth.
- **Waiting on a person or a third party:** [#2520](https://github.com/TheurgicDuke771/DataQ/issues/2520) manual screen-reader pass · [#1708](https://github.com/TheurgicDuke771/DataQ/issues/1708) external pen test · [#732](https://github.com/TheurgicDuke771/DataQ/issues/732) listing decisions or a hosted offer.
- **Decided, scheduled for v1.4:** [#2523](https://github.com/TheurgicDuke771/DataQ/issues/2523) conda → uv (revisit) · [#2804](https://github.com/TheurgicDuke771/DataQ/issues/2804) croniter 7 (audit stored schedules first) · [#2312](https://github.com/TheurgicDuke771/DataQ/issues/2312) Babelfish stand-in.
- **Process, from the retro:** keep a weekly milestone to its planned rows (Weeks 6 and 7 overran again); update this file when an item closes, not at the week's end; group the frontend Dependabot bumps.
- Everything else open at the close is the `v1.3 Backlog` milestone, to be re-homed by the v1.4 plan.

---

## How to update this file

When merging a PR:

1. Find the task(s) it implements in the relevant week.
2. Flip `⬜` → `✅` (or `⬜` → `🟡` if partial).
3. Append the PR link: `— [PR #N](https://github.com/.../pull/N)`.
4. Update the **Snapshot** table (open PRs/issues).
5. If the PR closes a carried-over item, strike it through with the closing ref.
6. If the PR added out-of-scope work, add a row with a note (same honesty rule as v1).

PR-template checkbox enforces this. If the change is purely tooling / docs that doesn't map
to a tracked task, tick the "N/A" checkbox.
