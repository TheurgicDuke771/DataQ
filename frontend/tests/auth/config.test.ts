import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { DataqAuthConfig } from '../../src/auth/config';

// authMode / authConfig are computed at module load, so each case sets the source
// then re-imports a fresh module. Precedence: injected window.__DATAQ_CONFIG__
// wins; build-time VITE_* is only the `pnpm dev` fallback (no injected /config.js).
beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  vi.unstubAllEnvs();
  delete (window as { __DATAQ_CONFIG__?: unknown }).__DATAQ_CONFIG__;
});

function inject(auth: DataqAuthConfig | undefined) {
  (window as { __DATAQ_CONFIG__?: { auth?: DataqAuthConfig } }).__DATAQ_CONFIG__ = { auth };
}

async function loadConfig() {
  return import('../../src/auth/config');
}

describe('authMode (runtime config)', () => {
  it("is 'real' when the injected config has mode:'oidc' + authority + clientId", async () => {
    inject({ mode: 'oidc', authority: 'https://issuer.example/v2.0', clientId: 'spa-1' });
    const { authMode } = await loadConfig();
    expect(authMode).toBe('real');
  });

  it("is 'dev_bypass' ONLY on an explicit mode:'bypass'", async () => {
    inject({ mode: 'bypass' });
    const { authMode } = await loadConfig();
    expect(authMode).toBe('dev_bypass');
  });

  it("is 'unconfigured' when the config is injected but empty — fail-closed, never inferred bypass", async () => {
    inject({});
    const { authMode } = await loadConfig();
    expect(authMode).toBe('unconfigured');
  });

  it('does not infer bypass from authority/clientId being absent (fail-closed)', async () => {
    inject({ authority: '', clientId: '' });
    const { authMode } = await loadConfig();
    expect(authMode).toBe('unconfigured');
  });
});

describe('authConfig (runtime config)', () => {
  it('exposes the injected authority / clientId / apiScope', async () => {
    inject({
      mode: 'oidc',
      authority: 'https://issuer.example/v2.0',
      clientId: 'spa-1',
      apiScope: 'api://api-1/access_as_user',
    });
    const { authConfig } = await loadConfig();
    expect(authConfig).toEqual({
      authority: 'https://issuer.example/v2.0',
      clientId: 'spa-1',
      apiScope: 'api://api-1/access_as_user',
    });
  });
});

describe('build-time fallback (pnpm dev, no injected config)', () => {
  it("maps VITE_AZURE_* onto the generic contract → 'real' with a v2.0 authority + api scope", async () => {
    vi.stubEnv('VITE_AZURE_TENANT_ID', 'tenant-1');
    vi.stubEnv('VITE_AZURE_SPA_CLIENT_ID', 'spa-1');
    vi.stubEnv('VITE_AZURE_API_CLIENT_ID', 'api-1');
    vi.stubEnv('VITE_AZURE_API_SCOPE', 'access_as_user');
    const { authMode, authConfig } = await loadConfig();
    expect(authMode).toBe('real');
    expect(authConfig.authority).toBe('https://login.microsoftonline.com/tenant-1/v2.0');
    expect(authConfig.apiScope).toBe('api://api-1/access_as_user');
  });

  it('honours VITE_AUTH_DEV_BYPASS=true as an explicit dev bypass', async () => {
    vi.stubEnv('VITE_AZURE_TENANT_ID', '');
    vi.stubEnv('VITE_AZURE_SPA_CLIENT_ID', '');
    vi.stubEnv('VITE_AUTH_DEV_BYPASS', 'true');
    const { authMode } = await loadConfig();
    expect(authMode).toBe('dev_bypass');
  });

  it("is 'unconfigured' with nothing set and bypass off", async () => {
    vi.stubEnv('VITE_AZURE_TENANT_ID', '');
    vi.stubEnv('VITE_AZURE_SPA_CLIENT_ID', '');
    vi.stubEnv('VITE_AUTH_DEV_BYPASS', 'false');
    const { authMode } = await loadConfig();
    expect(authMode).toBe('unconfigured');
  });

  it('defaults the API scope to user_impersonation', async () => {
    vi.stubEnv('VITE_AZURE_API_CLIENT_ID', 'api-1');
    vi.stubEnv('VITE_AZURE_API_SCOPE', '');
    const { authConfig } = await loadConfig();
    expect(authConfig.apiScope).toBe('api://api-1/user_impersonation');
  });
});
