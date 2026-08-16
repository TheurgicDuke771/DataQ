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

describe('authClient.getUserManager scope (#1347)', () => {
  function mockRealConfigWithScope(scope?: string) {
    vi.doMock('../../src/auth/config', () => ({
      authMode: 'real',
      authConfig: {
        authority: 'https://issuer.example/v2.0',
        clientId: 'spa-1',
        apiScope: 'api://x/u',
        scope,
      },
    }));
  }

  async function constructedScope(): Promise<string> {
    const { getUserManager } = await import('../../src/auth/authClient');
    getUserManager();
    const { UserManager } = await import('oidc-client-ts');
    const call = vi.mocked(UserManager).mock.calls[0][0];
    return call.scope as string;
  }

  it('requests the default scope list when no override is configured', async () => {
    mockRealConfigWithScope(undefined);
    mockOidc({ getUser: vi.fn(), signinSilent: vi.fn(), signinRedirect: vi.fn() });
    expect(await constructedScope()).toBe('openid profile email offline_access api://x/u');
  });

  it('uses DATAQ_AUTH_SCOPE verbatim when set — Cognito rejects offline_access', async () => {
    mockRealConfigWithScope('openid email profile');
    mockOidc({ getUser: vi.fn(), signinSilent: vi.fn(), signinRedirect: vi.fn() });
    expect(await constructedScope()).toBe('openid email profile');
  });
});

describe('authClient.logout (#1364)', () => {
  function mockRealConfigWithLogoutStyle(logoutStyle?: string) {
    vi.doMock('../../src/auth/config', () => ({
      authMode: 'real',
      authConfig: {
        authority: 'https://issuer.example/v2.0',
        clientId: 'spa-1',
        apiScope: 'api://x/u',
        logoutStyle,
      },
    }));
  }

  function fakeManagerWithEndSession(endSession: string | undefined) {
    return {
      getUser: vi.fn(),
      signinSilent: vi.fn(),
      signinRedirect: vi.fn(),
      signoutRedirect: vi.fn().mockResolvedValue(undefined),
      removeUser: vi.fn().mockResolvedValue(undefined),
      metadataService: { getEndSessionEndpoint: vi.fn().mockResolvedValue(endSession) },
    };
  }

  const assignSpy = vi.fn();
  beforeEach(() => {
    assignSpy.mockClear();
    // jsdom's window.location.assign throws "not implemented"; replace it.
    Object.defineProperty(window, 'location', {
      value: { ...window.location, origin: 'https://app.example', assign: assignSpy },
      writable: true,
    });
  });

  it('uses the standard signoutRedirect when no logoutStyle is configured', async () => {
    mockRealConfigWithLogoutStyle(undefined);
    const mgr = fakeManagerWithEndSession('https://idp.example/logout');
    mockOidc(mgr);
    const { logout } = await import('../../src/auth/authClient');
    await logout();
    expect(mgr.signoutRedirect).toHaveBeenCalledOnce();
    expect(assignSpy).not.toHaveBeenCalled();
  });

  it("builds Cognito's client_id + logout_uri URL when logoutStyle='cognito'", async () => {
    mockRealConfigWithLogoutStyle('cognito');
    const mgr = fakeManagerWithEndSession('https://pool.auth.example/logout');
    mockOidc(mgr);
    const { logout } = await import('../../src/auth/authClient');
    await logout();
    expect(mgr.signoutRedirect).not.toHaveBeenCalled();
    expect(mgr.removeUser).toHaveBeenCalledOnce();
    expect(assignSpy).toHaveBeenCalledWith(
      'https://pool.auth.example/logout?client_id=spa-1&logout_uri=https%3A%2F%2Fapp.example%2F',
    );
  });

  it('still clears the local session when discovery has no end_session_endpoint', async () => {
    mockRealConfigWithLogoutStyle('cognito');
    const mgr = fakeManagerWithEndSession(undefined);
    mockOidc(mgr);
    const { logout } = await import('../../src/auth/authClient');
    await logout();
    expect(mgr.removeUser).toHaveBeenCalledOnce();
    expect(assignSpy).not.toHaveBeenCalled();
    expect(mgr.signoutRedirect).not.toHaveBeenCalled();
  });
});
