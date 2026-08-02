import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { InternalAxiosRequestConfig } from 'axios';

interface Headers {
  set: (k: string, v: string) => void;
  get: (k: string) => string | undefined;
}

async function runRequestInterceptor(
  api: import('axios').AxiosInstance,
  config: InternalAxiosRequestConfig,
): Promise<InternalAxiosRequestConfig> {
  const handlers = api.interceptors.request as unknown as {
    handlers: {
      fulfilled: (c: InternalAxiosRequestConfig) => Promise<InternalAxiosRequestConfig>;
    }[];
  };
  const handler = handlers.handlers[0];
  if (!handler) throw new Error('No request interceptor registered');
  return handler.fulfilled(config);
}

function makeConfig(): InternalAxiosRequestConfig {
  const store: Record<string, string> = {};
  const headers: Headers = {
    set: (k, v) => {
      store[k] = v;
    },
    get: (k) => store[k],
  };
  return { headers } as unknown as InternalAxiosRequestConfig;
}

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  vi.doUnmock('../../src/auth/config');
  vi.doUnmock('../../src/auth/authClient');
});

describe('api client bearer-token interceptor', () => {
  it('does not attach Authorization (or call getApiToken) outside real mode', async () => {
    vi.doMock('../../src/auth/config', () => ({ authMode: 'dev_bypass' }));
    const getApiToken = vi.fn();
    vi.doMock('../../src/auth/authClient', () => ({ getApiToken }));

    const { api } = await import('../../src/api/client');
    const out = await runRequestInterceptor(api, makeConfig());
    expect((out.headers as Headers).get('Authorization')).toBeUndefined();
    expect(getApiToken).not.toHaveBeenCalled();
  });

  it('attaches the OIDC access token in real mode', async () => {
    vi.doMock('../../src/auth/config', () => ({ authMode: 'real' }));
    vi.doMock('../../src/auth/authClient', () => ({
      getApiToken: vi.fn().mockResolvedValue('tok-abc'),
    }));

    const { api } = await import('../../src/api/client');
    const out = await runRequestInterceptor(api, makeConfig());
    expect((out.headers as Headers).get('Authorization')).toBe('Bearer tok-abc');
  });

  it('skips the header when not signed in (null token)', async () => {
    vi.doMock('../../src/auth/config', () => ({ authMode: 'real' }));
    vi.doMock('../../src/auth/authClient', () => ({
      getApiToken: vi.fn().mockResolvedValue(null),
    }));

    const { api } = await import('../../src/api/client');
    const out = await runRequestInterceptor(api, makeConfig());
    expect((out.headers as Headers).get('Authorization')).toBeUndefined();
  });

  it('rejects the request when token acquisition throws (interactive redirect handoff)', async () => {
    vi.doMock('../../src/auth/config', () => ({ authMode: 'real' }));
    const err = new Error('needs interaction — redirecting');
    vi.doMock('../../src/auth/authClient', () => ({
      getApiToken: vi.fn().mockRejectedValue(err),
    }));

    const { api } = await import('../../src/api/client');
    await expect(runRequestInterceptor(api, makeConfig())).rejects.toBe(err);
  });
});

// ── 429: tell the user how long to wait (#788) ──────────────────────────────

describe('retryAfterSeconds', () => {
  const err = (status: number, data?: unknown, headers?: Record<string, string>) =>
    ({ response: { status, data, headers: headers ?? {} } }) as never;
  // Dynamic import to match the rest of this file: the module is re-imported per
  // test so the auth-mode mocks apply.
  const subject = async () => (await import('../../src/api/client')).retryAfterSeconds;

  it('reads the envelope detail', async () => {
    const retryAfterSeconds = await subject();
    expect(retryAfterSeconds(err(429, { error: { detail: { retry_after_seconds: 42 } } }))).toBe(
      42,
    );
  });

  it('falls back to the Retry-After header', async () => {
    const retryAfterSeconds = await subject();
    // The header is what survives a proxy that rewrites the body, so it is not a
    // redundant second source.
    expect(retryAfterSeconds(err(429, {}, { 'retry-after': '17' }))).toBe(17);
  });

  it('prefers the body over the header when both are present', async () => {
    const retryAfterSeconds = await subject();
    expect(
      retryAfterSeconds(
        err(429, { error: { detail: { retry_after_seconds: 5 } } }, { 'retry-after': '99' }),
      ),
    ).toBe(5);
  });

  it('rounds a fractional wait UP, never down', async () => {
    const retryAfterSeconds = await subject();
    // Rounding down would tell the user to retry while still throttled — advice
    // that produces a second 429 and reads as the app lying.
    expect(retryAfterSeconds(err(429, { error: { detail: { retry_after_seconds: 2.1 } } }))).toBe(
      3,
    );
  });

  it('is undefined for any non-429, and for a 429 carrying nothing usable', async () => {
    const retryAfterSeconds = await subject();
    expect(
      retryAfterSeconds(err(500, { error: { detail: { retry_after_seconds: 9 } } })),
    ).toBeUndefined();
    expect(retryAfterSeconds(err(429, {}))).toBeUndefined();
    expect(
      retryAfterSeconds(err(429, {}, { 'retry-after': 'Wed, 21 Oct 2026 07:28:00 GMT' })),
    ).toBeUndefined(); // HTTP-date form: not a number, so say nothing rather than NaN
    expect(retryAfterSeconds(undefined)).toBeUndefined();
    expect(retryAfterSeconds(new Error('boom'))).toBeUndefined();
  });
});

