import { describe, expect, it, vi } from 'vitest';

import { api } from '../../src/api/client';
import { getIncident, resolveIncident } from '../../src/api/incidents';
import { apiPath } from '../../src/api/path';
import { getRun } from '../../src/api/runs';

vi.mock('../../src/api/client', () => ({
  api: { get: vi.fn(), post: vi.fn() },
}));

const HOSTILE = ['a/b', 'a?x=1', 'a#frag', '..', '../admin', 'a b'];

describe('apiPath', () => {
  it('leaves a UUID and the literal segments untouched', () => {
    const id = '3f2b8c1e-9a4d-4e6f-8b7a-1c2d3e4f5a6b';
    expect(apiPath`/suites/${id}/checks/${7}`).toBe(`/suites/${id}/checks/7`);
  });

  it.each(HOSTILE)('encodes %j into a single segment', (bad) => {
    const out = apiPath`/incidents/${bad}/ack`;
    expect(out.split('/')).toHaveLength(4);
    expect(out).not.toMatch(/[?#]/);
    expect(out).toBe(`/incidents/${encodeURIComponent(bad)}/ack`);
  });
});

describe('API client sinks encode ids', () => {
  it.each(HOSTILE)('incidents and runs: %j', async (bad) => {
    vi.mocked(api.get).mockClear();
    vi.mocked(api.post).mockClear();
    vi.mocked(api.get).mockResolvedValue({ data: {} });
    vi.mocked(api.post).mockResolvedValue({ data: {} });
    await getIncident(bad);
    await resolveIncident(bad);
    await getRun(bad);
    const enc = encodeURIComponent(bad);
    expect(vi.mocked(api.get)).toHaveBeenCalledWith(`/incidents/${enc}`);
    expect(vi.mocked(api.get)).toHaveBeenCalledWith(`/runs/${enc}`);
    expect(vi.mocked(api.post).mock.calls[0][0]).toBe(`/incidents/${enc}/resolve`);
  });
});
