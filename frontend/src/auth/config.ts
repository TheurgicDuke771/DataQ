/**
 * Auth configuration — sourced at RUNTIME, not build time (ADR 0028).
 *
 * The container serves `/config.js` (rendered from env by nginx at startup) which
 * sets `window.__DATAQ_CONFIG__` before the app bundle runs. That means one
 * generic image with nothing baked in — no cloud, no secret, no auth-bypass.
 * When no such global is present — `pnpm dev`, and the static/SWA build until the
 * ADR-0028 cutover — we fall back to the build-time `VITE_*` env (bypass there
 * stays DEV-gated, so a production static bundle can't enable it).
 *
 * The injected contract is provider-neutral (`DATAQ_AUTH_*`): `mode` + a standard
 * OIDC-shaped `authority` / `clientId` / `apiScope`. Azure is one populated shape
 * (`authority = https://login.microsoftonline.com/<tenant>/v2.0`); no `AZURE` in
 * the contract.
 *
 * Mode is computed once at module load:
 * - 'real'         — `mode:'oidc'` with authority + clientId present. The auth
 *                    client (MSAL today; a generic OIDC client next — #504) drives
 *                    redirect-flow login + token acquisition.
 * - 'dev_bypass'   — ONLY when `mode:'bypass'` is explicitly set. Fail-closed:
 *                    never inferred from missing config. Renders a fixed dev user.
 * - 'unconfigured' — anything else. AuthGate shows a setup-needed banner.
 */

export type AuthMode = 'real' | 'dev_bypass' | 'unconfigured';

/** The runtime auth contract injected via `window.__DATAQ_CONFIG__.auth`. */
export interface DataqAuthConfig {
  /** 'bypass' = no IdP (local/eval); 'oidc' = real sign-in. Absent → unconfigured. */
  mode?: 'bypass' | 'oidc';
  /** OIDC issuer/authority URL (e.g. https://login.microsoftonline.com/<tenant>/v2.0). */
  authority?: string;
  /** The public SPA client id registered with the IdP. */
  clientId?: string;
  /** Full scope string requested for the API access token (Azure: api://<api-client-id>/<scope>). */
  apiScope?: string;
}

declare global {
  interface Window {
    __DATAQ_CONFIG__?: { auth?: DataqAuthConfig };
  }
}

/**
 * Build-time fallback for `pnpm dev` (no injected /config.js). Maps the legacy
 * VITE_* Azure vars onto the generic contract so local dev is unchanged. Bypass
 * still requires the explicit VITE_AUTH_DEV_BYPASS=true opt-in.
 */
function fromBuildEnv(): DataqAuthConfig {
  const tenantId = import.meta.env.VITE_AZURE_TENANT_ID;
  const clientId = import.meta.env.VITE_AZURE_SPA_CLIENT_ID;
  const apiClientId = import.meta.env.VITE_AZURE_API_CLIENT_ID;
  const scope = import.meta.env.VITE_AZURE_API_SCOPE || 'user_impersonation';
  // Belt-and-suspenders: bypass in the build-env fallback stays gated on a DEV
  // build, so a production static bundle (e.g. the SWA deploy, which serves this
  // fallback path — the /config.js stub sets no global) can never enable auth
  // bypass even if VITE_AUTH_DEV_BYPASS=true were baked in. The image path never
  // reaches here (nginx injects window.__DATAQ_CONFIG__).
  const bypass = import.meta.env.DEV && import.meta.env.VITE_AUTH_DEV_BYPASS === 'true';
  return {
    mode: bypass ? 'bypass' : tenantId && clientId ? 'oidc' : undefined,
    authority: tenantId ? `https://login.microsoftonline.com/${tenantId}/v2.0` : undefined,
    clientId: clientId || undefined,
    apiScope: apiClientId ? `api://${apiClientId}/${scope}` : undefined,
  };
}

// The injected runtime config wins; the build-time env is the dev-only fallback
// used only when no /config.js was served (i.e. `pnpm dev`).
const injected = typeof window !== 'undefined' ? window.__DATAQ_CONFIG__?.auth : undefined;
const cfg: DataqAuthConfig = injected ?? fromBuildEnv();

export const authConfig = {
  authority: cfg.authority,
  clientId: cfg.clientId,
  apiScope: cfg.apiScope,
} as const;

export const authMode: AuthMode = (() => {
  // Fail-closed: bypass ONLY on the explicit flag, never inferred.
  if (cfg.mode === 'bypass') return 'dev_bypass';
  if (cfg.authority && cfg.clientId) return 'real';
  return 'unconfigured';
})();

export const DEV_USER = {
  name: 'Dev Bypass User',
  username: 'dev-bypass@dataq.local',
  homeAccountId: 'dev-bypass',
  isDev: true as const,
};
