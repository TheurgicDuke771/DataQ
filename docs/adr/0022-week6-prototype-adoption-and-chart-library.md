# ADR 0022 — Week-6 prototype adoption (dedicated-page flows + full screen set) and chart library (recharts)

- **Status:** Accepted
- **Date:** 2026-06-21
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0018](0018-results-surface-and-grafana-deferral.md) (results is an in-app page, not Grafana — this ADR builds its dashboard/charts), [0005](0005-severity-tier-weights.md) / [0016](0016-severity-derivation-semantics.md) (the severity/status model the screens render), [0011](0011-extensibility-seams-for-deferred-integrations.md) (the `ResultPublisher` alerting seam Profile/Settings notification UI fronts), [0010](0010-provider-agnostic-infrastructure-seams.md) / [0013](0013-marketplace-distribution-and-anti-lock-in.md) (no Azure resource names baked into the new Settings/Secrets screen — Key Vault is one impl behind the seam). Tracked in epic [#175](https://github.com/TheurgicDuke771/DataQ/issues/175).

## Context

Week 6 ("Results dashboard + alerting") is also the week we adopt the **DataQ Design System** prototype (claude.ai/design project *"DataQ Design System"*, `projectId 317fec67-2b8a-498c-8f6b-523750916a8d`; its `readme.md` is the spec). The prototype was reverse-engineered from the shipped indigo/antd app, so the **foundation already matches** (`theme.ts` palette, `resultsFormat.ts` status model, the 56px-header/220px-sider shell) — this is **not a re-skin**. It adopts the prototype's **structure**.

Two problems pushed this to an ADR:

1. **The Week-6 task list under-scoped the work.** `progress.md` tracked 23 tasks but the prototype's `templates/app/index.html` registers **13 screens**, and three real screens fell out of the tracking: **Profile** (still a placeholder `Home` component), **Settings** (didn't exist; had been punted to Week 7 as "backend-gated"), and the **New-Connection source-select split**. The decision now is to build the **full prototype screen set in Week 6**, not a subset.
2. **The dashboard + trend charts gate on a charting library** the prototype is silent about (it fakes charts with divs). antd ships none, so this is a real dependency decision with bundle-size + `pnpm audit` consequences.

## Decision

### A. Adopt the full prototype screen set in Week 6

Every create/edit flow becomes a **dedicated, deep-linkable page**; **the Share drawer is the _only_ surviving drawer**. Where the prototype differs from the shipped app, **the prototype wins**. The 13 screens and their disposition:

