import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// A stand-in for oidc-client-ts's ErrorResponse — getApiToken uses `instanceof`
// against the (mocked) class + the `.error` OAuth code to decide interaction.
class FakeErrorResponse extends Error {
  error: string;
  constructor(error: string) {
    super(error);
    this.error = error;
  }
}

interface FakeUserManager {
  getUser: ReturnType<typeof vi.fn>;
  signinSilent: ReturnType<typeof vi.fn>;
  signinRedirect: ReturnType<typeof vi.fn>;
}

function mockOidc(instance: FakeUserManager) {
  vi.doMock('oidc-client-ts', () => ({
    // A regular function (not an arrow) so `new UserManager(...)` constructs; the
    // returned object becomes the instance.
    UserManager: vi.fn(function () {
      return instance;
    }),
    WebStorageStateStore: vi.fn(),
    ErrorResponse: FakeErrorResponse,
  }));
}

function mockRealConfig() {
  vi.doMock('../../src/auth/config', () => ({
    authMode: 'real',
    authConfig: {
      authority: 'https://issuer.example/v2.0',
      clientId: 'spa-1',
      apiScope: 'api://x/u',
    },
  }));
}

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  vi.doUnmock('oidc-client-ts');
  vi.doUnmock('../../src/auth/config');
});

describe('authClient.getApiToken', () => {
  it('returns the cached access token when the user is still valid', async () => {
    mockRealConfig();
    const signinSilent = vi.fn();
    mockOidc({
      getUser: vi.fn().mockResolvedValue({ expired: false, access_token: 'tok-valid' }),
      signinSilent,
      signinRedirect: vi.fn(),
    });
    const { getApiToken } = await import('../../src/auth/authClient');
    expect(await getApiToken()).toBe('tok-valid');
    expect(signinSilent).not.toHaveBeenCalled();
  });

  it('silently renews when the cached token is expired', async () => {
    mockRealConfig();
    mockOidc({
      getUser: vi.fn().mockResolvedValue({ expired: true, access_token: 'stale' }),
      signinSilent: vi.fn().mockResolvedValue({ access_token: 'tok-renewed' }),
      signinRedirect: vi.fn(),
    });
    const { getApiToken } = await import('../../src/auth/authClient');
    expect(await getApiToken()).toBe('tok-renewed');
  });

  it('redirects to the IdP when silent renew needs interaction, and rethrows', async () => {
    mockRealConfig();
    const interactionErr = new FakeErrorResponse('login_required');
    const signinRedirect = vi.fn().mockResolvedValue(undefined);
    mockOidc({
      getUser: vi.fn().mockResolvedValue({ expired: true }),
      signinSilent: vi.fn().mockRejectedValue(interactionErr),
      signinRedirect,
    });
    const { getApiToken } = await import('../../src/auth/authClient');
    await expect(getApiToken()).rejects.toBe(interactionErr);
    expect(signinRedirect).toHaveBeenCalledOnce();
  });

  it('returns null when not signed in — no silent renew, no redirect', async () => {
    mockRealConfig();
    const signinSilent = vi.fn();
    const signinRedirect = vi.fn();
    mockOidc({ getUser: vi.fn().mockResolvedValue(null), signinSilent, signinRedirect });
    const { getApiToken } = await import('../../src/auth/authClient');
    expect(await getApiToken()).toBeNull();
    expect(signinSilent).not.toHaveBeenCalled();
    expect(signinRedirect).not.toHaveBeenCalled();
  });

  it('does NOT redirect on a transient (non-interaction) renew error', async () => {
    mockRealConfig();
    const networkErr = new Error('network down');
    const signinRedirect = vi.fn();
    mockOidc({
      getUser: vi.fn().mockResolvedValue({ expired: true }),
      signinSilent: vi.fn().mockRejectedValue(networkErr),
      signinRedirect,
    });
    const { getApiToken } = await import('../../src/auth/authClient');
    await expect(getApiToken()).rejects.toBe(networkErr);
    expect(signinRedirect).not.toHaveBeenCalled();
  });

  it('returns null outside real auth mode', async () => {
    vi.doMock('../../src/auth/config', () => ({ authMode: 'dev_bypass', authConfig: {} }));
    mockOidc({ getUser: vi.fn(), signinSilent: vi.fn(), signinRedirect: vi.fn() });
    const { getApiToken } = await import('../../src/auth/authClient');
    expect(await getApiToken()).toBeNull();
  });
});
