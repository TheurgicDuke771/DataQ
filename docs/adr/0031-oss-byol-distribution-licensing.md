# ADR 0031 — Distribution licensing: free open-source (MIT) + customer-deployed BYOL

- **Status:** Accepted
- **Date:** 2026-07-09
- **Deciders:** @TheurgicDuke771

## Context

[ADR 0013](0013-marketplace-distribution-and-anti-lock-in.md) chose **customer-deployed BYOL** as the marketplace distribution model and deferred the commercial/legal scope to the post-v1 initiative, listing "licensing model + entitlement/license-key (we are not metering under BYOL)" among the open items (§5). The 2026-07-09 marketplace-readiness review (issue #732) surfaced the tension that line carries: the repository is already published under the **MIT license**, which grants everyone the right to use, copy, modify, distribute, and *sell* the software — a paid-entitlement model layered on top would be incoherent (you cannot sell a key to rights the license already gives away), and retrofitting a restrictive license onto an already-public MIT repo only gets harder with time and adoption.

The same review ran a full dependency license audit against the actual installed trees (not declared metadata alone):

- **Backend** — 239 packages in the conda env: **zero strong-copyleft or source-available licenses**. Core stack: Great Expectations, FastMCP, the Snowflake/Databricks connectors, pyiceberg, boto3, pyarrow, OpenTelemetry (Apache-2.0); FastAPI, SQLAlchemy, Pydantic, Alembic (MIT); Celery, uvicorn, pandas, numpy (BSD). Weak copyleft only: `psycopg2-binary` (LGPL-3.0, dynamically linked), `certifi`/`pathspec` (MPL-2.0, file-level).
- **Frontend** — 643 packages in `node_modules`: **zero strong copyleft**. `dompurify` is dual-licensed MPL-2.0 OR Apache-2.0 (Apache elected); `lightningcss` (MPL-2.0) is build-time only and never distributed; `@fontsource/*` fonts are OFL-1.1 (standard for bundled web fonts).

So the only distribution-compliance obligation is notice preservation (MIT/BSD copyright lines, Apache-2.0 NOTICE content) — there is no copyleft constraint on the model.

## Decision

1. **DataQ is and remains free open-source software under the MIT license.** There is no paid license, no entitlement check, and no license-key machinery — the "licensing model + entitlement/license-key" line of ADR 0013 §5 is **superseded by this ADR**. Everything else in ADR 0013 (customer-deployed BYOL distribution, the Azure→AWS/GCP phasing, the anti-lock-in guardrails) stands unchanged; under this decision "BYOL" reads as *bring your own (free, MIT) license* — the customer deploys the OSS into their own account, and no marketplace metering or entitlement integration is needed.
2. **Marketplace listings are free/BYOL offers of the OSS artifacts** (the public GHCR images — ADR 0023 — plus the portable install artifact when it lands). Listings still require seller registration and per-marketplace certification, but not commerce integration.
3. **Distribution compliance = notices, wired into the release path.** Container images and GitHub releases ship a `THIRD-PARTY-NOTICES` file (or an SPDX SBOM carrying license data) covering the bundled dependency licenses; generation is automated in CI/publish rather than hand-maintained (tracked in #732).
4. **Dependency license guardrail (standing, binding on future PRs):** the dependency tree stays free of strong-copyleft and source-available licenses (GPL, AGPL, SSPL, BUSL/Elastic, Commons-Clause). Weak copyleft (LGPL/MPL/EPL) is acceptable with notice. Adding a dependency that violates this needs an explicit ADR-level exception. The check joins the quarterly supply-chain audit cadence (CONTRIBUTING rule 39).
5. **Monetization, if it ever happens, is built beside the OSS, not into it** — support/services, a hosted offering, or commercially-licensed *additions*; never retroactive enforcement against the MIT core.

## Consequences

**Positive**

- No entitlement/license-server build — the last commercial-machinery item ADR 0013 carried disappears; the marketplace path reduces to packaging + certification.
- Free offers are the lowest-friction marketplace listing type (no metering/transaction integration), and the OSS grant maximizes eval→adoption conversion — consistent with the anti-lock-in posture of ADR 0010/0013.
- One coherent license story: repo, images, and marketplace artifact all carry the same MIT grant; the audit above confirms nothing in the tree contradicts it.

**Negative / accepted trade-offs**

- **No direct license revenue, and anyone may fork, rebrand, or resell DataQ** — inherent to MIT and accepted knowingly; the countermove is execution and trust, not license enforcement.
- Free users still generate support expectations — set them explicitly (SUPPORT.md, community channels; a #732 checklist item).
- A future pivot to a restrictive license would be practically irreversible for already-published versions; this decision treats that door as closed.

## Alternatives considered

- **Dual licensing (AGPL + commercial)** — rejected. AGPL chills exactly the enterprise/self-hosted adoption BYOL targets, and dual licensing requires CLA overhead plus ownership discipline over every contribution for the commercial grant to stay clean.
- **Source-available (BSL/Elastic-style)** — rejected. Not open source, adds marketplace-certification and procurement friction, and contradicts the stated intent of a free OSS offering; also awkward to apply retroactively to an already-MIT repo.
- **Proprietary core + free tier with entitlement keys** — rejected. This is precisely the machinery ADR 0013 hoped to scope and this review found incoherent against the existing MIT grant; it would also forfeit the OSS adoption channel.
- **Keep MIT but sell entitlement keys anyway** — rejected as legally incoherent: MIT already grants every right a key would gate.

## Related

- [ADR 0013](0013-marketplace-distribution-and-anti-lock-in.md) — the BYOL distribution decision this ADR completes; its §5 licensing-model line is superseded here, the rest stands.
- [ADR 0023](0023-container-image-registry-ghcr.md) — public GHCR images, the free distribution channel.
- Issue #732 — marketplace-readiness checklist (license-audit record, THIRD-PARTY-NOTICES/SBOM automation, SUPPORT.md, G-h/G-i prerequisites).
