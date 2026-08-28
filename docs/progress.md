# DataQ — Progress tracker (v1.2 cycle)

> The **live task tracker**, active since `v1.1.0` (re-tagged at the true cycle close,
> 2026-08-21). The completed cycle ledgers are archived frozen at
> [progress-v1.md](progress-v1.md) (v1, Weeks 1–8) and
> [progress-v1.1.md](progress-v1.1.md) (v1.1, Weeks 1–7 + the W7 stretch); companions:
> [retro-v1.md](retro-v1.md), [retro-v1.1.md](retro-v1.1.md).
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
| **v1 baseline** | `v1.0.0` tagged 2026-07-04 — 187/189 roadmap tasks; deployed to Azure Container Apps; ledger at [progress-v1.md](progress-v1.md) |
| **v1.1 baseline** | `v1.1.0` — cycle ran 2026-07-04 → 2026-08-21 (6 weeks + the W7 stretch; first tagged 2026-08-15 at `b8d8278b`, then **re-tagged 2026-08-21 at the true close** — the stretch added 121 commits / 126 merged PRs / 55 closed issues, incl. RBAC ADR 0033, the MCP 8→46 expansion + honesty pass, the security-surface audit, and the compliance G1–G6 track). Ledger at [progress-v1.1.md](progress-v1.1.md); retro at [retro-v1.1.md](retro-v1.1.md) |
| **Current cycle** | **v1.2 — 8 weeks, 2026-08-22 → 2026-10-16** (planned 2026-08-21 at the v1.1 close; epic [#1518](https://github.com/TheurgicDuke771/DataQ/issues/1518)). The arc: **W1** clears the user's infra decision gates (#590/#588) + the MCP honesty follow-ups; **W2–W4** build the DQ-intelligence track from [docs/post-v1-dq-intelligence-notes.md](post-v1-dq-intelligence-notes.md) (catalog expansion → server-side allowlist → `LLMProvider` seam → SQL-gen → suggestions) alongside compliance G2; **W5** platform-native engines (Snowflake DMF, ADR 0036) + notification channels; **W6** perf/scale hardening; **W7** feature burn-down; **W8** spikes/decisions + **cycle close** (the closing week is deliberately last). See [Cycle plan](#cycle-plan--v12-8-weeks-2026-08-22--2026-10-16) below. |
| **Open issues** | **53** open repo-wide (2026-08-28) — the compliance G1 residual closed: #1460 (tamper-evidence hash chain + `TamperAnchor` seam) via #1598, and the two parity-audit findings #1554/#1555 (audit-log + deployment-posture UI) via #1599. Only #590/#588/#1392/#1257 remain of the W1 list — the two are the user's decision gates. `v1.2 Backlog` otherwise **empty** as the default for new filings. |
| **Open PRs** | **0** (2026-08-28): #1598/#1599 merged same day. |
| **Coverage gates (CI-enforced, ≥80%)** | backend `--cov-fail-under=80` (~4,800 backend tests) · frontend all-src `lines: 80` (~1,000 tests) — every PR rides the same gates |

---

## Carried over from v1.1

Everything open at the v1.1 close, re-homed by name into the weekly milestones below
(GitHub is the source of truth for issue state; this register mirrors it so nothing
carried is lost). The two **decision gates** are the user's call, not the assistant's:
[#590](https://github.com/TheurgicDuke771/DataQ/issues/590) (Azure estate fate) and
[#588](https://github.com/TheurgicDuke771/DataQ/issues/588) (Snowflake leg retirement) —
W1 schedules the *decision*, never presumes the outcome. One item sits in `v1.2 Backlog`
rather than a week: [#1384](https://github.com/TheurgicDuke771/DataQ/issues/1384)
(CloudFront→ALB cleartext hop — blocked on buying a custom domain + ACM, not on
engineering).

**Unplanned, user-reported, shipped 2026-08-22 — the check-engine (gx/dmf) picker UI ([#1550](https://github.com/TheurgicDuke771/DataQ/issues/1550), PR [#1552](https://github.com/TheurgicDuke771/DataQ/pull/1552)).** The backend has supported per-check engine selection since #1529 (ADR 0036), but no UI ever surfaced it — every check authored through the app silently got `gx`, and Snowflake DMF was reachable only via API/MCP. Added a "Snowflake DMF" catalog category (4 types) plus an explicit Engine picker on Freshness (the one type both engines evaluate), gated to Snowflake connections. `/code-review` (4-agent pass) caught and fixed a version-history safety gap (a restore silently reinstating a different engine with no visible warning), a stale-form-value bug on an expectation-type switch, and a virtualized-Select accessibility bug; one review-suggested fix was reverted after mutation-testing proved it was dead code. [#1551](https://github.com/TheurgicDuke771/DataQ/issues/1551) filed for the remaining non-bug polish (engine not shown in the Suites list or RunDetail/RunReport).

---

## Cycle plan — v1.2 (8 weeks, 2026-08-22 → 2026-10-16)

> Planned 2026-08-21 from [retro-v1.1.md](retro-v1.1.md), the v1.1 follow-up graph,
> [context/post-v1-roadmap.md](../context/post-v1-roadmap.md), and the 2026-08-21
> post-v1-notes sweep that filed #1509–#1516 (PR
> [#1517](https://github.com/TheurgicDuke771/DataQ/pull/1517)). GitHub mirror: milestones
> **`v1.2 Week 1..8`** (due Fridays), the cycle epic
> [#1518](https://github.com/TheurgicDuke771/DataQ/issues/1518), and the **DataQ Roadmap**
> project (every scheduled issue carries the `v1.2 week` single-select + Status). The
> closing week is the **last** week (user direction at planning), and the stretch tail is
> split across W7/W8 to keep weekly loads sane.

### v1.2 W1 — Decision gates + MCP honesty burn-down (due 2026-08-28)

⚡ **#590/#588 are the user's decision gates** (~2026-08-25 revisit): keep or tear down the
Azure estate and the harness Snowflake leg. Everything else in the week is independent of
the outcome, but #1392/#1257 are cheapest while the decision is fresh — and #895 (W5)
needs the Snowflake half decided.

| Status | Task | Theme |
|---|---|---|
| ⬜ | [#590](https://github.com/TheurgicDuke771/DataQ/issues/590) Azure estate: user decision + (if teardown) G-i + `tofu destroy` + credential retirement | ops / decision gate |
| ⬜ | [#588](https://github.com/TheurgicDuke771/DataQ/issues/588) Snowflake harness leg: user decision + clean retirement procedure (rehearsed 2026-07-04) | ops / decision gate |
| ⬜ | [#1392](https://github.com/TheurgicDuke771/DataQ/issues/1392) Verify the demo environment is actually disposable (`tofu destroy` never run) | ops |
| ⬜ | [#1257](https://github.com/TheurgicDuke771/DataQ/issues/1257) Narrow `DATAQ_LOADER`'s account-wide MANAGE GRANTS to the dbt hook's need | ops / least-priv |
| ✅ | [#1442](https://github.com/TheurgicDuke771/DataQ/issues/1442) MCP: time filter on `list_runs`/`list_incidents` ("what failed today?") | MCP honesty |
| ✅ | [#1445](https://github.com/TheurgicDuke771/DataQ/issues/1445) MCP: `list_incidents` auto-resolve makes "what failed today" silently empty | MCP honesty |
| ✅ | [#1443](https://github.com/TheurgicDuke771/DataQ/issues/1443) MCP: `get_adf_pipeline_status` misnamed (covers ADF/Airflow/dbt) | MCP honesty |
| ✅ | [#1446](https://github.com/TheurgicDuke771/DataQ/issues/1446) MCP: `list_connections` vs `test_connection` error-classification contradiction | MCP honesty |
| ✅ | [#1447](https://github.com/TheurgicDuke771/DataQ/issues/1447) MCP: trim two over-long descriptions + drop unlookuppable refs | MCP honesty |
| ✅ | [#1448](https://github.com/TheurgicDuke771/DataQ/issues/1448) MCP: `create_check` under-caveated relative to the surface | MCP honesty |

**Exit gate:** #590/#588 decisions recorded by the user (and executed if teardown);
#1392/#1257 discharged consistently with them; all six MCP honesty follow-ups closed.

**Unplanned, user-directed (2026-08-22, in-week):** codebase-wide prose trim, PR
[#1536](https://github.com/TheurgicDuke771/DataQ/pull/1536) — comment/docstring density
cut to ~≤0.2 prose-to-code across backend/tests/alembic/frontend/infra (−27k lines,
680 files; `mcp/server.py`'s LLM-facing docstrings deliberately untouched). Code proven
unchanged four independent ways (docstring-stripped AST, TS token stream, YAML structural
diff, heredoc-aware line diff); the /code-review pass returned 10 findings (garbled
truncations, erased guard comments, one `argparse --help` behavior change) — all fixed
pre-merge, load-bearing security/ops constraints restored in condensed form.

### v1.2 W2 — Expectation catalog + server-side allowlist (due 2026-09-04)

The DQ-intelligence track opens ([post-v1-dq-intelligence-notes.md](post-v1-dq-intelligence-notes.md), filed as #1509–#1513 by the 2026-08-21 sweep).

| Status | Task | Theme |
|---|---|---|
| 🟡 | [#1509](https://github.com/TheurgicDuke771/DataQ/issues/1509) The 5 high-ROI GX built-ins (`mostly`, compound/cross-column, type, set/date) + the aggregate-stats kind decision — [#1608](https://github.com/TheurgicDuke771/DataQ/pull/1608) (aggregate stats decided → #1602) | intelligence |
| ⬜ | [#1510](https://github.com/TheurgicDuke771/DataQ/issues/1510) Curated expectation superset (4a) + **server-side `expectation_type` allowlist** on REST + MCP | intelligence / security |
| ⬜ | [#1505](https://github.com/TheurgicDuke771/DataQ/issues/1505) API honesty: `extra='ignore'` lets an invented knob validate cleanly and do nothing | quality |
| ⬜ | [#1320](https://github.com/TheurgicDuke771/DataQ/issues/1320) Suite-delete confirmation states its blast radius (N checks/runs/results) | ux safety |
| ⬜ | [#1326](https://github.com/TheurgicDuke771/DataQ/issues/1326) Migration-parity literal-set check: IS NULL vs IS NOT NULL | test |

**Exit gate:** the built-in groups author end-to-end in the check editor; the allowlist
refuses an unknown `expectation_type` on every write surface; #1505 decided/landed.

### v1.2 W3 — LLMProvider seam + SQL generator (due 2026-09-11)

| Status | Task | Theme |
|---|---|---|
| ⬜ | [#1511](https://github.com/TheurgicDuke771/DataQ/issues/1511) `LLMProvider` seam — admin-configured, BYO credential, default-off, worker-side, schema-only context (**new ADR**) | intelligence |
| ⬜ | [#1512](https://github.com/TheurgicDuke771/DataQ/issues/1512) LLM SQL generator: NL rule → SQL through the ADR 0019 validator + dry-run | intelligence |
| ⬜ | [#1247](https://github.com/TheurgicDuke771/DataQ/issues/1247) Couple the near-miss read/write tuple derivation in code | refactor |
| ⬜ | [#1307](https://github.com/TheurgicDuke771/DataQ/issues/1307) Lineage floor-denial message: structural, not string-equality | refactor |
| ⬜ | [#1309](https://github.com/TheurgicDuke771/DataQ/issues/1309) Lineage: ACCESS_HISTORY denial mislabeled with the GET_LINEAGE grant message | bug |

**Exit gate:** LLM features degrade gracefully to fully-working with no provider
configured; generated SQL passes the same validation a human's would; the seam ADR is
Accepted.

### v1.2 W4 — LLM check suggestions + compliance G2 (due 2026-09-18)

| Status | Task | Theme |
|---|---|---|
| ⬜ | [#1513](https://github.com/TheurgicDuke771/DataQ/issues/1513) LLM curated check suggestions — profiler-driven, catalog-constrained structured output | intelligence |
| ✅ | [#432](https://github.com/TheurgicDuke771/DataQ/issues/432) Compliance G2: data-subject-rights machinery (erase / access / portability) — design agreed with user, [PR #1586](https://github.com/TheurgicDuke771/DataQ/pull/1586) merged | compliance |
| ✅ | [#1267](https://github.com/TheurgicDuke771/DataQ/issues/1267) `unparsed_value` scalar cell has no retention sweep (adjacent to #1253) — [PR #1585](https://github.com/TheurgicDuke771/DataQ/pull/1585) merged | compliance |
| ✅ | [#1477](https://github.com/TheurgicDuke771/DataQ/issues/1477) Audit retention sweep: a zero-row run is unobservable | compliance |

**Exit gate:** suggestions can only emit what the runner can run; G2's erasure/export
levers exist with the design recorded; both audit residuals closed.

### v1.2 W5 — Native engines (DMF) + notification channels (due 2026-09-25)

| Status | Task | Theme |
|---|---|---|
| ⬜ | [#895](https://github.com/TheurgicDuke771/DataQ/issues/895) `check.engine` seam + Snowflake DMF as the first platform-native engine (ADR 0036) — **gated on the W1 Snowflake decision keeping a live warehouse** | engines |
| ⬜ | [#1514](https://github.com/TheurgicDuke771/DataQ/issues/1514) Reusable notification channels — define once, reference from many suites | alerting |
| ⬜ | [#1515](https://github.com/TheurgicDuke771/DataQ/issues/1515) Incident routing prefers the asset owner when set (falls back to suite owner) | incidents |
| ✅ | [#1460](https://github.com/TheurgicDuke771/DataQ/issues/1460) G1 residual: tamper-evidence anchor for `audit_events` — hash chain + `TamperAnchor` seam (webhook, unanchored by default), [PR #1598](https://github.com/TheurgicDuke771/DataQ/pull/1598) merged | compliance |
| ✅ | [#1554](https://github.com/TheurgicDuke771/DataQ/issues/1554) Audit log has no UI — `GET /admin/audit-events` reachable from Admin, [PR #1599](https://github.com/TheurgicDuke771/DataQ/pull/1599) merged | compliance |
| ✅ | [#1555](https://github.com/TheurgicDuke771/DataQ/issues/1555) Deployment/data-residency posture has no UI — `GET /admin/deployment` reachable from Admin, [PR #1599](https://github.com/TheurgicDuke771/DataQ/pull/1599) merged | compliance |

**Exit gate:** a DMF check runs and persists through the normal result path (or the ADR's
trigger conditions are re-recorded if the W1 decision removed Snowflake); a channel
defined once delivers for two suites; #1460 shipped a decision, not a shrug.

### v1.2 W6 — Perf/scale hardening (due 2026-10-02)

| Status | Task | Theme |
|---|---|---|
| ⬜ | [#1393](https://github.com/TheurgicDuke771/DataQ/issues/1393) Systematic scale baseline + CI perf-regression budget | perf |
| ⬜ | [#1328](https://github.com/TheurgicDuke771/DataQ/issues/1328) Iceberg runner size probe / scan cap — measured against #1393, not inherited | perf |
| ⬜ | [#1329](https://github.com/TheurgicDuke771/DataQ/issues/1329) Efficiency batch: sampled read paths | perf |
| ⬜ | [#1330](https://github.com/TheurgicDuke771/DataQ/issues/1330) Reuse/simplification batch: CSV stream construction first | refactor |
| ⬜ | [#1331](https://github.com/TheurgicDuke771/DataQ/issues/1331) Comparison-source sampling (coherent key-set, not two draws) | perf |
| ⬜ | [#1245](https://github.com/TheurgicDuke771/DataQ/issues/1245) `(created_at, id)` index for deep offset paging | perf |
| ⬜ | [#1243](https://github.com/TheurgicDuke771/DataQ/issues/1243) Batch-target preview: bound the listing + move regex off the API process | perf / safety |
| ⬜ | [#1236](https://github.com/TheurgicDuke771/DataQ/issues/1236) Lineage: durable backstop for a suspended snapshot prune | reliability |

**Exit gate:** a perf regression is *detectable* (the budget fails CI); the sampled-read
batches land measured against the new baseline.

### v1.2 W7 — Feature burn-down (due 2026-10-09)

| Status | Task | Theme |
|---|---|---|
| ⬜ | [#505](https://github.com/TheurgicDuke771/DataQ/issues/505) GCP deploy IaC (AWS shipped 2026-08-15 — GCP is the remainder) | deploy |
| ⬜ | [#466](https://github.com/TheurgicDuke771/DataQ/issues/466) Interactive datasource browsing (ADLS/S3 container browser + UC catalog picker) | ux |
| ⬜ | [#244](https://github.com/TheurgicDuke771/DataQ/issues/244) Suite-on-suite triggering | orchestration |
| ⬜ | [#888](https://github.com/TheurgicDuke771/DataQ/issues/888) Tagging for suites/assets/connections/checks | platform |
| ⬜ | [#1516](https://github.com/TheurgicDuke771/DataQ/issues/1516) Profile / Workspace-Settings IA pickup | ux |
| ⬜ | [#685](https://github.com/TheurgicDuke771/DataQ/issues/685) Purge/redact path for connection version history | security |
| ⬜ | [#682](https://github.com/TheurgicDuke771/DataQ/issues/682) Webhook-auth metadata onto the `OrchestrationProvider` seam | refactor |
| ⬜ | [#1314](https://github.com/TheurgicDuke771/DataQ/issues/1314) Alerting: already-logged-traceback downgrade leaks through log filtering | reliability |
| ⬜ | [#1274](https://github.com/TheurgicDuke771/DataQ/issues/1274) Iceberg over S3-compatible storage: pyarrow ACCESS_DENIED + Test Connection blind | bug |
| ⬜ | [#1385](https://github.com/TheurgicDuke771/DataQ/issues/1385) AWS: ElastiCache at-rest encryption + restrict subnet egress | security |

**Exit gate:** each closed or rolled by name with a rationale comment.

### v1.2 W8 — Spikes, decisions & cycle close (due 2026-10-16)

| Status | Task | Theme |
|---|---|---|
| ⬜ | [#717](https://github.com/TheurgicDuke771/DataQ/issues/717) Iceberg v3 revisit (deletion vectors, row lineage) behind a capability gate | spike |
| ⬜ | [#732](https://github.com/TheurgicDuke771/DataQ/issues/732) Marketplace-listing readiness checklist | docs |
| ⬜ | [#1239](https://github.com/TheurgicDuke771/DataQ/issues/1239) OTP uniform-response anti-enumeration tradeoff — decide | decision |
| ⬜ | [#980](https://github.com/TheurgicDuke771/DataQ/issues/980) Redis 8 gate: Vector Sets module stays unloaded | watch |
| ⬜ | [#970](https://github.com/TheurgicDuke771/DataQ/issues/970) OTel stack → 1.44 when the Azure exporter supports it | watch |
| ⬜ | [#1327](https://github.com/TheurgicDuke771/DataQ/issues/1327) Profiler: `sqlalchemy.values()` for the rank driver — consider | spike |
| ⬜ | [#1334](https://github.com/TheurgicDuke771/DataQ/issues/1334) Check ordinal (same-transaction inserts tie on `created_at`) | quality |
| ⬜ | [#1518](https://github.com/TheurgicDuke771/DataQ/issues/1518) **Cycle close:** every remaining item closed or rolled by name; zero open PRs; retro-v1.2; freeze this file; tag + release `v1.2.0` | close |

**Exit gate:** the close checklist in #1518 — retro written, ledger frozen, `v1.2.0`
tagged + released, epic closed.

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
