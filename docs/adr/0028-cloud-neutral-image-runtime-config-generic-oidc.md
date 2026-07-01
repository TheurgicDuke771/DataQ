# ADR 0028 — Cloud-neutral image: runtime config injection + generic OIDC auth, bypass fail-closed

- **Status:** Accepted
- **Date:** 2026-06-30
- **Deciders:** @TheurgicDuke771
- **Amends:** ADR [0024](0024-app-deployment-infrastructure.md) (frontend hosting moves Static Web App → a Container App running the nginx image)
- **Related:** ADR [0010](0010-provider-agnostic-infrastructure-seams.md) (provider-agnostic seams — Azure is one impl), [0013](0013-marketplace-distribution-and-anti-lock-in.md) (BYOL / anti-lock-in), [0023](0023-container-image-registry-ghcr.md) (GHCR), [0025](0025-production-image-pip-slim.md) (slim image), [0008](0008-mcp-server.md) (MCP token validation), [0026](0026-auth-api-keys-and-principal-seam.md) (DataQ-issued credentials — the backend identity seam)
- **Issue:** [#504](https://github.com/TheurgicDuke771/DataQ/issues/504); post-v1 AWS/GCP IaC → [#505](https://github.com/TheurgicDuke771/DataQ/issues/505). Follows the prebuilt-image work in [#472](https://github.com/TheurgicDuke771/DataQ/issues/472).

## Context

The prebuilt-image distribution (#472) shipped, but exposed three coupling/complexity
smells:

1. **Two frontend images.** Vite inlines `VITE_*` at **build** time, so auth config is
   baked into the bundle. That forced a `:latest` (production, no tenant baked — shows an
   "unconfigured" banner as-pulled) **and** a `:dev` (dev-bypass) image. The `:dev` image
   is **an auth-disabled artifact on a public registry** — pull it, deploy it, no auth.
2. **Azure lock-in in the SPA.** The frontend authenticates with **MSAL**
   (`@azure/msal-browser`), an Azure-AD-specific client. The backend is already
   provider-neutral at the boundary (`get_current_user`, no Entra-claim reads —
   CLAUDE.md §11); the SPA is the remaining coupling.
3. **The image + config are Azure-shaped** (`VITE_AZURE_*`, baked values), not
   cloud-neutral.

The distribution goal (ADR 0013): **one multi-arch generic image per component, nothing
baked in — no cloud, no secrets, no auth-bypass — with config injected at runtime behind
generic seams.**

## Decision

1. **Runtime frontend config injection.** The nginx frontend image serves `/config.js`
   (`window.__DATAQ_CONFIG__ = { auth: { … } }`) rendered from env at container start via
   the **existing** envsubst-on-templates step (added for `DATAQ_API_UPSTREAM` in #472 — no
   new moving part). A blocking classic `<script src="/config.js">` in `index.html` runs
   before the deferred module bundle, so `src/auth/config.ts` reads a **synchronous**
   global (falling back to `import.meta.env` for `pnpm dev`). Its consumers are unchanged.
   The `VITE_*` / `BUILD_NODE_ENV` build-args and the `:dev` variant are **deleted → one
   frontend image**.

2. **Generic `DATAQ_AUTH_*` contract.** The injected config is a provider-shaped identity
   contract — `mode` (`bypass` | `oidc`), `authority`, `clientId`, `apiScope` (+ optional
   `provider` for an impl quirk). **No `AZURE` in the image or the contract**; Azure is one
   populated shape (`authority = https://login.microsoftonline.com/<tenant>/v2.0`). This is
   a seam with one wired impl (ADR 0010) — the *contract* is neutral; a second *impl*
   waits for a real target.

3. **Bypass fail-closed.** Auth resolves to real/`unconfigured` by default; dev-bypass
   activates **only** on explicit `DATAQ_AUTH_MODE=bypass` (frontend) + `AUTH_DEV_BYPASS=true`
   (backend, already default `false`). A config-less image is **not** bypassed. The eval
   compose (`docker-compose.ghcr.yml`) is the single explicit opt-in. Because nothing
   shippable bakes bypass, "off by default" is **structural**, not a convention — a strict
   security improvement over the retired `:dev` image.

4. **Generic OIDC client, validated against Azure (in v1).** Replace MSAL with a standards
   OIDC client (e.g. `oidc-client-ts`) and **validate against the live Azure AD tenant**.
   The login redirect is vanilla OIDC; the risk is acquiring the **API-scope access token**
   (`api://<api-client-id>/user_impersonation`) + **silent renew**. If clean → **retire
   MSAL** (one client for Azure, OIDC, Cognito, Identity Platform, Keycloak, local). If
   Azure needs quirks → fall back to a two-impl seam (MSAL-for-Azure + OIDC-for-others).
   The #168 `InteractionRequiredAuthError` → interactive fallback is reworked in the OIDC
   client's silent-renew-error terms.

5. **Deployed-app cutover (amends ADR 0024).** Runtime `/config.js` is served by the nginx
   **container**, but prod serves the UI from **Static Web App** (static, no runtime
   injection). So the frontend moves **SWA → a Container App** (`dataq-app-frontend`),
   **keeping Key Vault, App Insights / Log Analytics, the shared Postgres + `dataq` DB, and
   Redis as-is**. The deployed app runs `DATAQ_AUTH_MODE=oidc` against the real tenant.
   AWS/GCP deploy IaC is **post-v1** (#505).

   **As implemented (Terraform):** a *targeted* apply, not a literal teardown — Terraform
   destroys only the SWA (`azurerm_static_web_app` + its `null_resource` linked-backend) and
   creates the frontend Container App; **api + worker update in place** (only `PUBLIC_BASE_URL`
   changes) and the migrate job is untouched. Two mechanisms make this clean:
   - **Deterministic FQDNs break the api↔frontend cycle.** The frontend needs the api URL as
     its `/api` proxy upstream and the api needs the frontend URL (`PUBLIC_BASE_URL` + the SPA
     redirect), which as resource references would be a Terraform cycle. Both URLs are instead
     computed as `https://<app-name>.<env-default-domain>` from the shared environment data
     source, so neither resource references the other.
   - **Cloud-neutral nginx resolver.** The `/api` upstream is resolved at request time through
     a variable, which needs a `resolver`; the image detects it from `/etc/resolv.conf` at
     startup (nginx's `NGINX_ENTRYPOINT_LOCAL_RESOLVERS`) instead of a baked-in `127.0.0.11`,
     so the one image works in compose (embedded DNS) and ACA/K8s (cluster DNS). nginx forwards
     the upstream host as `Host` (+ TLS SNI) so ACA's Envoy routes `/api` to the api app.

## Consequences

- **One** multi-arch frontend image, cloud-neutral, nothing baked; the auth-disabled `:dev`
  artifact is gone. Real self-host = set env vars, not rebuild.
- The last Azure coupling in the SPA is removed (or reduced to a documented seam); combined
  with ADR 0026 (backend credentials) the platform is genuinely provider-neutral for auth.
- **Risk:** this replaces MSAL — the auth path currently live in production — so it must be
  validated end-to-end against the real tenant (API-scoped token + silent renew) before
  cutover; expect the security review to flag runtime bypass (mitigated: fail-closed +
  backend enforcement).
- The deployed frontend changes hosting (SWA → Container App), a one-time cutover with
  preserved data/secret/observability layers.
- The generic contract makes AWS/GCP (#505) a drop-in — no future image change.

## Alternatives considered

- **Keep two images (status quo from #472).** Rejected: ships an auth-disabled image on a
  public registry, `:latest` unusable as-pulled, and the docs carry permanent caveats.
- **Relax `config.ts` to allow bypass in a production build.** Rejected earlier (the #472
  two-tag decision) and still: it weakens a guard without solving the cloud-neutrality or
  MSAL coupling. Runtime config + fail-closed bypass is stronger and more general.
- **Build-time config (rebuild per environment).** Rejected: defeats "one generic image";
  every self-hoster would rebuild.
- **MSAL for Azure + OIDC for others (two impls from the start).** Deferred, not chosen up
  front: validating one generic client against Azure first is what decides whether the
  second impl is even needed — one stack is simpler if Azure passes.
- **AWS/GCP IaC now.** Rejected for v1: a second *deploy impl* with no second target is
  over-engineering; the seam (this ADR) keeps the door open at ~zero cost (#505).

## Related

Supersedes the frontend-hosting half of ADR 0024 (SWA → Container App). Closes the SPA
side of the anti-lock-in program (ADR 0010/0013); ADR 0026 closes the backend-credential
side.
