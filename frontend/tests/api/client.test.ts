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
