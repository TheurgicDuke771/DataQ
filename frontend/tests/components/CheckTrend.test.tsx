import { render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  type CheckBaseline,
  type CheckResultPoint,
  getCheckBaseline,
  listCheckHistory,
} from '../../src/api/suites';
import { CheckTrend } from '../../src/components/checks/CheckTrend';

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, listCheckHistory: vi.fn(), getCheckBaseline: vi.fn() };
});

const mockHistory = vi.mocked(listCheckHistory);
const mockBaseline = vi.mocked(getCheckBaseline);

// recharts' SVG doesn't lay out under jsdom (zero-size container, same lesson
// as QualityTrends.test.tsx) — these assert the chrome, empty/error states, and
// the a11y table fallback (real DOM rows), not rendered chart pixels.

const expectationCheck = {
  id: 'c1',
  kind: 'expectation',
  warn_threshold: null,
  fail_threshold: null,
  critical_threshold: null,
};

const thresholdCheck = {
  id: 'c1',
  kind: 'expectation',
  warn_threshold: 1,
  fail_threshold: 5,
  critical_threshold: 10,
};

const anomalyCheck = {
  id: 'c1',
  kind: 'anomaly',
  warn_threshold: null,
  fail_threshold: 3,
  critical_threshold: null,
};

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe('CheckTrend', () => {
  it('fetches the check history for the given suite + check, with a 90-point window', async () => {
    const points: CheckResultPoint[] = [
      { run_id: 'r1', status: 'pass', metric_value: 0, created_at: '2026-06-10T00:00:00Z' },
      { run_id: 'r2', status: 'warn', metric_value: 2.5, created_at: '2026-06-11T00:00:00Z' },
    ];
    mockHistory.mockResolvedValue(points);
    render(<CheckTrend suiteId="s1" check={expectationCheck} />);

    await vi.waitFor(() => expect(mockHistory).toHaveBeenCalledWith('s1', 'c1', 90));
    // With metric data, the empty state is not shown.
    expect(screen.queryByText('No metric history yet')).not.toBeInTheDocument();
    // Non-anomaly kind: no baseline fetch at all.
    expect(mockBaseline).not.toHaveBeenCalled();
  });

  it('shows an empty state when no point records a metric', async () => {
    mockHistory.mockResolvedValue([
      { run_id: 'r1', status: 'pass', metric_value: null, created_at: '2026-06-10T00:00:00Z' },
    ]);
    render(<CheckTrend suiteId="s1" check={expectationCheck} />);
    expect(await screen.findByText('No metric history yet')).toBeInTheDocument();
  });

  it('shows an error when the history fails to load', async () => {
    mockHistory.mockRejectedValue(new Error('boom'));
    render(<CheckTrend suiteId="s1" check={expectationCheck} />);
    expect(await screen.findByText('Failed to load trend')).toBeInTheDocument();
  });

  it('renders without a thresholds caption when the check has none set', async () => {
    mockHistory.mockResolvedValue([
      { run_id: 'r1', status: 'pass', metric_value: 1, created_at: '2026-06-10T00:00:00Z' },
    ]);
    render(<CheckTrend suiteId="s1" check={expectationCheck} />);
    await screen.findByText('Chart');
    expect(screen.queryByText(/Thresholds:/)).not.toBeInTheDocument();
  });

  it('shows a plain-text thresholds caption (a11y mirror of the chart bands) when set', async () => {
    // recharts' SVG doesn't lay out under jsdom (see the module-level note), so
    // this asserts the real DOM text caption rather than the chart's own
    // ReferenceLine labels — which is exactly the a11y fallback #594 requires:
    // a screen reader gets nothing from the SVG either.
    mockHistory.mockResolvedValue([
      { run_id: 'r1', status: 'pass', metric_value: 1, created_at: '2026-06-10T00:00:00Z' },
    ]);
    render(<CheckTrend suiteId="s1" check={thresholdCheck} />);
    expect(
      await screen.findByText('Thresholds: Warn ≥ 1 · Fail ≥ 5 · Critical ≥ 10'),
    ).toBeInTheDocument();
  });

  it('fetches + renders the anomaly baseline overlay when the check kind is anomaly', async () => {
    mockHistory.mockResolvedValue([
      { run_id: 'r1', status: 'pass', metric_value: 0.4, created_at: '2026-06-10T00:00:00Z' },
    ]);
    const baseline: CheckBaseline = {
      kind: 'anomaly',
      captured_at: '2026-06-10T00:00:00Z',
      baseline: {
        version: 1,
        target_metric: 'row_count',
        observations: [
          { ts: '2026-06-08T00:00:00Z', value: 100 },
          { ts: '2026-06-09T00:00:00Z', value: 110 },
          { ts: '2026-06-10T00:00:00Z', value: 90 },
        ],
      },
    };
    mockBaseline.mockResolvedValue(baseline);
    render(<CheckTrend suiteId="s1" check={anomalyCheck} />);

    await vi.waitFor(() => expect(mockBaseline).toHaveBeenCalledWith('s1', 'c1'));
    expect(await screen.findByText(/Anomaly baseline — learned band/)).toBeInTheDocument();
    // The debugger caption surfaces the learned mean/stddev/point-count, and
    // uses the check's own fail_threshold (3) as k rather than the mean±2σ default.
    expect(screen.getByText(/mean ± 3σ/)).toBeInTheDocument();
    expect(screen.getByText(/3 points/)).toBeInTheDocument();
  });

  it('computes the seasonal anomaly band from only the current UTC weekday, mirroring eligible_values', async () => {
    // Pin "now" to a Friday (UTC) so the weekday filter is deterministic. Fake
    // only `Date` (not timers): RTL's `findByText`/`waitFor` polling relies on
    // real `setTimeout`, which faking wholesale would stall against forever.
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-07-31T12:00:00Z'));

    mockHistory.mockResolvedValue([
      { run_id: 'r1', status: 'pass', metric_value: 0.4, created_at: '2026-07-31T00:00:00Z' },
    ]);
    mockBaseline.mockResolvedValue({
      kind: 'anomaly',
      captured_at: '2026-07-31T00:00:00Z',
      baseline: {
        version: 1,
        target_metric: 'row_count',
        window: 2,
        seasonality: true,
        observations: [
          { ts: '2026-07-10T00:00:00Z', value: 110 }, // Friday — dropped by the window=2 slice
          { ts: '2026-07-15T00:00:00Z', value: 9999 }, // Wednesday — must be excluded
          { ts: '2026-07-17T00:00:00Z', value: 130 }, // Friday — kept
          { ts: '2026-07-24T00:00:00Z', value: 150 }, // Friday — kept
          { ts: '2026-07-29T00:00:00Z', value: 8888 }, // Wednesday — must be excluded
        ],
      },
    });
    render(<CheckTrend suiteId="s1" check={anomalyCheck} />);

    // Hand-computed over the Friday-only, last-2 subset [130, 150] — NOT all 5
    // observations, and NOT the naive last-2-of-any-weekday [150, 8888]:
    // mean = 140; sample stddev (n-1) = sqrt(((130-140)^2 + (150-140)^2) / 1) = sqrt(200) ≈ 14.142.
    expect(
      await screen.findByText(/Anomaly baseline — learned band for Fridays/),
    ).toBeInTheDocument();
    expect(screen.getByText(/μ=140\.00/)).toBeInTheDocument();
    expect(screen.getByText(/σ=14\.14/)).toBeInTheDocument();
    expect(screen.getByText(/2 points/)).toBeInTheDocument();
    // The excluded Wednesday value (8888) must not leak into the mean/stddev —
    // if it had, μ would be nowhere near 140.
    expect(screen.queryByText(/μ=3049/)).not.toBeInTheDocument();
  });

  it('shows honest "no observations" copy (never "learned band") when the check has no baseline yet', async () => {
    mockHistory.mockResolvedValue([
      { run_id: 'r1', status: 'pass', metric_value: 0.4, created_at: '2026-06-10T00:00:00Z' },
    ]);
    mockBaseline.mockResolvedValue(null);
    render(<CheckTrend suiteId="s1" check={anomalyCheck} />);

    expect(
      await screen.findByText('Anomaly baseline — no observations captured yet.'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/learned band/)).not.toBeInTheDocument();
  });

  it('shows honest "collecting observations" copy (never "learned band") with only 1 observation', async () => {
    mockHistory.mockResolvedValue([
      { run_id: 'r1', status: 'pass', metric_value: 0.4, created_at: '2026-06-10T00:00:00Z' },
    ]);
    mockBaseline.mockResolvedValue({
      kind: 'anomaly',
      captured_at: '2026-06-10T00:00:00Z',
      baseline: {
        version: 1,
        target_metric: 'row_count',
        window: 14,
        seasonality: false,
        observations: [{ ts: '2026-06-09T00:00:00Z', value: 100 }],
      },
    });
    render(<CheckTrend suiteId="s1" check={anomalyCheck} />);

    expect(
      await screen.findByText(/Anomaly baseline — collecting observations \(1 so far/),
    ).toBeInTheDocument();
    // The dishonest phrasing this replaces was "learned band (mean ± …σ)"
    // unconditionally; that specific claim must not appear here (the copy's own
    // "need at least 2 for a learned band" mention doesn't count — it's the
    // honest disclaimer, not the claim).
    expect(screen.queryByText(/learned band \(mean/)).not.toBeInTheDocument();
  });

  it('does not fetch or render a baseline overlay for a non-anomaly check', async () => {
    mockHistory.mockResolvedValue([
      { run_id: 'r1', status: 'pass', metric_value: 1, created_at: '2026-06-10T00:00:00Z' },
    ]);
    render(<CheckTrend suiteId="s1" check={thresholdCheck} />);
    await screen.findByText('Chart');
    expect(mockBaseline).not.toHaveBeenCalled();
    expect(screen.queryByText(/Anomaly baseline/)).not.toBeInTheDocument();
  });

  it('renders a data-table fallback with run time / metric / status when toggled', async () => {
    mockHistory.mockResolvedValue([
      { run_id: 'r1', status: 'pass', metric_value: 1, created_at: '2026-06-10T00:00:00Z' },
      { run_id: 'r2', status: 'warn', metric_value: 4, created_at: '2026-06-11T00:00:00Z' },
    ]);
    render(<CheckTrend suiteId="s1" check={expectationCheck} />);
    await screen.findByText('Chart');

    const { default: userEvent } = await import('@testing-library/user-event');
    const user = userEvent.setup();
    await user.click(screen.getByText('Table'));

    const table = await screen.findByRole('table');
    expect(within(table).getByText('Run time')).toBeInTheDocument();
    expect(within(table).getByText('Metric')).toBeInTheDocument();
    expect(within(table).getByText('Status')).toBeInTheDocument();
    // Newest-first: row for r2 (warn, metric 4) appears before r1's.
    const rows = within(table).getAllByRole('row');
    expect(within(rows[1]).getByText('warn')).toBeInTheDocument();
    expect(within(rows[1]).getByText('4')).toBeInTheDocument();
    expect(within(rows[2]).getByText('pass')).toBeInTheDocument();
  });

  it('table fallback also lists anomaly baseline observations when present', async () => {
    mockHistory.mockResolvedValue([
      { run_id: 'r1', status: 'pass', metric_value: 0.2, created_at: '2026-06-10T00:00:00Z' },
    ]);
    mockBaseline.mockResolvedValue({
      kind: 'anomaly',
      captured_at: '2026-06-10T00:00:00Z',
      baseline: {
        version: 1,
        target_metric: 'row_count',
        observations: [{ ts: '2026-06-09T00:00:00Z', value: 100 }],
      },
    });
    render(<CheckTrend suiteId="s1" check={anomalyCheck} />);
    await screen.findByText('Chart');

    const { default: userEvent } = await import('@testing-library/user-event');
    const user = userEvent.setup();
    await user.click(screen.getByText('Table'));

    expect(
      await screen.findByText('Anomaly baseline observations (raw measurements)'),
    ).toBeInTheDocument();
    expect(screen.getByText('Observed at')).toBeInTheDocument();
    expect(screen.getByText('100')).toBeInTheDocument();
  });
});
