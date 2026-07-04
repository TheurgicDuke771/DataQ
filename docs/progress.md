# DataQ — Progress tracker (post-v1)

> The **live task tracker**, active since `v1.0.0` (tagged 2026-07-04). The completed v1
> ledger — the per-PR record of the 8-week roadmap, Weeks 1–8 — is **archived, frozen at the
> tag, in [progress-v1.md](progress-v1.md)** (companion: [retro-v1.md](retro-v1.md)).
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
| **v1 baseline** | `v1.0.0` tagged 2026-07-04 — 187/189 roadmap tasks (~99%); all 8 weekly exit gates met; deployed to Azure Container Apps; retro at [retro-v1.md](retro-v1.md); full ledger at [progress-v1.md](progress-v1.md) |
| **Current cycle** | **Post-v1 cycle planning — not started.** Input: [context/post-v1-roadmap.md](../context/post-v1-roadmap.md) (53 issues across 13 themes + the gap register G-a…G-i). Recommended opening sequence per that doc: Theme-1 `schema_drift` + `anomaly` monitor kinds (rides the ADR 0012 seam) → scale-aware execution (gap G-b, Theme 7) → incident/lineage design doc (G-d). |
| **Open issues** | **53** (verified against GitHub 2026-07-04), all on the `Backlog (post-v1 / testing)` milestone — mapped by theme in [post-v1-roadmap.md](../context/post-v1-roadmap.md), not duplicated here |
| **Open PRs** | none |
| **Coverage gates (CI-enforced, ≥80%)** | backend `--cov-fail-under=80` (98.4% / 1,289 tests at the tag) · frontend all-src `lines: 80` (~88% / 337 tests at the tag) — every post-v1 PR rides the same gates |

---

## Carried over from v1

Everything that was still open, pending, or deferred in the v1 ledger at the tag. GitHub is
the source of truth for issue state; this register mirrors it so nothing carried is lost.

### Deferred v1 roadmap tasks (the 2 🔵 of 189)

