import axios, { AxiosError } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  createDemoAdapter,
  NOT_RECORDED_MESSAGE,
  READ_ONLY_MESSAGE,
  UNAVAILABLE_MESSAGE,
} from '../../src/demo/adapter';
import type { FixtureBundle } from '../../src/demo/fixtures';
import { DEMO_NOTICE_EVENT } from '../../src/demo/notices';

const NOW = Date.parse('2026-10-03T06:00:00Z');
const BUNDLE: FixtureBundle = {
  capturedAt: '2026-10-01T00:00:00Z',
  routes: [],
  entries: {
    'GET /runs?limit=200': {
      status: 200,
      data: [{ id: 'r1', started_at: '2026-09-30T10:00:00Z' }],
      headers: { 'x-total-count': '41' },
    },
    'GET /suites/s1/column-policy': {
      status: 404,
      data: { error: { message: 'no policy' } },
    },
  },
};

function client() {
  const load = vi.fn(() => Promise.resolve(BUNDLE));
  const http = axios.create({ baseURL: '/api/v1', adapter: createDemoAdapter(load) });
  return { http, load };
}

describe('demo adapter', () => {
  let notices: string[];
  const onNotice = (e: Event) => notices.push((e as CustomEvent<string>).detail);

  beforeEach(() => {
    notices = [];
    window.addEventListener(DEMO_NOTICE_EVENT, onNotice);
    vi.useFakeTimers({ now: NOW });
  });
  afterEach(() => {
    window.removeEventListener(DEMO_NOTICE_EVENT, onNotice);
    vi.useRealTimers();
  });

  it('replays a recorded read with its headers, dates moved up to today', async () => {
    const { http } = client();
    const res = await http.get('/runs', { params: { limit: 200 } });
    expect(res.status).toBe(200);
    expect(res.headers['x-total-count']).toBe('41');
    // Recorded two whole days ago.
    expect(res.data).toEqual([{ id: 'r1', started_at: '2026-10-02T10:00:00Z' }]);
    expect(notices).toEqual([]);
  });

  it('replays a recorded error as an error', async () => {
    const { http } = client();
    const failure = await http.get('/suites/s1/column-policy').catch((e: AxiosError) => e);
    expect(failure).toBeInstanceOf(AxiosError);
    expect((failure as AxiosError).response?.status).toBe(404);
    expect(notices).toEqual([]);
  });

  it('answers an unrecorded filter with the nearest recording and flags it', async () => {
    const { http } = client();
    const res = await http.get('/runs', { params: { limit: 200, status: 'failed' } });
    expect(res.data).toHaveLength(1);
    expect(notices).toEqual(['approximate']);
  });

  it('refuses an unrecorded read rather than inventing an answer', async () => {
    const { http } = client();
    const failure = (await http.get('/incidents').catch((e: AxiosError) => e)) as AxiosError<{
      error: { code: string; message: string };
    }>;
    expect(failure.response?.status).toBe(404);
    expect(failure.response?.data.error).toEqual({
      code: 'demo_not_recorded',
      message: NOT_RECORDED_MESSAGE,
    });
    expect(notices).toEqual(['not-recorded']);
  });

  it.each(['post', 'put', 'patch', 'delete'] as const)(
    'refuses %s without reading the recording',
    async (method) => {
      const { http, load } = client();
      const failure = (await http
        .request({ method, url: '/runs', data: {} })
        .catch((e: AxiosError) => e)) as AxiosError<{ error: { code: string; message: string } }>;
      expect(failure.response?.status).toBe(403);
      expect(failure.response?.data.error).toEqual({
        code: 'demo_read_only',
        message: READ_ONLY_MESSAGE,
      });
      expect(notices).toEqual(['read-only']);
      expect(load).not.toHaveBeenCalled();
    },
  );

  it('answers 503 in the API error shape when the recording cannot be loaded', async () => {
    const http = axios.create({
      baseURL: '/api/v1',
      adapter: createDemoAdapter(() => Promise.reject(new Error('offline'))),
    });
    const failure = (await http.get('/runs').catch((e: unknown) => e)) as AxiosError<{
      error: { message: string };
    }>;
    expect(axios.isAxiosError(failure)).toBe(true);
    expect(failure.response?.status).toBe(503);
    expect(failure.response?.data.error.message).toBe(UNAVAILABLE_MESSAGE);
  });

  it('resolves a refused status when the caller accepts it', async () => {
    const { http } = client();
    const res = await http.post('/runs', {}, { validateStatus: () => true });
    expect(res.status).toBe(403);
  });
});
