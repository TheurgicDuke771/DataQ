import { describe, expect, it, vi } from 'vitest';

import { api } from '../../src/api/client';
import { search } from '../../src/api/search';

vi.mock('../../src/api/client', () => ({
  api: { get: vi.fn() },
}));

const mockGet = vi.mocked(api.get);

describe('search client', () => {
  it('queries /search with the term, the palette page size and the abort signal', async () => {
    const body = {
      query: 'orders',
      groups: { suites: [], checks: [], connections: [], runs: [] },
      truncated_groups: [],
    };
    mockGet.mockResolvedValueOnce({ data: body });
    const controller = new AbortController();
    await expect(search('orders', controller.signal)).resolves.toBe(body);
    expect(mockGet).toHaveBeenCalledWith('/search', {
      params: { q: 'orders', limit: 8 },
      signal: controller.signal,
    });
  });
});
