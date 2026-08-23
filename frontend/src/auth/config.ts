/** Auth configuration — sourced at RUNTIME, not build time (ADR 0028). */

export type AuthMode = 'real' | 'otp' | 'dev_bypass' | 'unconfigured';

/** The runtime auth contract injected via `window.__DATAQ_CONFIG__.auth`. */
export interface DataqAuthConfig {
  /** 'bypass' = no IdP (local/eval); 'otp' = email one-time codes (ADR 0032); 'oidc' = IdP sign-in. */
  mode?: 'bypass' | 'otp' | 'oidc';
  /** OIDC issuer/authority URL (e.g. https://login.microsoftonline.com/<tenant>/v2.0). */
  authority?: string;
  /** The public SPA client id registered with the IdP. */
  clientId?: string;
  /** Full scope string requested for the API access token (Azure: api://<api-client-id>/<scope>). */
  apiScope?: string;
  /** Complete OVERRIDE of the requested OAuth scope string (#1347). */
  scope?: string;
  /** Sign-out protocol dialect (#1364). */
  logoutStyle?: 'cognito' | '';
}

declare global {
  interface Window {
    __DATAQ_CONFIG__?: { auth?: DataqAuthConfig };
  }
}

/** Build-time fallback for `pnpm dev` (no injected /config.js). */
function fromBuildEnv(): DataqAuthConfig {
  const tenantId = import.meta.env.VITE_AZURE_TENANT_ID;
  const clientId = import.meta.env.VITE_AZURE_SPA_CLIENT_ID;
  const apiClientId = import.meta.env.VITE_AZURE_API_CLIENT_ID;
  const scope = import.meta.env.VITE_AZURE_API_SCOPE || 'user_impersonation';
  // Belt-and-suspenders: bypass in the build-env fallback stays gated on a DEV build, so a
  // production static bundle (e.g. the SWA deploy, which serves this fallback path — the
  // /config.js stub sets no global) can never enable auth bypass even if VITE_AUTH_DEV_BYPASS=true
  // were baked in.
  const bypass = import.meta.env.DEV && import.meta.env.VITE_AUTH_DEV_BYPASS === 'true';
  // `pnpm dev` against a locally-running OTP backend.
  const otp = import.meta.env.VITE_AUTH_MODE === 'otp';
  return {
    // OTP is checked BEFORE bypass (#1150).
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
  scope: cfg.scope,
  logoutStyle: cfg.logoutStyle,
} as const;

export const authMode: AuthMode = (() => {
  // Fail-closed: bypass and otp ONLY on their explicit flag, never inferred.
  if (cfg.mode === 'bypass') return 'dev_bypass';
  // Checked before the OIDC branch so a deployment that leaves stale authority/clientId values
  // behind while switching to `otp` gets the mode it asked for.
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
