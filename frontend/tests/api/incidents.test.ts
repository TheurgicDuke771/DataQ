import { describe, expect, it, vi } from 'vitest';

import { api } from '../../src/api/client';
import {
  acknowledgeIncident,
  getIncident,
  listIncidents,
  resolveIncident,
} from '../../src/api/incidents';

vi.mock('../../src/api/client', () => ({
  api: { get: vi.fn(), post: vi.fn() },
}));

const mockGet = vi.mocked(api.get);
const mockPost = vi.mocked(api.post);

describe('incidents client', () => {
  it('lists incidents with filters', async () => {
    const rows = [{ id: 'inc-1' }];
    mockGet.mockResolvedValueOnce({ data: rows });
    await expect(listIncidents({ asset_id: 'a1', state: 'open' })).resolves.toBe(rows);
    expect(mockGet).toHaveBeenCalledWith('/incidents', {
      params: { asset_id: 'a1', state: 'open' },
    });
  });

  it('gets one incident', async () => {
    const detail = { id: 'inc-1', evidence: {} };
    mockGet.mockResolvedValueOnce({ data: detail });
    await expect(getIncident('inc-1')).resolves.toBe(detail);
    expect(mockGet).toHaveBeenCalledWith('/incidents/inc-1');
  });

  it('acknowledges with a note', async () => {
    const updated = { id: 'inc-1', status: 'acknowledged' };
    mockPost.mockResolvedValueOnce({ data: updated });
    await expect(acknowledgeIncident('inc-1', 'looking into it')).resolves.toBe(updated);
    expect(mockPost).toHaveBeenCalledWith('/incidents/inc-1/ack', { note: 'looking into it' });
  });

  it('acknowledges without a note (null passthrough)', async () => {
    mockPost.mockResolvedValueOnce({ data: {} });
    await acknowledgeIncident('inc-1');
    expect(mockPost).toHaveBeenCalledWith('/incidents/inc-1/ack', { note: null });
  });

  it('resolves an incident', async () => {
    const updated = { id: 'inc-1', status: 'resolved' };
    mockPost.mockResolvedValueOnce({ data: updated });
    await expect(resolveIncident('inc-1', 'fixed upstream')).resolves.toBe(updated);
    expect(mockPost).toHaveBeenCalledWith('/incidents/inc-1/resolve', { note: 'fixed upstream' });
  });
});