// ── 401 → "your OTP session is gone" (ADR 0032, #736) ───────────────────────

/**
 * The response interceptor's session-loss signal.
 *
 * The exclusion of `/auth/*` is the whole reason this has a test: `POST
 * /auth/otp/verify` answers 401 for a **wrong code**, and treating that as
 * session loss would throw the user back to the email step on every mistyped
 * digit — destroying the code they were half way through entering. Everything
 * else's 401 genuinely means the cookie expired, was revoked, or was cleared.
 */
describe('otp session-loss signal', () => {
  const notifySessionInvalidated = vi.fn();

  async function runResponseInterceptor(mode: string, url: string, status: number) {
    vi.resetModules();
    notifySessionInvalidated.mockReset();
    vi.doMock('../../src/auth/config', () => ({ authMode: mode }));
    vi.doMock('../../src/auth/authClient', () => ({ getApiToken: vi.fn() }));
    vi.doMock('../../src/auth/sessionEvents', () => ({ notifySessionInvalidated }));

    const { api } = await import('../../src/api/client');
    const handlers = api.interceptors.response as unknown as {
      handlers: { rejected: (e: unknown) => Promise<unknown> }[];
    };
    const handler = handlers.handlers[0];
    if (!handler) throw new Error('No response interceptor registered');
    const error = { config: { url }, response: { status, data: {}, headers: {} } };
    await handler.rejected(error).catch(() => {});
    return notifySessionInvalidated;
  }

  afterEach(() => {
    vi.doUnmock('../../src/auth/sessionEvents');
  });

  it('fires for a 401 on an ordinary API call in otp mode', async () => {
    const notify = await runResponseInterceptor('otp', '/suites', 401);
    expect(notify).toHaveBeenCalledOnce();
  });

  it.each(['/auth/otp/verify', '/auth/otp/request', '/auth/logout'])(
    'does NOT fire for a 401 on %s — that is the sign-in path answering normally',
    async (url) => {
      const notify = await runResponseInterceptor('otp', url, 401);
      expect(notify).not.toHaveBeenCalled();
    },
  );

  it.each([403, 429, 500, 502])('does NOT fire for a %i — only 401 means signed out', async (s) => {
    const notify = await runResponseInterceptor('otp', '/suites', s);
    expect(notify).not.toHaveBeenCalled();
  });

  it.each(['real', 'dev_bypass', 'unconfigured'])(
    'does NOT fire in %s mode — there is no cookie session to lose',
    async (mode) => {
      // An OIDC 401 belongs to the token layer (silent renew / interactive
      // redirect); hijacking it here would fight that flow.
      const notify = await runResponseInterceptor(mode, '/suites', 401);
      expect(notify).not.toHaveBeenCalled();
    },
  );

  it('does not throw when the error carries no config at all', async () => {
    vi.resetModules();
    notifySessionInvalidated.mockReset();
    vi.doMock('../../src/auth/config', () => ({ authMode: 'otp' }));
    vi.doMock('../../src/auth/authClient', () => ({ getApiToken: vi.fn() }));
    vi.doMock('../../src/auth/sessionEvents', () => ({ notifySessionInvalidated }));
    const { api } = await import('../../src/api/client');
    const handlers = api.interceptors.response as unknown as {
      handlers: { rejected: (e: unknown) => Promise<unknown> }[];
    };
    const handler = handlers.handlers[0];
    if (!handler) throw new Error('No response interceptor registered');
    await handler.rejected({ response: { status: 401, headers: {} } }).catch(() => {});
    // No config → no url → treated as a non-/auth call, which is the safe default
    // (drop to sign-in) rather than a crash inside an interceptor.
    expect(notifySessionInvalidated).toHaveBeenCalledOnce();
  });
});
