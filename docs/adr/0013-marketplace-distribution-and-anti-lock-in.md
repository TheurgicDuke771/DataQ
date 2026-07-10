# ADR 0013 — Marketplace distribution (customer-deployed BYOL) and anti-vendor-lock-in guardrails

- **Status:** Accepted
- **Date:** 2026-06-01
- **Deciders:** @TheurgicDuke771

> **Amendment (2026-07-09, [ADR 0031](0031-oss-byol-distribution-licensing.md)):**
> the licensing model is decided as **free open-source (MIT) — there is no license
> revenue**. This supersedes §5's "licensing model + entitlement/license-key" open
> item and this ADR's licensed-revenue framing of BYOL (the comparison table's
> "billed outside the meter", the Consequences' "revenue is licensed outside the
> meter", the Alternatives' "approximate with BYOL licensing"). The distribution
> model (customer-deployed BYOL), the phasing, and the anti-lock-in guardrails
> below are unchanged; §5's remaining commercial/legal items carry per-item
> dispositions in issue #732.

## Context

We are evaluating listing DataQ on the **Azure, AWS, and GCP marketplaces**. Two questions follow: is it viable given the current architecture, and what must change to keep the option open without derailing the 8-week v1.

There are two fundamentally different ways to be on a cloud marketplace, with very different engineering cost:

| | **Vendor-hosted SaaS** | **Customer-deployed (BYOL)** |
|---|---|---|
| We operate | one multi-tenant service | nothing — the customer deploys into *their own* subscription/account |
| Tenancy | **multi-tenant required** | single-tenant is fine — one isolated instance per customer |
| Billing | marketplace metering-API integration | "bring your own license", billed outside the meter |
| Auth | customer IdP federates into ours | customer's own IdP, in their tenant |
| Fit for DataQ | ❌ requires a tenant-isolation rebuild | ✅ aligns with the current design |

DataQ v1 is **explicitly single-tenant** (CLAUDE.md §1). Vendor-hosted multi-tenant SaaS would force tenant isolation into every table, per-tenant secrets, and tenant-scoped auth — a large rebuild that fights the current architecture. Customer-deployed BYOL is the opposite: single-tenant *is* the unit of sale, one isolated deployment per customer.

The architecture is already partly portable by intent — [ADR 0010](0010-provider-agnostic-infrastructure-seams.md) established that **Azure is the default implementation of each infra seam, not the architecture** (secrets behind a Protocol, auth behind a generic `current_user` boundary, observability containable to one handler, hosting already container-portable). [ADR 0011](0011-extensibility-seams-for-deferred-integrations.md) did the same on the feature side. This ADR records the *distribution decision* those seams serve, and closes the remaining lock-in gaps the marketplace analysis surfaced (packaging, datasource credential auth, an explicit standing checklist).

## Decision

**1. Distribution model = customer-deployed BYOL, not vendor-hosted multi-tenant SaaS.** Each customer deploys an isolated DataQ instance into their own cloud account; we do not operate a shared multi-tenant service. This preserves the single-tenant design rather than rebuilding it.

**2. Marketplace listing is a post-v1 initiative. No marketplace work enters the 8-week v1 budget.** v1 ships Azure-hosted and single-tenant exactly as planned. The only obligation v1 carries is the *negative* one in guardrail 3: do not deepen Azure coupling.

**3. Standing anti-lock-in guardrails — binding on v1 PRs now, even though marketplace work is deferred.** Keep Azure as *one implementation behind each seam*; never let a vendor-specific assumption leak into business logic. These are the rule future PRs are reviewed against:

| Seam | Guardrail | Status |
|---|---|---|
| **Auth** | Protected routes depend on the generic `get_current_user` dependency (mode-bound in `core/auth.py`); **never read MSAL/Entra claims in route or service code**. Azure AD is one OIDC provider, not *the* auth. (extends [ADR 0010](0010-provider-agnostic-infrastructure-seams.md) §2) | discipline now; generic OIDC `AuthProvider` impl deferred |
| **Secrets** | New backends (AWS Secrets Manager, GCP Secret Manager, Vault) are additive `SecretStore` classes; no call site changes. (ADR 0010 §1) | already abstracted |
| **Observability** | App Insights stays behind the `core/logging.py` handler; second backend arrives via **OpenTelemetry/OTLP**, not a parallel hardcoded path. (ADR 0010 §3) | seam reserved |
| **Packaging** | Container images stay cloud-neutral — no Azure-only assumptions baked into app code or entrypoints. The portable deploy artifact is a **Helm chart** (build deferred). | images portable; chart deferred |
| **Datasource auth** | Credential modes stay behind `ConnectionAdapter`; Workload Identity / IAM-role / Managed-Identity are additive per cloud (already the Week-7 plan, SAS/access-key only in v1). | seam exists |
| **Config** | No hardcoded Azure resource names/endpoints in business logic — everything via Pydantic Settings (12-factor). | in place |

