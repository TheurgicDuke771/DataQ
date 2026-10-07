import { afterEach, describe, expect, it, vi } from 'vitest';

import { api } from '../../src/api/client';
import { installDemo } from '../../src/demo/install';

describe('installDemo', () => {
  const original = api.defaults.adapter;
  afterEach(() => {
    api.defaults.adapter = original;
    vi.unstubAllGlobals();
  });

  const bundle = {
    capturedAt: new Date().toISOString(),
    routes: [],
    entries: { 'GET /me': { status: 200, data: { email: 'demo@dataq.local' } } },
  };

  it('answers the shared client from the fixture file beside the bundle, fetched once', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(new Response(JSON.stringify(bundle))));
    vi.stubGlobal('fetch', fetchMock);
    installDemo('/DataQ/demo/');

    expect((await api.get('/me')).data).toEqual({ email: 'demo@dataq.local' });
    await api.get('/me');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('/DataQ/demo/demo-fixtures.json');
  });

  it('fails every read when the fixture file is missing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response('', { status: 404 }))),
    );
    installDemo('/');
    await expect(api.get('/me')).rejects.toThrow('demo fixtures failed to load (404)');
  });
});
