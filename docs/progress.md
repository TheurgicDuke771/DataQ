# DataQ — Progress tracker (v1.3 cycle)

> The **live task tracker**, active since `v1.2.0` (2026-10-04). The completed cycle
> ledgers are archived frozen at [progress-v1.md](progress-v1.md),
> [progress-v1.1.md](progress-v1.1.md) and [progress-v1.2.md](progress-v1.2.md);
> companions: [retro-v1.md](retro-v1.md), [retro-v1.1.md](retro-v1.1.md),
> [retro-v1.2.md](retro-v1.2.md).
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

---

## Snapshot

| | |
|---|---|
| **v1 baseline** | `v1.0.0` tagged 2026-07-04; ledger at [progress-v1.md](progress-v1.md) |
| **v1.1 baseline** | `v1.1.0` — 2026-07-04 → 2026-08-21; ledger at [progress-v1.1.md](progress-v1.1.md) |
| **v1.2 baseline** | `v1.2.0` — 2026-08-22 → 2026-10-04: 533 commits, 513 issues closed across eight weekly milestones plus 223 in the backlog, ADRs 0042–0047, datasources 5 → 11, MCP tools 46 → 54; ledger at [progress-v1.2.md](progress-v1.2.md), retro at [retro-v1.2.md](retro-v1.2.md) |
| **Current cycle** | **v1.3 — not yet planned.** Theme proposed in [context/roadmap-v1.2-v1.3.md](../context/roadmap-v1.2-v1.3.md): automation and external evidence. The weekly milestones are opened once the plan below is confirmed. |
| **Environment** | No live cloud environment (estate retired 2026-10-03). Work is verified on the local stacks; live verification against Snowflake, Unity Catalog, Azure or AWS waits on [#2224](https://github.com/TheurgicDuke771/DataQ/issues/2224). |
| **Open issues** | **76** open repo-wide (2026-10-04, live re-count at the v1.2 close): 74 in `v1.2 Backlog`, 1 in `v1.3 Backlog` (#732), and the v1.2 epic #1518, which closes with the tag. |
| **Open PRs** | **1 open** (2026-10-04): the v1.2 close-out. |

---

## Carried over from v1.2

Named so nothing rolls silently (see the retro's "What rolls"):

- **Security, filed and not fixed:** [#2348](https://github.com/TheurgicDuke771/DataQ/issues/2348) deleting a check rewrites run history · [#2383](https://github.com/TheurgicDuke771/DataQ/issues/2383) a suggested value set outlives a later PII classification · [#2240](https://github.com/TheurgicDuke771/DataQ/issues/2240) aggregate min/max on a sensitive column · [#2260](https://github.com/TheurgicDuke771/DataQ/issues/2260) oauthlib advisory (server-side only).
- **Waiting on something outside the repo:** [#2224](https://github.com/TheurgicDuke771/DataQ/issues/2224) cloud live verification (a test instance) · [#717](https://github.com/TheurgicDuke771/DataQ/issues/717) Iceberg v3 (trigger-gated) · [#732](https://github.com/TheurgicDuke771/DataQ/issues/732) listing readiness (decisions, or a hosted offer).
- **Process, from the retro:** changelog fragments (one file per PR) to end the serial merge conflicts; keep a weekly milestone to its planned rows.
- The other open items are the `v1.2 Backlog` milestone, to be re-homed by the plan below.

---

## Cycle plan — v1.3 (proposed, not confirmed)

> A proposal for the maintainer to accept or change. No weekly milestone exists yet and no
> issue has been moved.

| Week | Due | Theme | Candidate issues |
|---|---|---|---|
| W1 | 2026-10-23 | Correctness and security carry-overs | #2348 #2383 #2240 #2111 #2260 #2374 #2243 #2133 #2068 #1824 #1840 |
| W2 | 2026-10-30 | Coverage without authoring | #1661 #2369 #1674 #1675 |
| W3 | 2026-11-06 | What a first outside user needs | #1704 #2387 #1707 #2253 #1664, changelog fragments |
| W4 | 2026-11-13 | MCP and API polish | #2353 #2360 #2367 #1838 #2356 #2354 #1613 #2255 |
| W5 | 2026-11-20 | Performance | #1999 #2117 #2012 #2010 #1987 #1979 #1597 #2235 |
| W6 | 2026-11-27 | Accessibility and UX | #1672 #1673 #2067 #2064 #2061 #2051 #1516 #1667 #1687 #1686 |
| W7 | 2026-12-04 | Test, CI and dependency debt | #2358 #2136 #2135 #2115 #2124 #2312 #1605 #1610 #1740 #2070 #2275 |
| W8 | 2026-12-11 | Decisions and cycle close | #732 #1708 #2224 #717 #505 #2388 #1681 #1683 #2309 #2310 #2311 #1977 #1689 #1690 #888 #685 #244 #1762 — each decided or rolled by name |

---

## How to update this file

When merging a PR:

1. Find the task(s) it implements in the relevant week above.
2. Flip `⬜` → `✅` (or `⬜` → `🟡` if partial).
3. Append the PR link: `— [PR #N](https://github.com/.../pull/N)`.
4. Update the **Snapshot** table (open PRs/issues).
5. If the PR closes a carried-over item, strike it through with the closing ref.
6. If the PR added out-of-scope work, add a row with a note (same honesty rule as v1).

PR-template checkbox enforces this. If the change is purely tooling / docs that doesn't map
to a tracked task, tick the "N/A" checkbox.
