import { describe, expect, it, vi } from 'vitest';

import { listPipelineRuns } from '../../src/api/runs';
import { api } from '../../src/api/client';

vi.mock('../../src/api/client', () => ({
  api: { get: vi.fn() },
}));

const mockGet = vi.mocked(api.get);

describe('runs client — listPipelineRuns paging total (#1108)', () => {
  it('reads the total off X-Total-Count, mirroring the /assets shape (#925)', async () => {
    const rows = [{ id: 'p1' }];
    mockGet.mockResolvedValueOnce({ data: rows, headers: { 'x-total-count': '211' } });
    await expect(listPipelineRuns()).resolves.toEqual({ items: rows, total: 211 });
    expect(mockGet).toHaveBeenCalledWith('/pipeline_runs', { params: undefined });
  });

  it('passes provider/status/limit/offset params through', async () => {
    mockGet.mockResolvedValueOnce({ data: [], headers: { 'x-total-count': '0' } });
    await listPipelineRuns({ provider: 'adf', status: 'failed', limit: 50, offset: 100 });
    expect(mockGet).toHaveBeenCalledWith('/pipeline_runs', {
      params: { provider: 'adf', status: 'failed', limit: 50, offset: 100 },
    });
  });

  it('falls back to the page length when the header is absent (deploy-skew backend)', async () => {
    const rows = [{ id: 'p1' }, { id: 'p2' }];
    mockGet.mockResolvedValueOnce({ data: rows, headers: {} });
    await expect(listPipelineRuns()).resolves.toEqual({ items: rows, total: 2 });
  });

  it('falls back to the page length on a non-numeric header value', async () => {
    const rows = [{ id: 'p1' }];
    mockGet.mockResolvedValueOnce({ data: rows, headers: { 'x-total-count': 'not-a-number' } });
    await expect(listPipelineRuns()).resolves.toEqual({ items: rows, total: 1 });
  });
});
