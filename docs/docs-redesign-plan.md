# Docs redesign plan — 2026-09-02 (internal, not published)

Goal: a modern, beginner-first docs site that keeps what already exceeds the
field (architecture, ADRs, security, compliance, engineering practice) and
fixes what trails it: zero screenshots, no videos, 16 of 25 check types
unmentioned, a prose-only API table, no LLM-readable index.

## Information architecture (Diátaxis, mkdocs-material tabs)

| Tab | Purpose | Pages |
|---|---|---|
| **Home** | Visual landing: what DataQ is, three doors (Try it · Learn · Reference), demo video | `index.md` |
| **Get started** | Tutorials, screenshot-led, one outcome each | Install (Docker) · Your first suite · Your first alert · Connect a pipeline · Ask an AI assistant |
| **Guides** | How-to, task-shaped | Connections (one section per datasource) · Checks (one page per kind: expectation, custom SQL, freshness, volume, schema drift, anomaly, comparison) · Scheduling · Orchestration · Notifications · Sharing & roles · API keys · MCP · LLM features · Assets, lineage & incidents · Best practices |
| **Reference** | Look-up, generated where possible | Check-type catalog (generated from the allowlist) · REST API (table + published OpenAPI JSON) · MCP tools (generated from `mcp_gates.GATES`) · Feature matrix · Configuration (env vars) · Glossary · Changelog |
| **Architecture** | Explanation, kept as-is | Architecture · ADR index · MCP honesty design · Evidence card · Performance baseline · Contributing |
| **Security & compliance** | Kept as-is, promoted to a tab | Security · sub-processors · DPIA · DSR runbook · breach runbook · DPA/BAA |
| **Operate** | Running it | Deployment · Parity · Observability · Troubleshooting · Runbook & FAQ |

Old URLs keep working via `mkdocs-redirects` (MIT).

## Visuals

- **Screenshots** captured by a repeatable Playwright lane (`frontend/e2e-docs/`,
  TS, reuses the dev-bypass seeded stack) — never hand-taken, so they regenerate
  after UI changes. 1440×900 @2x, light theme, demo users only (no personal
  identifiers; the pre-commit hook cannot read PNGs, so the lane is the guard).
  Zoom via `mkdocs-glightbox` (MIT).
- **Videos**: ≤20s clips from Playwright `recordVideo`, transcoded with ffmpeg
  to H.264 mp4 + poster, embedded `<video autoplay loop muted playsinline>`.
  Five clips: sign in & tour · add a connection · author a check & dry-run ·
  run a suite & read results · wire an alert.
- Size budget: ≤2 MB per mp4, ≤300 KB per PNG; total site assets ≤25 MB.

## Phases (one PR each, in order)

0. Plan + epic + this file.
1. **IA**: tabs, section index pages, page moves, redirects, nav — no content rewrite. `mkdocs build --strict` green.
1b. **Versioned docs** (user direction 2026-09-02): `latest` tracks `main`, one frozen copy per release tag (`v1.x.y`), version selector in the header, marketing root untouched. **Decision:** `mike` (MIT, by the mkdocs-material author) with `--deploy-prefix docs` on a `gh-pages` branch; Pages source switches from artifact to branch; the Docs workflow commits `marketing/` to the branch root beside mike's `docs/`. Rejected: self-building every tag into one artifact (each tag needs its own plugin set; rebuilding old tags with today's config is fragile). **No backfill of `v1.0.0`/`v1.1.0`** — both predate the `docs/site/` publication split, so building them would re-run the `exclude_docs` default that once published the ops log. Versioning starts at the next release; until then the selector shows `latest` only.
2. **Capture lane**: `frontend/e2e-docs/` + `scripts/docs/capture.sh`; first screenshot set into Get started.
3. **Videos**: five clips + posters, embedded.
4. **Check-type catalog**: generator from `expectation_allowlist.py` + one guide page per check kind.
5. **Reference generation + LLM-readable docs**: OpenAPI JSON published at build, `llms.txt` + `llms-full.txt`, MCP tool table from GATES. **Every page also published as raw Markdown** at `<page>/index.md` (build hook copies the source), with a header action row (user direction 2026-09-02): **View as Markdown** · **Copy as Markdown** (clipboard) · **Ask AI ▾** — deep links that open the page's Markdown URL in the reader's *own* assistant (Claude, ChatGPT, GitHub Copilot, Cursor) with a prefilled question, plus a "connect your assistant to DataQ over MCP" item pointing at the MCP guide, since the same pages are served by the `get_doc` tool. No hosted chat widget: BYO tool only, nothing leaves the reader's browser except to the tool they chose.
6. **Beginner rewrite**: landing, concepts, tutorials rewritten around the screenshots; glossary linked from first use.
7. **Polish**: dark-mode screenshot variants (optional), social cards, docs-lint hook for image size + alt text.
8. **Marketing refresh** (user direction 2026-09-02): `marketing/index.html` re-anchored on the current feature set — five datasources + three providers, three auth modes, RBAC, assets/lineage/incidents, the seven check kinds, LLM check suggestions + NL→SQL + RCA narrative, 48 MCP tools, two-cloud deploy, compliance pack — with the new screenshots/video from phases 2–3 and links into the new IA. Claims verified against `feature-matrix.md`, never written from memory.

## Guards that stay

- `check-docs-publication.py` (nav findability) and `check-docs-references.py`
  (no ticket refs / internal links in published pages) — every new page passes them.
- `mkdocs build --strict` in the Docs workflow.
- Rule 40: every plugin added is MIT (glightbox, redirects, swagger-ui-tag verified 2026-09-02).

## Out of scope

i18n, a hosted search service.
