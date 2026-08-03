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

  // ── otp (ADR 0032, #736) ──────────────────────────────────────────────────
  it("is 'otp' ONLY on an explicit mode:'otp' — nothing else configures it", async () => {
    // Unlike oidc there is no authority/clientId to infer from: the credential is
    // a cookie the server sets, so the mode selector is the whole contract.
    inject({ mode: 'otp' });
    const { authMode } = await loadConfig();
    expect(authMode).toBe('otp');
  });

  it("keeps 'otp' even when stale OIDC values are left behind", async () => {
    // Realistic migration state: an operator flips MODE to otp and forgets to
    // blank the authority/clientId. Falling through to the OIDC branch would
    // render an IdP redirect against a tenant nobody signs into any more.
    inject({ mode: 'otp', authority: 'https://issuer.example/v2.0', clientId: 'spa-1' });
    const { authMode } = await loadConfig();
    expect(authMode).toBe('otp');
  });

  it('does NOT treat an unrecognised mode as otp (or anything else permissive)', async () => {
    inject({ mode: 'magic-link' as unknown as 'otp' });
    const { authMode } = await loadConfig();
    expect(authMode).toBe('unconfigured');
  });

  it('bypass still wins over otp when both are somehow expressible', async () => {
    // Only one `mode` value can be set, so this pins the precedence order rather
    // than a reachable config: bypass is checked first and must stay first.
    inject({ mode: 'bypass' });
    const { authMode } = await loadConfig();
    expect(authMode).toBe('dev_bypass');
  });
});

describe('authMethodLabel (#618 — derived from the runtime mode, never hardcoded)', () => {
  it("labels real OIDC sign-in 'OIDC (SSO)' — provider-neutral, no library name", async () => {
    inject({ mode: 'oidc', authority: 'https://issuer.example/v2.0', clientId: 'spa-1' });
    const { authMethodLabel } = await loadConfig();
    expect(authMethodLabel).toBe('OIDC (SSO)');
  });

  it('is honest about dev-bypass — never claims SSO when no IdP is involved', async () => {
    inject({ mode: 'bypass' });
    const { authMethodLabel } = await loadConfig();
    expect(authMethodLabel).toBe('Dev bypass (no IdP)');
  });

  it("labels an unconfigured deployment 'Not configured'", async () => {
    inject({});
    const { authMethodLabel } = await loadConfig();
    expect(authMethodLabel).toBe('Not configured');
  });

  it("names the OTP mode for what it is — not 'SSO', which it is not", async () => {
    inject({ mode: 'otp' });
    const { authMethodLabel } = await loadConfig();
    expect(authMethodLabel).toBe('Email one-time code');
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

  it('honours VITE_AUTH_MODE=otp as an explicit opt-in for `pnpm dev`', async () => {
    vi.stubEnv('VITE_AZURE_TENANT_ID', '');
    vi.stubEnv('VITE_AZURE_SPA_CLIENT_ID', '');
    vi.stubEnv('VITE_AUTH_MODE', 'otp');
    const { authMode } = await loadConfig();
    expect(authMode).toBe('otp');
  });

  it('lets VITE_AUTH_MODE=otp win over a leftover VITE_AUTH_DEV_BYPASS=true', async () => {
    // The local compose stack carries the bypass flag as a long-standing dev
    // default and switches to OTP by setting the mode (#1150). If the boolean
    // won, that stack would render "no sign-in at all" against a backend that
    // was serving email codes — the split-brain the two-selector contract
    // exists to prevent. Only ever upgrades: bypass → a real authenticator.
    vi.stubEnv('VITE_AZURE_TENANT_ID', '');
    vi.stubEnv('VITE_AZURE_SPA_CLIENT_ID', '');
    vi.stubEnv('VITE_AUTH_DEV_BYPASS', 'true');
    vi.stubEnv('VITE_AUTH_MODE', 'otp');
    const { authMode } = await loadConfig();
    expect(authMode).toBe('otp');
  });

  it('still bypasses when VITE_AUTH_MODE is blank (the downgrade path)', async () => {
    // The compose switch renders an EMPTY VITE_AUTH_MODE when the operator opts
    // into dev-bypass, so blank must not become "unconfigured".
    vi.stubEnv('VITE_AZURE_TENANT_ID', '');
    vi.stubEnv('VITE_AZURE_SPA_CLIENT_ID', '');
    vi.stubEnv('VITE_AUTH_DEV_BYPASS', 'true');
    vi.stubEnv('VITE_AUTH_MODE', '');
    const { authMode } = await loadConfig();
    expect(authMode).toBe('dev_bypass');
  });

  it('ignores a VITE_AUTH_MODE it does not recognise (fail-closed)', async () => {
    vi.stubEnv('VITE_AZURE_TENANT_ID', '');
    vi.stubEnv('VITE_AZURE_SPA_CLIENT_ID', '');
    vi.stubEnv('VITE_AUTH_MODE', 'yolo');
    const { authMode } = await loadConfig();
    expect(authMode).toBe('unconfigured');
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
