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
