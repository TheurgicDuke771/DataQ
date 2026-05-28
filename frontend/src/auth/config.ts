/**
 * Auth configuration sourced from VITE_* env vars at build time.
 *
 * Mode is computed once at module load:
 * - 'real'         — VITE_AZURE_TENANT_ID + VITE_AZURE_SPA_CLIENT_ID set.
 *                    MSAL handles redirect-flow login + token acquisition.
 * - 'dev_bypass'   — DEV build + VITE_AUTH_DEV_BYPASS=true + Azure vars empty.
 *                    No MSAL; renders a fixed dev user. Mirrors backend behaviour.
 * - 'unconfigured' — anything else. AuthGate shows a setup-needed banner.
 */

export type AuthMode = 'real' | 'dev_bypass' | 'unconfigured';

const tenantId = import.meta.env.VITE_AZURE_TENANT_ID;
const spaClientId = import.meta.env.VITE_AZURE_SPA_CLIENT_ID;
const apiClientId = import.meta.env.VITE_AZURE_API_CLIENT_ID;
const apiScope = import.meta.env.VITE_AZURE_API_SCOPE || 'user_impersonation';
const devBypass = import.meta.env.VITE_AUTH_DEV_BYPASS === 'true';

export const authConfig = {
  tenantId,
  spaClientId,
  apiClientId,
  apiScope,
  apiScopeUri: apiClientId ? `api://${apiClientId}/${apiScope}` : undefined,
} as const;

export const authMode: AuthMode = (() => {
  if (tenantId && spaClientId) return 'real';
  if (import.meta.env.DEV && devBypass) return 'dev_bypass';
  return 'unconfigured';
})();

export const DEV_USER = {
  name: 'Dev Bypass User',
  username: 'dev-bypass@dataq.local',
  homeAccountId: 'dev-bypass',
  isDev: true as const,
};
