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

  it('fails a read while the fixture file is missing, and recovers once it is there', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response('', { status: 404 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(bundle)));
    vi.stubGlobal('fetch', fetchMock);
    installDemo('/');

    await expect(api.get('/me')).rejects.toMatchObject({ response: { status: 503 } });
    expect((await api.get('/me')).status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