| Screen | Route | Disposition |
|---|---|---|
| AppShell / nav | — | Restructure: sider → **Dashboard · Connections · Suites · Results · Profile**; footer group **Admin · Settings · Documentation**; login redirect `/` → `/dashboard` |
| **Dashboard** | `/dashboard` | **New** landing — KPI cards, Quality Trends chart, Suite Performance bars, Recent Runs table (gated on §B) |
| Connections | `/connections` | Exists — align |
| New Connection | `/connections/new` → source-select → config | Adopt the **categorized source-select** step (Orchestration first), then spec-driven config |
| Suites | `/suites` | Exists — align (two-panel) |
| **New Suite** | `/suites/new` | **Restructure** `SuiteDrawer` create → page |
| Add Check | `/suites/:id/checks/new` | Exists — align |
| **Edit flows** | `/connections/:id/edit`, `/suites/:id/edit`, `/suites/:id/checks/:checkId/edit` | **Restructure** the Connection/Suite/Check **edit drawers** → pages reusing the create page + prefill |
| Results | `/results` | Exists — add KPI cards + filter expansion |
| **Run detail** | `/results/:runId` | **Restructure** the in-page `openRun` drawer → routed page; add CSV/JSON download |
| **Profile** | `/profile` | **New** real content (identity + alert-channel toggles) — replaces the placeholder `Home` |
| Admin | `/admin` | Exists (#289) — reconcile layout to the prototype (MetricCards + Members & access + All suites) |
| **Settings** | `/settings` | **New** — General · Secrets (Key Vault) · Notifications · Danger zone. **Pulled into Week 6** (was Week 7) |
| Share | drawer | Stays a drawer (the only one) ✓ |
| **Error pages** | router catch-all + `ErrorBoundary` fallback | **New** `ErrorState`-based 400/401/403/404/429/500/502/503/504 catalog |

**Built with real Ant Design**, reading tokens from `theme.ts`. The prototype's framework-free `.jsx` recreations and `_ds_bundle.js` are **reference only** (per the design `readme.md`).

**Honesty constraints carried forward:**
- Render **only backend-backed KPIs** on the Dashboard/Results (no invented metrics — the W7 "KPI honesty pass"). Where the prototype shows a metric with no backend (e.g. "Avg. Time to Resolution"), either wire it or omit it — don't fake it.
- Failing-row **sample drill-down stays withheld** pending row-level PII redaction ([#226](https://github.com/TheurgicDuke771/DataQ/issues/226), ADR 0018).
- Settings **Secrets** tab shows Key Vault state via the generic secret seam — **no hardcoded Azure resource names** in component/business logic (ADR 0010/0013).
- Notification toggles (Profile/Settings) front the **`ResultPublisher`** seam (ADR 0011); Teams is the v1 impl.

### B. Chart library: **recharts**

Adopt **recharts** for the Dashboard KPI/trend charts and the per-check historical trend. Rationale: lightweight composable React-native SVG (~95 KB gz), MIT, clean audit history, and **lazy-loaded** so it ships only on chart-bearing routes (`/dashboard`, run/check detail) — keeping the initial bundle lean, consistent with the existing code-split routes in `App.tsx`. It clears the `pnpm audit --audit-level=high` + bundle-size merge gate.

### Explicitly NOT adopted
- **"View as" role switch** (prototype user-menu demo only) — real authz is server-driven via `/me` (#289).
- **Marketing landing page** (`templates/marketing/`) — post-v1.
- **Dark mode** — post-v1; the semantic-token contract already keeps the app dark-ready (`tokens/dark.css`), so no work now.
- The prototype `_ds_bundle.js` / framework-free component recreations — reference only.

## Consequences

**Positive**
- One honest, complete Week-6 scope: every shipped screen has a prototype-matched target, and the three under-tracked screens (Profile, Settings, connection source-select) are now first-class.
- Deep-linkable, refreshable pages for every flow (drawers were not) — better URLs, back-nav, and browser history. Only Share stays modal.
- recharts unblocks the Dashboard and the per-check trend without a heavy dependency; lazy-loading contains its bundle cost.

**Negative / watch**
- **Scope expansion**: pulling Settings + Admin-reconcile into Week 6 grows the week beyond the original roadmap. Tracked honestly in `progress.md` (W6 task count rises; CLAUDE.md §13 headline updated).
- New runtime dependency (recharts) enters the `pnpm audit` + Dependabot surface — acceptable, but it's one more thing to keep patched.
- Removing the create/edit **drawers** touches `Connections.tsx` / `Suites.tsx` / the drawer components; must dedup with the open frontend nits ([#204](https://github.com/TheurgicDuke771/DataQ/issues/204)) and keep the extracted form specs (`connectionFormSpec.ts` / `checkForm.ts` / `suiteTarget.ts`) as the shared source for both create and edit.
- Settings/Secrets/Admin surfaces touch sensitive config — hold the anti-lock-in line (no Azure names in logic) and the KPI-honesty line (no metric without a backing query).

## Alternatives considered

- **`@ant-design/charts`** — visually on-brand out of the box, but G2Plot/G2 underneath makes it ~300 KB+ gz with a much larger dep/audit tree. Rejected: weight out' weighs the brand-fit gain for our 4–5 simple chart types.
- **Apache ECharts** — most powerful, canvas-based; overkill and the largest footprint. Rejected for v1.
- **Build the subset (just Dashboard + Results route) in Week 6, defer Profile/Settings/Admin to Week 7.** Rejected: that is exactly the under-scoping this ADR corrects — the screens are cohesive (shared shell, nav, error pages), and splitting them fragments the adoption.
- **Keep create/edit as drawers, only add new pages.** Rejected: the prototype's structural decision is dedicated pages everywhere except Share; partial adoption leaves an inconsistent navigation model.
