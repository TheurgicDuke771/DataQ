import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '../../src/api/client';
import { reportEvent, resetTelemetryStatus, routeName } from '../../src/api/telemetry';
import { ROUTE_PATTERNS } from '../../e2e-demo/routes';

vi.mock('../../src/api/client', () => ({
  api: { get: vi.fn(), post: vi.fn() },
}));

const mockGet = vi.mocked(api.get);
const mockPost = vi.mocked(api.post);

async function settle(): Promise<void> {
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
}

beforeEach(() => {
  vi.clearAllMocks();
  resetTelemetryStatus();
  mockPost.mockResolvedValue({ data: {} });
});

describe('routeName', () => {
  it('maps a URL to its route name, never its ids', () => {
    expect(routeName('/suites/8f0c-1234')).toBe('suite_detail');
    expect(routeName('/suites/new')).toBe('suite_new');
    expect(routeName('/suites/abc/checks/def/edit')).toBe('check_edit');
    expect(routeName('/results/run-1')).toBe('run_detail');
    expect(routeName('/results/run-0/compare/run-1')).toBe('run_compare');
    expect(routeName('/nowhere/at/all')).toBe('other');
  });

  it('names every routed page the demo covers', () => {
    for (const pattern of ROUTE_PATTERNS) {
      expect(routeName(pattern.replace(/:id/g, 'x')), pattern).not.toBe('other');
    }
  });
});

describe('reportEvent', () => {
  it('posts nothing while the server says telemetry is off', async () => {
    mockGet.mockResolvedValue({ data: { enabled: false } });
    reportEvent({ name: 'page_viewed', attributes: { route: 'dashboard' } });
    await settle();
    expect(mockPost).not.toHaveBeenCalled();
  });

  it('posts the event when on, asking the status once', async () => {
    mockGet.mockResolvedValue({ data: { enabled: true } });
    reportEvent({ name: 'page_viewed', attributes: { route: 'dashboard' } });
    reportEvent({ name: 'check_editor_abandoned', attributes: { step: 'config' } });
    await settle();
    expect(mockGet).toHaveBeenCalledTimes(1);
    expect(mockPost).toHaveBeenCalledWith('/telemetry/events', {
      events: [{ name: 'page_viewed', attributes: { route: 'dashboard' } }],
    });
    expect(mockPost).toHaveBeenCalledTimes(2);
  });

  it('does not remember a failed status ask', async () => {
    mockGet.mockRejectedValueOnce(new Error('401'));
    reportEvent({ name: 'page_viewed', attributes: { route: 'dashboard' } });
    await settle();
    mockGet.mockResolvedValue({ data: { enabled: true } });
    reportEvent({ name: 'page_viewed', attributes: { route: 'suites' } });
    await settle();
    expect(mockGet).toHaveBeenCalledTimes(2);
    expect(mockPost).toHaveBeenCalledTimes(1);
  });

  it('swallows a failed post', async () => {
    mockGet.mockResolvedValue({ data: { enabled: true } });
    mockPost.mockRejectedValue(new Error('429'));
    reportEvent({ name: 'page_viewed', attributes: { route: 'dashboard' } });
    await settle();
    expect(mockPost).toHaveBeenCalledTimes(1);
  });
});
