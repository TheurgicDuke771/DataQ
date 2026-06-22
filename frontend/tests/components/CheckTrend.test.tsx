import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type CheckResultPoint, listCheckHistory } from '../../src/api/suites';
import { CheckTrend } from '../../src/components/checks/CheckTrend';

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, listCheckHistory: vi.fn() };
});

const mockHistory = vi.mocked(listCheckHistory);

afterEach(() => {
  vi.clearAllMocks();
});

describe('CheckTrend', () => {
  it('fetches the check history for the given suite + check', async () => {
    const points: CheckResultPoint[] = [
      { run_id: 'r1', status: 'pass', metric_value: 0, created_at: '2026-06-10T00:00:00Z' },
      { run_id: 'r2', status: 'warn', metric_value: 2.5, created_at: '2026-06-11T00:00:00Z' },
    ];
    mockHistory.mockResolvedValue(points);
    render(<CheckTrend suiteId="s1" checkId="c1" />);

    await vi.waitFor(() => expect(mockHistory).toHaveBeenCalledWith('s1', 'c1'));
    // With metric data, the empty state is not shown.
    expect(screen.queryByText('No metric history yet')).not.toBeInTheDocument();
  });

  it('shows an empty state when no point records a metric', async () => {
    mockHistory.mockResolvedValue([
      { run_id: 'r1', status: 'pass', metric_value: null, created_at: '2026-06-10T00:00:00Z' },
    ]);
    render(<CheckTrend suiteId="s1" checkId="c1" />);
    expect(await screen.findByText('No metric history yet')).toBeInTheDocument();
  });

  it('shows an error when the history fails to load', async () => {
    mockHistory.mockRejectedValue(new Error('boom'));
    render(<CheckTrend suiteId="s1" checkId="c1" />);
    expect(await screen.findByText('Failed to load trend')).toBeInTheDocument();
  });
});
