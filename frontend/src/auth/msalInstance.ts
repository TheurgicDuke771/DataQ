import { PublicClientApplication, type Configuration } from '@azure/msal-browser';

import { authConfig, authMode } from './config';

let _instance: PublicClientApplication | null = null;

/**
 * Returns the MSAL PublicClientApplication singleton when running in real
 * auth mode; null otherwise. The caller is responsible for awaiting both
 * .initialize() and .handleRedirectPromise() before the first render.
 */
export function getMsalInstance(): PublicClientApplication | null {
  if (authMode !== 'real') return null;
  if (_instance) return _instance;

  const { spaClientId, tenantId } = authConfig;
  if (!spaClientId || !tenantId) {
    // authMode='real' guarantees both are set; defensive guard satisfies the type checker.
    throw new Error('Real auth mode requires VITE_AZURE_SPA_CLIENT_ID + VITE_AZURE_TENANT_ID');
  }
  const config: Configuration = {
    auth: {
      clientId: spaClientId,
      authority: `https://login.microsoftonline.com/${tenantId}`,
      redirectUri: window.location.origin,
      postLogoutRedirectUri: window.location.origin,
    },
    cache: {
      cacheLocation: 'sessionStorage',
    },
  };
  _instance = new PublicClientApplication(config);
  return _instance;
}

/** Test-only: drop the cached instance so the next call rebuilds it. */
export function resetMsalInstanceCache(): void {
  _instance = null;
}
