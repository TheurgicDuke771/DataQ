import { describe, expect, it, vi } from 'vitest';

import { getAsset, listAssets, updateAsset } from '../../src/api/assets';
import { api } from '../../src/api/client';

vi.mock('../../src/api/client', () => ({
  api: { get: vi.fn(), patch: vi.fn() },
}));

const mockGet = vi.mocked(api.get);
const mockPatch = vi.mocked(api.patch);

describe('assets client', () => {
  it('lists assets and reads the total off X-Total-Count (#925)', async () => {
    const rows = [{ id: 'a1' }];
    mockGet.mockResolvedValueOnce({ data: rows, headers: { 'x-total-count': '37' } });
    await expect(listAssets()).resolves.toEqual({ items: rows, total: 37 });
    expect(mockGet).toHaveBeenCalledWith('/assets', { params: undefined });
  });

  it('passes pagination params through', async () => {
    mockGet.mockResolvedValueOnce({ data: [], headers: { 'x-total-count': '0' } });
    await listAssets({ limit: 50, offset: 100 });
    expect(mockGet).toHaveBeenCalledWith('/assets', { params: { limit: 50, offset: 100 } });
  });

  it('falls back to the page length when the header is absent (deploy-skew backend)', async () => {
    const rows = [{ id: 'a1' }, { id: 'a2' }];
    mockGet.mockResolvedValueOnce({ data: rows, headers: {} });
    await expect(listAssets()).resolves.toEqual({ items: rows, total: 2 });
  });

  it('gets one asset', async () => {
    const detail = { summary: { id: 'a1' } };
    mockGet.mockResolvedValueOnce({ data: detail });
    await expect(getAsset('a1')).resolves.toBe(detail);
    expect(mockGet).toHaveBeenCalledWith('/assets/a1');
  });

  it('patches asset metadata', async () => {
    const updated = { id: 'a1', description: 'x' };
    mockPatch.mockResolvedValueOnce({ data: updated });
    await expect(updateAsset('a1', { description: 'x' })).resolves.toBe(updated);
    expect(mockPatch).toHaveBeenCalledWith('/assets/a1', { description: 'x' });
  });
});
