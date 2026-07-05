import { describe, expect, it, vi } from 'vitest';

import { createApiKey, listApiKeys, revokeApiKey } from '../../src/api/apiKeys';
import { api } from '../../src/api/client';

vi.mock('../../src/api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

const mockGet = vi.mocked(api.get);
const mockPost = vi.mocked(api.post);
const mockDelete = vi.mocked(api.delete);

describe('apiKeys client', () => {
  it('lists the caller’s keys (GET /me/api-keys, payload as-is)', async () => {
    const payload = [{ id: 'k1' }];
    mockGet.mockResolvedValueOnce({ data: payload });
    await expect(listApiKeys()).resolves.toBe(payload);
    expect(mockGet).toHaveBeenCalledWith('/me/api-keys');
  });

  it('creates a key (POST /me/api-keys) and returns the once-only token', async () => {
    const created = { id: 'k1', token: 'dq_live_secret' };
    mockPost.mockResolvedValueOnce({ data: created });
    await expect(createApiKey({ name: 'ci', expires_in_days: 90 })).resolves.toBe(created);
    expect(mockPost).toHaveBeenCalledWith('/me/api-keys', { name: 'ci', expires_in_days: 90 });
  });

  it('revokes a key (DELETE /me/api-keys/{id})', async () => {
    mockDelete.mockResolvedValueOnce({ data: null });
    await revokeApiKey('k1');
    expect(mockDelete).toHaveBeenCalledWith('/me/api-keys/k1');
  });
});
