import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// authMode / authConfig are computed at module load from import.meta.env, so
// each case stubs env then re-imports a fresh module.
beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  vi.unstubAllEnvs();
});

async function loadConfig() {
  return import('../../src/auth/config');
}

describe('authMode', () => {
  it("is 'real' when tenant + SPA client id are set", async () => {
    vi.stubEnv('VITE_AZURE_TENANT_ID', 'tenant-1');
    vi.stubEnv('VITE_AZURE_SPA_CLIENT_ID', 'spa-1');
    const { authMode } = await loadConfig();
    expect(authMode).toBe('real');
  });

  it("is 'dev_bypass' in a DEV build with bypass on and Azure vars empty", async () => {
    vi.stubEnv('VITE_AZURE_TENANT_ID', '');
    vi.stubEnv('VITE_AZURE_SPA_CLIENT_ID', '');
    vi.stubEnv('VITE_AUTH_DEV_BYPASS', 'true');
    const { authMode } = await loadConfig();
    expect(authMode).toBe('dev_bypass');
  });

  it("is 'unconfigured' when nothing is set and bypass is off", async () => {
    vi.stubEnv('VITE_AZURE_TENANT_ID', '');
    vi.stubEnv('VITE_AZURE_SPA_CLIENT_ID', '');
    vi.stubEnv('VITE_AUTH_DEV_BYPASS', 'false');
    const { authMode } = await loadConfig();
    expect(authMode).toBe('unconfigured');
  });
});

describe('authConfig.apiScopeUri', () => {
  it('is derived from the API client id and scope', async () => {
    vi.stubEnv('VITE_AZURE_API_CLIENT_ID', 'api-1');
    vi.stubEnv('VITE_AZURE_API_SCOPE', 'access_as_user');
    const { authConfig } = await loadConfig();
    expect(authConfig.apiScopeUri).toBe('api://api-1/access_as_user');
  });

  it('defaults the scope to user_impersonation', async () => {
    vi.stubEnv('VITE_AZURE_API_CLIENT_ID', 'api-1');
    vi.stubEnv('VITE_AZURE_API_SCOPE', '');
    const { authConfig } = await loadConfig();
    expect(authConfig.apiScopeUri).toBe('api://api-1/user_impersonation');
  });

  it('is undefined when no API client id is set', async () => {
    vi.stubEnv('VITE_AZURE_API_CLIENT_ID', '');
    const { authConfig } = await loadConfig();
    expect(authConfig.apiScopeUri).toBeUndefined();
  });
});
