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
 * - 'real'         — `mode:'oidc'` with authority + clientId present. The generic
 *                    OIDC auth client (oidc-client-ts — ADR 0028/#504) drives
 *                    redirect-flow login + token acquisition.
 * - 'otp'          — ONLY when `mode:'otp'` is explicitly set (ADR 0032). Email
 *                    one-time codes; the credential is an HttpOnly cookie the SPA
 *                    never sees, so there is nothing else to configure here.
 * - 'dev_bypass'   — ONLY when `mode:'bypass'` is explicitly set. Fail-closed:
 *                    never inferred from missing config. Renders a fixed dev user.
 * - 'unconfigured' — anything else. AuthGate shows a setup-needed banner.
 *
 * The `otp` value is one half of a **pair of coordinated selectors** (ADR 0032
 * decision 2): the backend infers its own mode from `AUTH_EMAIL_*` + the signup
 * allowlist and never reads this one. Set both or neither — this alone yields a
 * code form whose endpoints 503.
 */

export type AuthMode = 'real' | 'otp' | 'dev_bypass' | 'unconfigured';

/** The runtime auth contract injected via `window.__DATAQ_CONFIG__.auth`. */
export interface DataqAuthConfig {
  /**
   * 'bypass' = no IdP (local/eval); 'otp' = email one-time codes (ADR 0032);
   * 'oidc' = IdP sign-in. Absent/unrecognised → unconfigured.
   */
  mode?: 'bypass' | 'otp' | 'oidc';
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
  // `pnpm dev` against a locally-running OTP backend. Explicit opt-in only — an
  // unrecognised value falls through to the OIDC/unconfigured branches rather
  // than being coerced into a mode. Not DEV-gated like bypass, because unlike
  // bypass this mode *is* a real authenticator: turning it on in a production
  // bundle grants nothing without a mailbox and a server-side allowlist.
  const otp = import.meta.env.VITE_AUTH_MODE === 'otp';
  return {
    // OTP is checked BEFORE bypass (#1150). `VITE_AUTH_DEV_BYPASS=true` is the
    // long-standing dev default and lives in the compose file; an explicitly
    // named mode has to win over a leftover boolean, or the local stack would
    // render "no sign-in at all" while its backend was serving email codes —
    // the split-brain the two-selector contract exists to prevent. Safe
    // direction: this can only ever REPLACE a bypass with a real authenticator.
    mode: otp ? 'otp' : bypass ? 'bypass' : tenantId && clientId ? 'oidc' : undefined,
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
  // Fail-closed: bypass and otp ONLY on their explicit flag, never inferred.
  if (cfg.mode === 'bypass') return 'dev_bypass';
  // Checked before the OIDC branch so a deployment that leaves stale
  // authority/clientId values behind while switching to `otp` gets the mode it
  // asked for, rather than silently continuing to render an IdP redirect.
  if (cfg.mode === 'otp') return 'otp';
  if (cfg.authority && cfg.clientId) return 'real';
  return 'unconfigured';
})();

/** Human-readable auth-method label per mode (Profile + Settings "Authentication" rows). */
export const AUTH_METHOD_LABELS: Record<AuthMode, string> = {
  real: 'OIDC (SSO)',
  otp: 'Email one-time code',
  dev_bypass: 'Dev bypass (no IdP)',
  unconfigured: 'Not configured',
};

/** The label for the mode this deployment is actually running in. */
export const authMethodLabel = AUTH_METHOD_LABELS[authMode];

export const DEV_USER = {
  name: 'Dev Bypass User',
  username: 'dev-bypass@dataq.local',
  homeAccountId: 'dev-bypass',
  isDev: true as const,
};
