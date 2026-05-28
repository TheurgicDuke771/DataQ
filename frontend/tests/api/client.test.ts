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
  vi.doUnmock('../../src/auth/msalInstance');
});

describe('api client bearer-token interceptor', () => {
  it('does not attach Authorization in dev_bypass mode', async () => {
    vi.doMock('../../src/auth/config', () => ({
      authMode: 'dev_bypass',
      authConfig: {},
    }));
    vi.doMock('../../src/auth/msalInstance', () => ({
      getMsalInstance: () => null,
    }));

    const { api } = await import('../../src/api/client');
    const out = await runRequestInterceptor(api, makeConfig());
    expect((out.headers as Headers).get('Authorization')).toBeUndefined();
  });

  it('attaches Bearer token in real mode when account exists', async () => {
    vi.doMock('../../src/auth/config', () => ({
      authMode: 'real',
      authConfig: { apiScopeUri: 'api://x/user_impersonation' },
    }));
    const acquireTokenSilent = vi.fn().mockResolvedValue({ accessToken: 'tok-abc' });
    vi.doMock('../../src/auth/msalInstance', () => ({
      getMsalInstance: () => ({
        getAllAccounts: () => [{ homeAccountId: 'h1' }],
        acquireTokenSilent,
      }),
    }));

    const { api } = await import('../../src/api/client');
    const out = await runRequestInterceptor(api, makeConfig());
    expect((out.headers as Headers).get('Authorization')).toBe('Bearer tok-abc');
    expect(acquireTokenSilent).toHaveBeenCalledWith({
      account: { homeAccountId: 'h1' },
      scopes: ['api://x/user_impersonation'],
    });
  });

  it('skips token attach in real mode when no account is signed in', async () => {
    vi.doMock('../../src/auth/config', () => ({
      authMode: 'real',
      authConfig: { apiScopeUri: 'api://x/user_impersonation' },
    }));
    vi.doMock('../../src/auth/msalInstance', () => ({
      getMsalInstance: () => ({
        getAllAccounts: () => [],
        acquireTokenSilent: vi.fn(),
      }),
    }));
    const { api } = await import('../../src/api/client');
    const out = await runRequestInterceptor(api, makeConfig());
    expect((out.headers as Headers).get('Authorization')).toBeUndefined();
  });
});
