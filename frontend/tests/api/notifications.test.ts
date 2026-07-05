import { describe, expect, it, vi } from 'vitest';

import { api } from '../../src/api/client';
import {
  deleteNotifications,
  getNotifications,
  putNotifications,
} from '../../src/api/notifications';

vi.mock('../../src/api/client', () => ({
  api: { get: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const mockGet = vi.mocked(api.get);
const mockPut = vi.mocked(api.put);
const mockDelete = vi.mocked(api.delete);

describe('notifications client', () => {
  it('gets a suite config (payload as-is)', async () => {
    const payload = { configured: true };
    mockGet.mockResolvedValueOnce({ data: payload });
    await expect(getNotifications('s1')).resolves.toBe(payload);
    expect(mockGet).toHaveBeenCalledWith('/suites/s1/notifications');
  });

  it('PUTs the update and returns the read', async () => {
    const read = { configured: true, has_slack_webhook: true };
    mockPut.mockResolvedValueOnce({ data: read });
    const update = {
      enabled: true,
      alert_on: 'warn' as const,
      slack_webhook: 'https://hooks.slack.com/x',
      email_recipients: 'a@x.io',
    };
    await expect(putNotifications('s1', update)).resolves.toBe(read);
    expect(mockPut).toHaveBeenCalledWith('/suites/s1/notifications', update);
  });

  it('deletes a suite config', async () => {
    mockDelete.mockResolvedValueOnce({ data: null });
    await deleteNotifications('s1');
    expect(mockDelete).toHaveBeenCalledWith('/suites/s1/notifications');
  });
});
