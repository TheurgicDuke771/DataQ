import { describe, expect, it, vi } from 'vitest';

import {
  listAdminAccess,
  listAdminSuites,
  listAdminUsers,
  listAdminWebhooks,
} from '../../src/api/admin';
import { api } from '../../src/api/client';
import { fetchMe } from '../../src/api/me';

vi.mock('../../src/api/client', () => ({ api: { get: vi.fn() } }));
const mockGet = vi.mocked(api.get);

// Thin unwrap-the-axios-envelope wrappers: each must hit its exact path and
// return `data` as-is. Table-driven — one row per endpoint.
const cases: [string, () => Promise<unknown>, string][] = [
  ['fetchMe', fetchMe, '/me'],
  ['listAdminSuites', listAdminSuites, '/admin/suites'],
  ['listAdminUsers', listAdminUsers, '/admin/users'],
  ['listAdminAccess', listAdminAccess, '/admin/access'],
  ['listAdminWebhooks', listAdminWebhooks, '/admin/orchestration/webhooks'],
];

describe.each(cases)('%s', (_name, call, path) => {
  it(`GETs ${path} and returns the payload`, async () => {
    const payload = { marker: path };
    mockGet.mockResolvedValueOnce({ data: payload });
    await expect(call()).resolves.toBe(payload);
    expect(mockGet).toHaveBeenCalledWith(path);
  });
});
