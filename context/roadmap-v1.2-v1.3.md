# DataQ roadmap — v1.2 (current) and v1.3 (proposed)

> Internal planning document (in `context/`, never published). Written 2026-08-29,
> mid-v1.2, from the gap review + external competitive assessment of the same date.
> **What this is:** the forward view — where v1.2 is going and what v1.3 should be —
> so cycle planning starts from a thesis, not a backlog sort. **What this is not:**
> a schedule ([docs/progress.md](../docs/progress.md) §Cycle plan is the live v1.2
> schedule; GitHub milestones are the source of truth for status). The themed
> deferred-work index remains [post-v1-roadmap.md](post-v1-roadmap.md).

## The thesis (one paragraph)

Three months in, the engineering risk is mostly retired — the seams keep proving
themselves by speed (AWS in days, the LLM track in a day, MCP 8→47) — while the
product risk is entirely intact: **no external evidence** (every "verified" is
self-referential, G-c) and **no automatic coverage** (the platform executes what a
human authors, G-a). The competition's moat is *automation plus evidence*; ours is
*architecture plus discipline*. v1.2 finishes converting discipline into features.
**v1.3 must convert architecture into automation, and put the product in front of
someone who isn't us.** Finding a real user is the standing priority above any
week's milestone.

---

## v1.2 — DQ intelligence + operability (2026-08-22 → 2026-10-16, in flight)