| Item | Where tracked |
|---|---|
| Interactive datasource browsing — ADLS/S3 container browser + UC 3-level catalog→schema→table picker (the two 🔵 scope-changed W2 rows; run/check paths shipped via explicit targets) | [#466](https://github.com/TheurgicDuke771/DataQ/issues/466) — post-v1-roadmap Theme 8 |

### Go-live follow-ups (filed 2026-07-03/04, open by choice — none blocking)

| # | Title |
|---|---|
| [#563](https://github.com/TheurgicDuke771/DataQ/issues/563) | Mutation-spike survivors triage (mutmut/Stryker spike fallout) |
| [#568](https://github.com/TheurgicDuke771/DataQ/issues/568) | Severity threshold ordering unvalidated (warn/fail/critical bands can be authored out of order) |
| [#571](https://github.com/TheurgicDuke771/DataQ/issues/571) | `checks_total` shows cosmetic 0 on pre-dispatch run failures |
| [#573](https://github.com/TheurgicDuke771/DataQ/issues/573) | Flaky `SchedulesPanel` Popconfirm test in CI |

### Long-standing follow-ups (pre-go-live filings, all on Backlog)

| # | Title |
|---|---|
| [#194](https://github.com/TheurgicDuke771/DataQ/issues/194) | Snowflake key-pair: encrypted (passphrase-protected) private keys |
| [#195](https://github.com/TheurgicDuke771/DataQ/issues/195) | Snowflake key-pair: migrate off deprecated GX `connect_args` private_key path |
| [#197](https://github.com/TheurgicDuke771/DataQ/issues/197) / [#199](https://github.com/TheurgicDuke771/DataQ/issues/199) / [#204](https://github.com/TheurgicDuke771/DataQ/issues/204) | Week-4 frontend refactor nits (shared antd-Select test helper · `useAsyncAction` toast helper · drawer/delete dedup) |
| [#327](https://github.com/TheurgicDuke771/DataQ/issues/327) | Column profiler N+1 query batching |
| [#349](https://github.com/TheurgicDuke771/DataQ/issues/349) / [#351](https://github.com/TheurgicDuke771/DataQ/issues/351) | Week-6 results/connection-page follow-ups |
| [#372](https://github.com/TheurgicDuke771/DataQ/issues/372) | `SecretStore` has no delete — webhook/connection secrets orphan on clear/delete |
| [#524](https://github.com/TheurgicDuke771/DataQ/issues/524) | opencensus → OTel log-export migration (spans done in #525; logs remain) |
| [#529](https://github.com/TheurgicDuke771/DataQ/issues/529) | MCP tool expansion (candidate tiers from #530 — Theme 13) |
| [#532](https://github.com/TheurgicDuke771/DataQ/issues/532) | Dry-run preview is Snowflake-only — extend to Unity Catalog + flat-file suites |
| [#505](https://github.com/TheurgicDuke771/DataQ/issues/505) | AWS/GCP deploy IaC (post-v1 per ADR 0028) |
| [#461](https://github.com/TheurgicDuke771/DataQ/issues/461) | DataQ-issued API keys / service tokens (ADR 0026 — deferred with shape confirmed: PATs first) |

_The rest of the 53 are mapped by theme in [post-v1-roadmap.md](../context/post-v1-roadmap.md) — that doc, plus the GitHub milestone, is the full register; this table only names the ones the v1 ledger and CLAUDE.md §13 called out individually._

### Pending design decisions

| Decision | Affects | Status |
|---|---|---|
| Two-connection model for `comparison` checks (**ADR 0015**, reserved in ADR 0014) | The reconciliation/`comparison` monitor kind, when its theme is picked up | ⬜ open, non-blocking until then |

### Standing decisions of record & guardrails (carried from the go-live close)

| Decision | Record |
|---|---|
| ADR 0026 (API keys) **deferred** to post-v1 Theme 3 — PATs-first, service principals later, HTTP Basic rejected; interim = Azure CLI pre-authorized on the API scope (#565) | [ADR 0026](adr/0026-auth-api-keys-and-principal-seam.md) |
| Databricks **Free Edition** = demo/eval only; paid workspace before any commercial use | gap **G-h**, [post-v1-roadmap.md](../context/post-v1-roadmap.md) |
| **Pre-marketplace harness teardown** — strip Flows A/B/C, the 5 harness connections, demo users, and the seeded-breach check before any marketplace/customer-facing artifact | gap **G-i**, [post-v1-roadmap.md](../context/post-v1-roadmap.md) + `deploy/README.md` |
| **Ops/renewal timers consciously skipped** — the Sept-2026 demo-credential cluster self-signals via #419 alerting; recovery = re-mint + KV update | [retro-v1.md](retro-v1.md) |
| Key Vault **purge protection left off** (demo-scoped vault) | `deploy/README.md` |

---

## Cycle plan

> ⬜ **To be generated** — post-v1 cycle planning hasn't started. When it does, the week-wise
> (or theme-wise) task breakdown gets generated from
> [context/post-v1-roadmap.md](../context/post-v1-roadmap.md) (that doc is explicitly the
> generator input) and lands here as per-cycle sections with checkboxes, mirroring how
> [progress-v1.md](progress-v1.md) tracked the v1 weeks. **Don't duplicate the backlog itself
> here** — issues stay themed in the roadmap doc until they're scheduled into a cycle.

---

## How to update this file

When merging a PR:

1. Find the task(s) it implements in the relevant cycle section (once the cycle plan exists).
2. Flip `⬜` → `✅` (or `⬜` → `🟡` if partial).
3. Append the PR link: `— [PR #N](https://github.com/.../pull/N)`.
4. Update the cycle subtotal and the **Snapshot** table (open PRs/issues).
5. If the PR closes a carried-over item above, strike it through with the closing ref.
6. If the PR added out-of-scope work, add a row with a note (same honesty rule as v1).

PR-template checkbox enforces this. If the change is purely tooling / docs that doesn't map
to a tracked task, tick the "N/A" checkbox.
