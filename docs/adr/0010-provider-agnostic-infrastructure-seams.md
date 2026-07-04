# ADR 0010 — Provider-agnostic infrastructure seams (Azure is the default, not the architecture)

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** @TheurgicDuke771

## Context

DataQ v1 is explicitly **single-tenant and Azure-hosted** (CLAUDE.md §1). The architecture leans on Azure in several places: Key Vault (secrets), Application Insights (observability), Azure AD / MSAL (auth), Azure Monitor (ADF event ingestion), and Container Apps / Static Web App (hosting). A reasonable question followed: should we make DataQ cloud-agnostic now, or after v1?

Making everything cloud-agnostic up front would be over-engineering against a requirement we do not have — v1 has no second-cloud target, and the 8-week timeline has no slack for speculative abstraction (see also the YAGNI posture behind ADR 0003). But hard-coupling everything to Azure risks an expensive retrofit later for the couplings that **spread as the codebase grows**.

The deciding lens is therefore *per-seam*, not global: **does deferring the abstraction make it more expensive later (a one-way door), or does the cost stay flat (a two-way door)?**

The three infrastructure couplings differ sharply on that axis:

| Coupling | Where it lives | Does lock-in spread as v1 grows? |
|---|---|---|
| **Secrets** | `core/secrets.py` | No — already behind a Protocol |
| **Auth** | `core/auth.py`, every protected endpoint, frontend MSAL, `/mcp` (W7) | **Yes** — every new endpoint adds a call site |
| **Observability** | `core/logging.py` (one `AzureLogHandler`) | No — contained to one file; all logs already flow through `get_logger()` |
| **Hosting** | Docker images + static bundle | No — already portable (ECS / Cloud Run / K8s) with no code change |

## Decision

**Azure is the default implementation of each infrastructure seam, not the architecture. We do not build a second cloud's implementation during v1. We do keep each Azure dependency behind a seam, with the *timing* of that seam decided per-coupling by whether lock-in spreads.**

1. **Secrets — already done, no action.** `SecretStore` is a `runtime_checkable` Protocol with `EnvSecretStore` (dev) and `AzureKeyVaultStore` (prod) behind a config-keyed factory (`core/secrets.py`). A future AWS Secrets Manager / Vault backend is a new class + enum value, zero ripple.

2. **Auth — guard the boundary now, do not build a second IdP.** Azure AD stays the only identity provider for v1 (single-tenant scope). But because auth coupling spreads with every new endpoint, protected routes must depend on a generic internal "current user" dependency that returns DataQ's own `User`, **never reach into MSAL token claims directly** in service or route code. This is a discipline, not a new abstraction layer — cheap now, expensive to retrofit after dozens of endpoints exist. A full `AuthProvider` seam (Auth0 / Okta / Cognito) is deferred to a real second-IdP requirement.

3. **Observability — defer to OpenTelemetry when a second backend is real.** App Insights remains the sole telemetry backend for v1. Because the coupling is contained to a single handler in `core/logging.py` and deferring it does not grow the blast radius, we do **not** abstract it now. When a second backend is actually needed, the seam is **OpenTelemetry** (structlog → OTLP exporter → any backend), swapped in one file.

4. **Hosting — already portable, no action.** Container images + a static bundle run anywhere; no code-level cloud assumptions.

**General rule:** abstract the seams whose lock-in *spreads* as you build (auth boundary); defer the ones that stay *contained* (observability); skip the ones already done (secrets). Do not build speculative multi-cloud infrastructure during the single-tenant v1.

## Consequences

**Positive**
- No roadmap budget spent on multi-cloud support we do not yet need.
- The one coupling that gets more expensive to undo over time (auth) is contained from the first new endpoint, at near-zero cost.
- When a real second-cloud / second-IdP / second-telemetry-backend requirement lands, each retrofit is a localized, well-understood change against a documented seam.

**Negative**
- DataQ is *not* cloud-portable at v1 ship — porting still requires writing the missing backend implementations (an `AuthProvider`, an OTLP exporter path). Accepted: v1 has no second-cloud target.
- The auth-boundary rule relies on developer discipline until/unless a formal `AuthProvider` interface is introduced. Mitigated by code review and this ADR.

## Alternatives considered

- **Build full cloud-agnostic infrastructure during v1** — rejected. Over-engineering against a single-tenant Azure requirement; burns 8-week budget; designing abstractions with no real second implementation tends to produce the wrong abstraction.
- **Hard-couple everything to Azure, generalize only if needed** — rejected for *auth specifically*. Auth coupling spreads across every endpoint, so a later retrofit is materially more expensive than the cheap boundary discipline adopted here. Accepted for secrets (already abstracted), observability, and hosting (contained / already portable).

## Related

- ADR 0009 — flat monorepo layout (secrets layout note) and `core/secrets.py`.
- `core/config.py` (Azure settings), `core/logging.py` (App Insights handler), `core/auth.py`.
- ADR 0011 — extensibility seams for deferred connectors/integrations (the feature-side counterpart to this infra-side decision).
- v1 action items tracked in `docs/progress-v1.md` (Week 2 auth-boundary note, Week 7 observability seam note).
