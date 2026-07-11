import { describe, expect, it, vi } from 'vitest';

import { getAsset, listAssets, updateAsset } from '../../src/api/assets';
import { api } from '../../src/api/client';

vi.mock('../../src/api/client', () => ({
  api: { get: vi.fn(), patch: vi.fn() },
}));

const mockGet = vi.mocked(api.get);
const mockPatch = vi.mocked(api.patch);

describe('assets client', () => {
  it('lists assets', async () => {
    const rows = [{ id: 'a1' }];
    mockGet.mockResolvedValueOnce({ data: rows });
    await expect(listAssets()).resolves.toBe(rows);
    expect(mockGet).toHaveBeenCalledWith('/assets');
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