Live schedule: [docs/progress.md](../docs/progress.md) §Cycle plan. Epic
[#1518](https://github.com/TheurgicDuke771/DataQ/issues/1518). Shape as of 2026-08-29:

| Weeks | Theme | State |
|---|---|---|
| W1–W3 | Decision gates · expectation catalog + allowlist · `LLMProvider` seam + SQL generator | **Closed** (W3 two weeks early; LLM track live on both clouds) |
| W4 | LLM check suggestions (#1513) + RCA evidence track (#1633/#1635/#1647/#1654) + compliance G2 close-out | In flight (due 09-18) |
| W5 | Native engines — Snowflake DMF (#895, gated on the #590/#588 decisions) + **notification channels** (#1514, model confirmed: admin defines centrally, suites reference) + webhook publisher (#1662/#1663) + zero-sample privacy mode (#1676) | Planned |
| W6 | Perf/scale hardening — **the scale-proof week**: baseline + regression budget (#1393, carries the enterprise-buyer questions), Iceberg scan cap (#1328), sampled-read batches, a11y CI ratchet (#1670) | Planned |
| W7 | Feature burn-down + **admin control-centre phase 1** (#1694 routed sub-pages, #1695 parity pass — pulled in 2026-08-29), ⌘K (#1667), bulk ops (#1669), severity cues (#1671), JSON (#1677), PG adapter (#1678), run diff (#1686), checks-as-code (#1688), positioning (#1664) | Planned |
| W8 | Spikes + decisions + cycle close — **the #1660 coverage-loop spike/ADR is the v1.3 planning input**, OneLake spike (#1680), onboarding (#1668), close (#1518) | Planned |

**v1.2 exit posture wanted:** scale is *measured* (not proven-good, measured);
the admin surface has structure + parity; one adapter (PG) proves the
engine-generic pattern; the coverage-loop ADR exists so v1.3 starts building, not
designing.

---

## v1.3 — Automation + Evidence (proposed theme, ~2026-10-17 →)

The two gaps that change what DataQ *is*, and the operational work a first
external user forces. Planning input: the W8 #1660 spike + this page + whatever
the first design partner breaks.

### Track 1 — Coverage without authoring (the category bet)

The move from "tell me what quality means" to "I'll figure out what should be
monitored" — the #1 gap in both our own G-a and the external review.

- [#1660](https://github.com/TheurgicDuke771/DataQ/issues/1660) the coverage
  loop: inventory → profile → suggest → approve → monitor (build; ADR from W8).
- [#1661](https://github.com/TheurgicDuke771/DataQ/issues/1661) zero-config
  auto-baselines (volume/freshness/nulls/cardinality/schema) — unknown-unknowns.
- The scorecard metric: **% of assets continuously monitored without a human
  authoring a check**, on the dashboard, with the false-positive companion rate.
- Anomaly promotion: from opt-in check kind to per-asset system (rides #1661).
- [#1710](https://github.com/TheurgicDuke771/DataQ/issues/1710) column-level
  lineage pull — the declared enhancer slot: suggestion placement/dedup +
  classification propagation. NOT a gate for #1513/#1660 (decision + full
  reasoning on the issue, 2026-08-30); a pull, never a build (ADR 0034 —
  ACCESS_HISTORY columns / UC `system.access.column_lineage`). The #1660 W8
  spike designs the suggestion engine with this slot declared.

### Track 2 — External evidence (not a feature; the priority anyway)

- **1–2 design partners** running DataQ on workloads we didn't build. Their
  breakage generates the real v1.3 backlog. Prerequisites we control:
  [#1706](https://github.com/TheurgicDuke771/DataQ/issues/1706) 10-minute
  evaluation path · [#1668](https://github.com/TheurgicDuke771/DataQ/issues/1668)
  onboarding/empty states · [#1704](https://github.com/TheurgicDuke771/DataQ/issues/1704)
  backup/restore + tested upgrade path ·
  [#1705](https://github.com/TheurgicDuke771/DataQ/issues/1705) API
  compatibility policy · [#1707](https://github.com/TheurgicDuke771/DataQ/issues/1707)
  opt-in telemetry seam (built *before* the partner arrives, default off).
- G-c is only dischargeable this way. No amount of internal testing moves it.
- [#1829](https://github.com/TheurgicDuke771/DataQ/issues/1829) thin Python
  client SDK (`dataq-client`) generated from the OpenAPI spec + a small
  `trigger_run`/`wait_for_run` layer — the consumer side of a CI/CD gate
  without #1651's ADR. Parked here 2026-09-02; gated on #1705 (compatibility
  policy), since a published client is a compatibility promise. Guardrails on
  the issue (spec-drift contract test, status-not-counts polling, OIDC trusted
  publishing). **Rejected, recorded there:** a `dataq-core` library wheel of
  the backend (it is an application, not a library) and a wheel for the
  stdlib-only Airflow/dbt callback snippets.

### Track 3 — Operate-a-workspace (admin phases 2–3)

Epic [#1702](https://github.com/TheurgicDuke771/DataQ/issues/1702) beyond the
W7 phase-1 pull-in: [#1693](https://github.com/TheurgicDuke771/DataQ/issues/1693)
in-app membership (ADR 0043 first; IdP stays a prerequisite — user direction
2026-08-29) → [#1698](https://github.com/TheurgicDuke771/DataQ/issues/1698)
write pass → [#1699](https://github.com/TheurgicDuke771/DataQ/issues/1699)
offboarding → [#1696](https://github.com/TheurgicDuke771/DataQ/issues/1696)/
[#1697](https://github.com/TheurgicDuke771/DataQ/issues/1697) health page +
credential-health signal → [#1700](https://github.com/TheurgicDuke771/DataQ/issues/1700)/
[#1701](https://github.com/TheurgicDuke771/DataQ/issues/1701) lens + integrations.

### Explicitly gated / named non-goals (so they aren't re-litigated)

- **Adapter tail** (#1679–#1685): demand-gated behind #1678 proving the pattern,
  then a concrete prospect per adapter. BigQuery demoted P2→P3 accordingly.
- **ITSM tier 2** (bidirectional sync): demand-gated on tier 1 (#1662/#1663).
- **SCIM / IdP-group provisioning:** named non-goal until a customer with an
  enterprise IdP asks; recorded so the gap is a decision, not a blind spot.
- **SOC 2:** non-goal until real customers; trigger recorded in
  [#1708](https://github.com/TheurgicDuke771/DataQ/issues/1708) (which also
  carries the pre-commercial third-party pen test + SECURITY.md).
- **Pricing/commercial:** ADR 0031 stands (free OSS BYOL, no license revenue).

### Competitive calibration (2026-08-29, for the record)

Lagging, in order of consequence: automated coverage (defines the tier) ·
column-level lineage with code-change correlation · anomaly-model maturity ·
time-to-value · enterprise trappings. **At or ahead:** orchestration-gated
execution, MCP/agent surface, deployment flexibility, honesty-of-display —
the mid-market doesn't know this yet, which is
[#1664](https://github.com/TheurgicDuke771/DataQ/issues/1664)'s job
("Data Quality Control Plane" positioning).