**4. Phasing (post-v1).** Sequenced so the portability spend happens once and unlocks both non-Azure clouds together:
   - **Phase 1 — Azure Marketplace, BYOL.** Smallest delta; we already run on Azure. Container/Managed-Application offer via Partner Center.
   - **Phase 2 — portability investment:** Helm chart + published images, generic OIDC auth, OTel observability, AWS/GCP `SecretStore` impls, managed-dependency mapping (RDS/Cloud SQL/Azure DB; ElastiCache/Memorystore/Azure Cache).
   - **Phase 3 — AWS, then GCP.** Mostly packaging + per-marketplace onboarding once Phase 2 lands.

**5. Commercial/legal/operational scope is part of the post-v1 initiative, not architecture** — recorded here so it is not forgotten: licensing model + entitlement/license-key (we are not metering under BYOL), seller registration + tax/banking, EULA / privacy / DPA, defined support SLA, per-marketplace security review, and (for enterprise buyers) SOC 2 + pen test. The existing CI scanning (Bandit, CodeQL, betterleaks, Dependabot) is a head start on the security reviews, not a substitute.

## Consequences

**Positive**
- Single-tenant — today's "limitation" — becomes the *product unit* under BYOL; no multi-tenancy rebuild.
- v1 scope and timeline are untouched; the only v1 cost is review discipline that ADR 0010 already imposes.
- When the initiative starts, the portability work is a localized fill-in of documented seams, not a re-architecture.
- One Helm chart + OIDC + OTel covers AKS/EKS/GKE, collapsing three per-cloud engineering stories into one investment.

**Negative**
- DataQ is **not** marketplace-ready at v1 ship — Phase 2's missing implementations (OIDC `AuthProvider`, OTLP exporter, multi-cloud `SecretStore`, Helm chart) are real work. Accepted: marketplace is explicitly post-v1.
- The auth and packaging guardrails rely on developer/review discipline until the formal seams exist. Mitigated by this ADR + ADR 0010 + code review.
- BYOL forgoes marketplace-metered usage billing; revenue is licensed outside the meter. Accepted as the cost of avoiding a multi-tenant rebuild.

## Alternatives considered

- **Vendor-hosted multi-tenant SaaS** — rejected for v1-era scope. Requires retrofitting tenant isolation into the schema, secrets, and auth — a major rebuild against the single-tenant design, for a revenue model (metered SaaS) we can approximate with BYOL licensing.
- **List on all three clouds at once** — rejected. The Azure coupling is deepest; AWS/GCP need the Phase-2 portability work first. Parallelizing triples onboarding/security-review effort with no shared learning.
- **Do nothing / decide at marketplace time** — rejected. Cheap to keep the door open *now* (guardrail 3 is mostly already ADR-0010 discipline); expensive to undo Azure coupling that spread through dozens of endpoints in the interim. The whole point is to pay near-zero during v1 to keep the option live.

## Related

- [ADR 0010](0010-provider-agnostic-infrastructure-seams.md) — provider-agnostic infra seams (the per-seam discipline this distribution decision depends on).
- [ADR 0011](0011-extensibility-seams-for-deferred-integrations.md) — feature-side extensibility seams (connectors, `ResultPublisher`, dbt-as-provider).
- [ADR 0031](0031-oss-byol-distribution-licensing.md) — **supersedes the §5 "licensing model + entitlement/license-key" line**: distribution licensing decided as free open-source (MIT) + BYOL, no entitlement machinery; the rest of this ADR stands.
- CLAUDE.md §1 (single-tenant scope), §9 (decision table), §11 (anti-patterns — "don't bypass the abstraction").
