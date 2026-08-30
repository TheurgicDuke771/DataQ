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
| **Open issues** | **109** open repo-wide (2026-08-30, after #1513 closed via [PR #1719](https://github.com/TheurgicDuke771/DataQ/pull/1719) — three review-driven follow-ups filed, #1717/#1720/#1721 — and #1644 closed via [PR #1716](https://github.com/TheurgicDuke771/DataQ/pull/1716)). Prior snapshot (2026-08-29 night) — the gap-review follow-through: forward roadmap page added at [context/roadmap-v1.2-v1.3.md](../context/roadmap-v1.2-v1.3.md) (v1.3 proposed theme: **Automation + Evidence**), story ledger started at [docs/stories.md](stories.md); five accepted never-asked items filed ([#1704](https://github.com/TheurgicDuke771/DataQ/issues/1704) backup/upgrade story, [#1705](https://github.com/TheurgicDuke771/DataQ/issues/1705) API compatibility policy, [#1706](https://github.com/TheurgicDuke771/DataQ/issues/1706) 10-minute eval path, [#1707](https://github.com/TheurgicDuke771/DataQ/issues/1707) opt-in telemetry seam, [#1708](https://github.com/TheurgicDuke771/DataQ/issues/1708) external security review); #1694/#1695 pulled into W7; adapter tail #1679–#1685 demand-gated behind #1678 (#1681 P2→P3). Earlier: the **admin control-centre track** is filed: epic [#1702](https://github.com/TheurgicDuke771/DataQ/issues/1702) (six routed sub-pages; mockup published 2026-08-29) over [#1693](https://github.com/TheurgicDuke771/DataQ/issues/1693) in-app membership (ADR 0043 to write; IdP stays a prerequisite — user direction), [#1694](https://github.com/TheurgicDuke771/DataQ/issues/1694) IA restructure, [#1695](https://github.com/TheurgicDuke771/DataQ/issues/1695) parity pass (4 backend-only admin endpoints), [#1696](https://github.com/TheurgicDuke771/DataQ/issues/1696) Overview health page, [#1697](https://github.com/TheurgicDuke771/DataQ/issues/1697) credential-health re-file of #954, [#1698](https://github.com/TheurgicDuke771/DataQ/issues/1698) write pass (supersedes #412), [#1699](https://github.com/TheurgicDuke771/DataQ/issues/1699) offboarding, [#1700](https://github.com/TheurgicDuke771/DataQ/issues/1700) admin lens (supersedes #411), [#1701](https://github.com/TheurgicDuke771/DataQ/issues/1701) Integrations; the [#1514](https://github.com/TheurgicDuke771/DataQ/issues/1514) channel model is user-confirmed (admin defines centrally, suites reference from a dropdown). Also includes the roadmap design-only sweep (user-directed): every remaining "_(no issue yet)_" marker in [context/post-v1-roadmap.md](../context/post-v1-roadmap.md) is now a filed issue — [#1667](https://github.com/TheurgicDuke771/DataQ/issues/1667)–[#1690](https://github.com/TheurgicDuke771/DataQ/issues/1690) (Theme 3 UI ×3, accessibility ×4, Theme 4 privacy pack ×3, Theme 8 datasource adapters ×9, Theme 9 results ×2, Theme 14 ecosystem ×3) — distributed 2026-08-29 by user direction: W5 +#1662/#1663/#1676, W6 +#1670, W7 +#1667/#1669/#1671/#1677/#1678/#1686/#1688/#1664, W8 +#1660/#1680/#1668 (spikes/onboarding), the other 12 stay `v1.2 Backlog`; verified-shipped rows marked instead of filed (asset view #760/#772+#782, OTLP endpoint #589, Vault→ADR 0039, RCA layers #1633/#1634/#1654); ITSM tier 2 deliberately unfiled (demand-gated on #1663). Also includes the issue-filing pass from the 2026-08-29 external competitive review (PR [#1665](https://github.com/TheurgicDuke771/DataQ/pull/1665)): [#1660](https://github.com/TheurgicDuke771/DataQ/issues/1660) automated coverage loop (the G-a remainder + the coverage metric), [#1661](https://github.com/TheurgicDuke771/DataQ/issues/1661) zero-config auto-baselines (unknown-unknowns), [#1662](https://github.com/TheurgicDuke771/DataQ/issues/1662)/[#1663](https://github.com/TheurgicDuke771/DataQ/issues/1663) the Theme-5 webhook publisher + PagerDuty/Opsgenie/ServiceNow/Jira payload templates (formerly "no issue yet" roadmap rows), [#1664](https://github.com/TheurgicDuke771/DataQ/issues/1664) control-plane positioning copy; scale-benchmark buyer questions added to [#1393](https://github.com/TheurgicDuke771/DataQ/issues/1393) as a comment. Earlier the same day: W3's core LLM track is DONE (#1511 + #1512 closed; admin UI #1641 merged). The competitive survey (internal doc, PR [#1646](https://github.com/TheurgicDuke771/DataQ/pull/1646)) drove a user-directed W4 feature set: [#1647](https://github.com/TheurgicDuke771/DataQ/issues/1647) alert-that-explains-itself, [#1648](https://github.com/TheurgicDuke771/DataQ/issues/1648) cadence-aware suggestions, [#1649](https://github.com/TheurgicDuke771/DataQ/issues/1649) multi-table SQL-gen, [#1650](https://github.com/TheurgicDuke771/DataQ/issues/1650) publish the MCP honesty discipline, plus #1635 promoted to W4 and #1644 (reaper). [#1651](https://github.com/TheurgicDuke771/DataQ/issues/1651) (opt-in DQ gate / circuit-breaker) filed **P3/Backlog by user direction — additive only, ADR required, existing orchestration contract unchanged**. Still open from W1: #590/#588 (user decision gates) + #1392/#1257. |
| **W2 close + deploy** | Milestone closed 2026-08-28 (22 closed / 0 open). **Both clouds deployed `4e13ecb1`** — per-service SHAs verified, migrate jobs green (delta incl. `5656bbfc1495`). The post-deploy WRITE probe (not the smoke) found [#1621](https://github.com/TheurgicDuke771/DataQ/issues/1621): the #1460 hash chain's seal UPDATE vs G1's append-only REVOKE 500ed every audited mutation — the known superuser-privilege-blindness class, on the chain's first deploy. Mitigated live on both DBs (column-scoped `GRANT UPDATE (prev_hash, row_hash)`), durable migration + privilege tests in [#1622](https://github.com/TheurgicDuke771/DataQ/pull/1622); Azure re-verified (suite create 200, #1607 gate 422s live, real SF run 2/2 on the new revision). **Standing rule: post-deploy verification includes an authenticated mutating request.** |
| **Open PRs** | **0** (2026-08-29 eve). **W3 milestone CLOSED at 19/19** — all six scheduled items plus the in-week #1634/#1631; #1309 closed via #1638's review fixups. **BOTH CLOUDS DEPLOYED `41f20587`** (2026-08-29: Azure [run 33277875436](https://github.com/TheurgicDuke771/DataQ/actions/runs/33277875436) + AWS [run 33277876641](https://github.com/TheurgicDuke771/DataQ/actions/runs/33277876641), both green) — the #1624 pushdown batch + the whole LLM track are LIVE. Verified per the standing battery: per-service image SHA on all six services (3× ACA, 3× ECS — worker genuinely running, the #1361 check), Azure migrate job Succeeded (`dd652ae1ef85` applied), full public smoke on both (healthz/SPA/deep-link 200, `/api`+`/mcp`+`/admin/llm`+`/llm/sql_generation` all 401-gated, 6/6 security headers, `/docs` serves the SPA shell), **authenticated WRITE probe 200 on Azure** (audited PATCH `/me` — the #1621 class), `/admin/llm` correctly 403s a non-admin, beat heartbeats clean on both workers. AWS-side authenticated write not exercised (no non-interactive Cognito bearer; prior-practice limitation, noted not hidden). |
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

> Forward view (v1.2 thesis + the proposed v1.3 **Automation + Evidence** cycle):
> [context/roadmap-v1.2-v1.3.md](../context/roadmap-v1.2-v1.3.md).

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
| ✅ | [#1509](https://github.com/TheurgicDuke771/DataQ/issues/1509) The 5 high-ROI GX built-ins (`mostly`, compound/cross-column, type, set/date) + the aggregate-stats kind decision — PR [#1608](https://github.com/TheurgicDuke771/DataQ/pull/1608): 7 new catalog types + `mostly` (floored 0.01 — `mostly=0` is a silent green), aggregates decided onto the monitor-kind path ([#1602](https://github.com/TheurgicDuke771/DataQ/issues/1602)); strftime is dataframe-only (gated on all three doors), set relations have inert thresholds ([#1607](https://github.com/TheurgicDuke771/DataQ/issues/1607)); cross-column types live-verified on Snowflake 2026-08-28 (found + fixed the compound-unique casing bug [#1616](https://github.com/TheurgicDuke771/DataQ/issues/1616) → [#1617](https://github.com/TheurgicDuke771/DataQ/pull/1617)) | intelligence |
| ✅ | [#1510](https://github.com/TheurgicDuke771/DataQ/issues/1510) Curated expectation superset (4a) + **server-side `expectation_type` allowlist** on REST + MCP — PR [#1614](https://github.com/TheurgicDuke771/DataQ/pull/1614): `datasources/expectation_allowlist.py` (25 vetted types, `dataframe_only` capability records) enforced on all FOUR author-time doors incl. the previously-ungated **dry-run preview**; refusal distinguishes "not a GX expectation" from "recognised, not enabled" and MCP forwards `detail.supported`; review fix: a stored legacy type stays editable/restorable (`permitted_stored_type`, mutation-verified) — only *changing* to an unvetted type refuses; export→import of legacy types is the one recorded exception; 10 SQL-capable additions share #1608's pre-deploy live-verification requirement (#953) | intelligence / security |
| ✅ | [#1505](https://github.com/TheurgicDuke771/DataQ/issues/1505) API honesty: `extra='ignore'` lets an invented knob validate cleanly and do nothing — PR [#1609](https://github.com/TheurgicDuke771/DataQ/pull/1609): `ApiRequestModel` (`extra='forbid'`) on 32 request models across 14 routers, export/import document models split (drift guard filed [#1610](https://github.com/TheurgicDuke771/DataQ/issues/1610)); ⚠️ breaking for clients sending unknown fields (CHANGELOG noted) | quality |
| ✅ | [#1320](https://github.com/TheurgicDuke771/DataQ/issues/1320) Suite-delete confirmation states its blast radius (N checks/runs/results) — PR [#1604](https://github.com/TheurgicDuke771/DataQ/pull/1604): `GET /suites/{id}/deletion_impact` (exact counts, delete-grade authz) + the confirm dialog states counts and irreversibility, fetch-failure fallback never blocks the delete | ux safety |
| ✅ | [#1326](https://github.com/TheurgicDuke771/DataQ/issues/1326) Migration-parity literal-set check: IS NULL vs IS NOT NULL — PR [#1603](https://github.com/TheurgicDuke771/DataQ/pull/1603): `_null_polarities()` extractor diffed beside `_literals()`; review caught multi-word-cast + outer-`NOT` regex bugs pre-merge; generalized operator class filed as [#1605](https://github.com/TheurgicDuke771/DataQ/issues/1605) | test |
| ✅ | [#1551](https://github.com/TheurgicDuke771/DataQ/issues/1551) Suites check-list card: engine/dimension/threshold badges + engine on RunDetail/RunReport — PR [#1611](https://github.com/TheurgicDuke771/DataQ/pull/1611): shared `checkBadges.tsx` (engine visual map typed against `CheckEngine` with unknown-engine fallback — dqx/dataplex no longer render as gx; dimension guard keeps ADR 0038's unclassified state visible), 3-way `ENGINE_LABEL` dedup incl. the check-editor Select, `DIMENSION_LABEL` + ScorecardPanel dedup; historical engine/dimension on `ResultRead` filed as [#1613](https://github.com/TheurgicDuke771/DataQ/issues/1613) | ux |
| ✅ | [#1616](https://github.com/TheurgicDuke771/DataQ/issues/1616) *(in-week, live-verification find)* compound-unique errors on live SF with uppercase columns — PR [#1617](https://github.com/TheurgicDuke771/DataQ/pull/1617): fold via the dialect's own `normalize_name`; review's index_columns sibling-door claim **refuted by live run** (locators work as authored — fold reverted); result-casing residual filed [#1618](https://github.com/TheurgicDuke771/DataQ/issues/1618) | bug |
| ✅ | [#1607](https://github.com/TheurgicDuke771/DataQ/issues/1607) *(in-week)* set-relation thresholds can never fire — PR [#1619](https://github.com/TheurgicDuke771/DataQ/pull/1619): AC1 settled on live SF (no bandable scalar, pass AND fail); `bandable` capability + refusal on every door incl. threshold-only PATCH (mutation-verified); editor hides the block; migration `5656bbfc1495` nulls the always-inert stored values in `checks` + `check_versions` (proven up+down on real Postgres); CheckEdit now surfaces unmatched-field 422s instead of silently no-opping | quality |

**Exit gate:** the built-in groups author end-to-end in the check editor; the allowlist
refuses an unknown `expectation_type` on every write surface; #1505 decided/landed.

### v1.2 W3 — LLMProvider seam + SQL generator — **CLOSED 2026-08-29, 6/6 + two in-week additions, ~2 weeks early** (was due 2026-09-11)

| Status | Task | Theme |
|---|---|---|
| ✅ | [#1511](https://github.com/TheurgicDuke771/DataQ/issues/1511) `LLMProvider` seam — **ADR [0042](site/adr/0042-llm-provider-seam.md) Accepted** (PR [#1636](https://github.com/TheurgicDuke771/DataQ/pull/1636)) + backend (PR [#1640](https://github.com/TheurgicDuke771/DataQ/pull/1640)): `backend/app/llm/` (anthropic SDK + openai_compat httpx impls; `native \| prompt_json` structured-output ladder, callers re-validate), `llm_settings` singleton + `llm_invocations` (poll + audit/cost record) behind `require_workspace_admin`, credential write-only with the #1403 destination-field rule, worker-side `llm_invoke` with a pending→running claim guard, new `llm` rate-limit class (+ ipall ceiling), G4 posture 3-state flip, orphan-sweep registry entry. **Live-verified against a real Ollama server both structured modes** (#953-class). `/code-review` found 10 real defects pre-merge incl. a prod-killer the fixture-built test schema hid (missing `gen_random_uuid()` default → every insert dies on a MIGRATED db) and a NUL-in-model-output path that stranded rows in `running`; three repo guards (migration-parity, sweep-registry, assert-hygiene) each caught one more. Admin UI panel: PR [#1641](https://github.com/TheurgicDuke771/DataQ/pull/1641) | intelligence |
| ✅ | [#1512](https://github.com/TheurgicDuke771/DataQ/issues/1512) LLM SQL generator — PR [#1643](https://github.com/TheurgicDuke771/DataQ/pull/1643): `services/llm_sqlgen.py` (first kind on the seam) + `POST /llm/sql_generation` (suite-edit, 202→poll) + the seam's `KIND_VALIDATORS` output gate — the model's SQL rides the ADR 0019 validator **on the already-NUL-scrubbed bytes** before storage; prompt = dialect + qualified target + column names + optional masked EGRESS profile stats (`top_n=0`, warehouse-tag floor), never sample rows. **Live-verified against Ollama both structured modes, injection string in the column list — read-only SQL out, gate holds.** The 8-angle `/code-review` returned ~20 real findings, headline classes: validate-then-mutate TOCTOU (NUL splitting `INTO` for the validator, scrub re-joining it in storage), name-only column egress with no access record on the DEFAULT path (guard-at-one-door), pre-model context failures misreported as model-output failures (new `llm_request_invalid`), a targetless suite 202-then-failing async (shared `check_generation_preconditions` at both altitudes), refuse-don't-guess on unlistable columns, dispatch-failure clobber race, CodeQL forcing the kind-registration imports to become load-bearing (`llm_kinds` + startup wiring guard). Deferred → [#1644](https://github.com/TheurgicDuke771/DataQ/issues/1644) (invocation reaper, W4) + [#1645](https://github.com/TheurgicDuke771/DataQ/issues/1645) (profiler double-login perf). Admin LLM settings panel also merged (PR [#1641](https://github.com/TheurgicDuke771/DataQ/pull/1641)) | intelligence |
| ✅ | [#1247](https://github.com/TheurgicDuke771/DataQ/issues/1247) Couple the near-miss read/write tuple derivation in code — PR [#1637](https://github.com/TheurgicDuke771/DataQ/pull/1637): one `near_miss_partner_envs()` used by both sides; review caught the matrix test passing green under over-inclusion drift (fixture had no same-env binding) — fixed + mutation-verified both directions | refactor |
| ✅ | [#1307](https://github.com/TheurgicDuke771/DataQ/issues/1307) Lineage floor-denial message: structural, not string-equality — PR [#1638](https://github.com/TheurgicDuke771/DataQ/pull/1638): `not_authorized_label` threaded at classification time; review found two MORE unlabelled sibling doors (seed enumeration misattributing to GET_LINEAGE; the no-top floor path reporting only an exception class name) — both fixed + mutation-verified | refactor |
| ✅ | [#1309](https://github.com/TheurgicDuke771/DataQ/issues/1309) Lineage: ACCESS_HISTORY denial mislabeled with the GET_LINEAGE grant message — closed by PR [#1638](https://github.com/TheurgicDuke771/DataQ/pull/1638)'s review fixups (the two remaining unlabelled not-authorized call sites got `not_authorized_label` threaded structurally) | bug |

| ✅ | [#1634](https://github.com/TheurgicDuke771/DataQ/issues/1634) *(in-week, RCA survey find)* the incident evidence card was dark in the UI — PR [#1639](https://github.com/TheurgicDuke771/DataQ/pull/1639): evidence drawer on IncidentsPanel + incident tags on RunDetail failing rows, explicit not-available states; review caught the frontend hand-copying `FAILING_TIERS` without `warn` (and a test locking the wrong behavior in) — the backend opens incidents on warn too | incidents |

| ✅ | [#1631](https://github.com/TheurgicDuke771/DataQ/issues/1631) *(in-week, filed at track start)* opt-in live-LLM lane — PR [#1656](https://github.com/TheurgicDuke771/DataQ/pull/1656): `backend/tests/e2e/test_llm_live.py`, gated on `DATAQ_LLM_LIVE=1` + base URL (8 skips by default, never a required check); proves the OpenAI-compat impl against a REAL Ollama server — usage/cost mapping, BOTH structured-ladder modes, wire error taxonomy (refused/timeout/unknown-model), and the full sql_generation worker body end-to-end per mode with an injection string in the column list. Live 8/8 twice (pre- and post-review); review fixed a strict-mode schema that would have failed against OpenAI/Azure — the lane's own portability proven before it's pointed anywhere else | intelligence |

**Exit gate:** LLM features degrade gracefully to fully-working with no provider
configured; generated SQL passes the same validation a human's would; the seam ADR is
Accepted. — **MET** (2026-08-29: default-off end-to-end — no `llm_settings` row means every LLM surface is absent and everything else works, feature endpoints 409 `llm_not_configured`; generated SQL rides the ADR 0019 validator on the exact stored bytes + the editor's dry-run path unchanged; ADR 0042 Accepted + live-verified against a real local model). **Milestone closed at 19/19 (0 open).**

### v1.2 W4 — LLM check suggestions + compliance G2 (due 2026-09-18)

| Status | Task | Theme |
|---|---|---|
| ✅ | [#1513](https://github.com/TheurgicDuke771/DataQ/issues/1513) LLM curated check suggestions — profiler-driven, catalog-constrained structured output ([PR #1719](https://github.com/TheurgicDuke771/DataQ/pull/1719)): `POST /llm/check_suggestions`, second kind on the seam; structured output constrained to a single-column-only vetted expectation vocabulary, every suggestion re-validated through the same gate a human's `create_check` reaches (bad ones dropped, not surfaced). Scope: SQL-queryable connections only, same as #1512. The review/apply UI ships next (#1512's own frontend is also still unbuilt) | intelligence |
| ✅ | [#432](https://github.com/TheurgicDuke771/DataQ/issues/432) Compliance G2: data-subject-rights machinery (erase / access / portability) — design agreed with user, [PR #1586](https://github.com/TheurgicDuke771/DataQ/pull/1586) merged | compliance |
| ✅ | [#1267](https://github.com/TheurgicDuke771/DataQ/issues/1267) `unparsed_value` scalar cell has no retention sweep (adjacent to #1253) — [PR #1585](https://github.com/TheurgicDuke771/DataQ/pull/1585) merged | compliance |
| ✅ | [#1477](https://github.com/TheurgicDuke771/DataQ/issues/1477) Audit retention sweep: a zero-row run is unobservable | compliance |
| ✅ | [#1644](https://github.com/TheurgicDuke771/DataQ/issues/1644) `llm_invocations` stuck-row reaper — [PR #1716](https://github.com/TheurgicDuke771/DataQ/pull/1716) merged: mirrors `reap_stuck_runs`, plus a symmetric guard on `execute_invocation`'s own terminal write (review found the reap-race cut both ways — a slow-not-dead worker could resurrect a row the reaper had already closed out). #1717 (missing status index) filed as a non-blocking follow-up | intelligence |
| ⬜ | [#1626](https://github.com/TheurgicDuke771/DataQ/issues/1626) MCP: expose curated docs via a `get_doc` tool | intelligence |
| ✅ | [#1632](https://github.com/TheurgicDuke771/DataQ/issues/1632) Prompt-injection adversarial battery for #1513/#1512 — extends `tests/support/adversarial.py` with a shared injection-string battery, parametrized across column/top-value/range/table/schema slots, proving each reaches the prompt as inert data; the pre-existing per-feature output-gate tests are the other half of the posture (output validation, not prompt hygiene). Plus a structural guarantee neither builder can even import the model that carries sample rows, and that a response is never logged unredacted — both mutation-verified against a deliberately reintroduced leak | intelligence |
| ⬜ | [#1633](https://github.com/TheurgicDuke771/DataQ/issues/1633) LLM root-cause narrative for failed checks — Layer 2 on the evidence card | intelligence |
| ⬜ | [#1635](https://github.com/TheurgicDuke771/DataQ/issues/1635) Evidence-card enrichment — cross-suite same-asset siblings + kind-aware payloads | incidents |
| ⬜ | [#1647](https://github.com/TheurgicDuke771/DataQ/issues/1647) The alert that explains itself — evidence summary + RCA narrative in the alert channel | alerting |
| ⬜ | [#1648](https://github.com/TheurgicDuke771/DataQ/issues/1648) Cadence-aware suggestions — bound-pipeline schedule + delay history drive thresholds | intelligence |
| ⬜ | [#1649](https://github.com/TheurgicDuke771/DataQ/issues/1649) Multi-table SQL generation — cross-table checks on one connection | intelligence |
| ⬜ | [#1650](https://github.com/TheurgicDuke771/DataQ/issues/1650) docs: publish the MCP honesty discipline | intelligence |
| ⬜ | [#1654](https://github.com/TheurgicDuke771/DataQ/issues/1654) docs: publish the evidence-card contract | incidents |

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

| ⬜ | [#1662](https://github.com/TheurgicDuke771/DataQ/issues/1662) Generic HMAC-signed outbound-webhook `ResultPublisher` (Theme 5 row, filed from the 2026-08-29 review pass) | alerting |
| ⬜ | [#1663](https://github.com/TheurgicDuke771/DataQ/issues/1663) Per-destination payload templates (PagerDuty/Opsgenie/ServiceNow/Jira) — stacked on #1662 | alerting |
| ⬜ | [#1676](https://github.com/TheurgicDuke771/DataQ/issues/1676) Zero-sample "privacy mode" — deployment-level switch, samples never persisted | compliance |
**Exit gate:** a DMF check runs and persists through the normal result path (or the ADR's
trigger conditions are re-recorded if the W1 decision removed Snowflake); a channel
defined once delivers for two suites; #1460 shipped a decision, not a shrug.

### v1.2 W6 — Perf/scale hardening (due 2026-10-02)

| Status | Task | Theme |
|---|---|---|
| ✅ | [#1624](https://github.com/TheurgicDuke771/DataQ/issues/1624) Convert the 14 eligible UC frame-batch expectation types to Databricks-SQL pushdown — shipped early, out of week order, at user request — [PR #1629](https://github.com/TheurgicDuke771/DataQ/pull/1629): all 14 joined `SQL_PUSHDOWN_EXPECTATION_TYPES`, live-Databricks-SQL-verified per type (`unexpected_percent` matched independently-computed ground truth on every check); `expect_compound_columns_to_be_unique` got a Databricks-dialect reflection-casing fold mirroring #1616's Snowflake fix, shared into `datasources/sql.py`; `/code-review` caught and fixed a real gap — the index-column clash workaround only checked the `column` kwarg, missing the new `column_A`/`column_B`/`column_list`-keyed types | perf |
| ⬜ | [#1393](https://github.com/TheurgicDuke771/DataQ/issues/1393) Systematic scale baseline + CI perf-regression budget | perf |
| ⬜ | [#1328](https://github.com/TheurgicDuke771/DataQ/issues/1328) Iceberg runner size probe / scan cap — measured against #1393, not inherited | perf |
| ⬜ | [#1329](https://github.com/TheurgicDuke771/DataQ/issues/1329) Efficiency batch: sampled read paths | perf |
| ⬜ | [#1330](https://github.com/TheurgicDuke771/DataQ/issues/1330) Reuse/simplification batch: CSV stream construction first | refactor |
| ⬜ | [#1331](https://github.com/TheurgicDuke771/DataQ/issues/1331) Comparison-source sampling (coherent key-set, not two draws) | perf |
| ⬜ | [#1245](https://github.com/TheurgicDuke771/DataQ/issues/1245) `(created_at, id)` index for deep offset paging | perf |
| ⬜ | [#1243](https://github.com/TheurgicDuke771/DataQ/issues/1243) Batch-target preview: bound the listing + move regex off the API process | perf / safety |
| ⬜ | [#1236](https://github.com/TheurgicDuke771/DataQ/issues/1236) Lineage: durable backstop for a suspended snapshot prune | reliability |

| ⬜ | [#1670](https://github.com/TheurgicDuke771/DataQ/issues/1670) a11y: axe-core CI ratchet (Playwright + vitest-axe) — the regression-stopping floor | quality |
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

| ⬜ | [#1667](https://github.com/TheurgicDuke771/DataQ/issues/1667) Global search / command palette (⌘K) | ux |
| ⬜ | [#1669](https://github.com/TheurgicDuke771/DataQ/issues/1669) Bulk operations on checks (multi-select enable/disable/severity/snooze) | ux |
| ⬜ | [#1671](https://github.com/TheurgicDuke771/DataQ/issues/1671) a11y: non-color severity cues + colorblind-safe palette | ux |
| ⬜ | [#1677](https://github.com/TheurgicDuke771/DataQ/issues/1677) JSON flat-file support (csv/parquet + json) | datasource |
| ⬜ | [#1678](https://github.com/TheurgicDuke771/DataQ/issues/1678) Generic PostgreSQL adapter — the G-f cheap first win, dogfoodable | datasource |
| ⬜ | [#1686](https://github.com/TheurgicDuke771/DataQ/issues/1686) Run comparison — diff two runs of a suite | results |
| ⬜ | [#1688](https://github.com/TheurgicDuke771/DataQ/issues/1688) Checks-as-code: dataq.yaml/JSON authoring contract + validate/apply/drift (phase 1 can ship alone) | gitops |
| ⬜ | [#1664](https://github.com/TheurgicDuke771/DataQ/issues/1664) "Data Quality Control Plane" positioning copy (marketing + docs site) | docs |
| ⬜ | [#1694](https://github.com/TheurgicDuke771/DataQ/issues/1694) Admin control centre phase 1a: routed sub-pages IA restructure (epic #1702; pulled in 2026-08-29) | admin |
| ⬜ | [#1695](https://github.com/TheurgicDuke771/DataQ/issues/1695) Admin control centre phase 1b: parity pass — DSR / SMTP test / webhooks / chain verify UI | admin |
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
| ⬜ | [#1660](https://github.com/TheurgicDuke771/DataQ/issues/1660) **Spike/ADR:** automated coverage loop — fleet-wide monitor bootstrap design (the G-a remainder) | spike |
| ⬜ | [#1680](https://github.com/TheurgicDuke771/DataQ/issues/1680) **Spike:** OneLake flat-file — ADLS adapter + endpoint override + Entra auth | spike |
| ⬜ | [#1668](https://github.com/TheurgicDuke771/DataQ/issues/1668) First-run onboarding + empty-state pass (pairs with #732 marketplace checklist) | ux |
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
