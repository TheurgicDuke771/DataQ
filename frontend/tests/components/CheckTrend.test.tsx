import { App as AntApp } from 'antd';
import { render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  type CheckBaseline,
  type CheckResultPoint,
  getCheckBaseline,
  listCheckHistory,
} from '../../src/api/suites';
import {
  type DetectedSeasonalPeriods,
  getDetectedSeasonalPeriods,
  listSeasonalPeriods,
  promoteDetectedPeriod,
  type SeasonalPeriod,
} from '../../src/api/seasonalPeriods';
import { CheckTrend } from '../../src/components/checks/CheckTrend';
import { WithMe } from '../support/me';

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, listCheckHistory: vi.fn(), getCheckBaseline: vi.fn() };
});

vi.mock('../../src/api/seasonalPeriods', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/seasonalPeriods')>();
  return {
    ...actual,
    getDetectedSeasonalPeriods: vi.fn(),
    listSeasonalPeriods: vi.fn(),
    promoteDetectedPeriod: vi.fn(),
  };
});

const mockHistory = vi.mocked(listCheckHistory);
const mockBaseline = vi.mocked(getCheckBaseline);
const mockDetected = vi.mocked(getDetectedSeasonalPeriods);
const mockCalendar = vi.mocked(listSeasonalPeriods);
const mockPromote = vi.mocked(promoteDetectedPeriod);

const NO_DETECTION: DetectedSeasonalPeriods = {
  reason: null,
  periods: [],
  thresholds: { ratio: 1.5, min_consecutive: 2, same_time_days: 7, level: 'median' },
  history: {
    unit: 'day',
    points: 0,
    backfilled: 0,
    evaluated: 0,
    first: null,
    last: null,
    years: 0,
  },
};

beforeEach(() => {
  mockDetected.mockResolvedValue(NO_DETECTION);
  mockCalendar.mockResolvedValue([]);
});

// recharts' SVG doesn't lay out under jsdom (zero-size container, same lesson as
// QualityTrends.test.tsx) — these assert the chrome, empty/error states.

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
    // recharts' SVG doesn't lay out under jsdom (see the module-level note), so this asserts the
    // real DOM text caption rather than the chart's own ReferenceLine labels.
    mockHistory.mockResolvedValue([
      { run_id: 'r1', status: 'pass', metric_value: 1, created_at: '2026-06-10T00:00:00Z' },
    ]);
    render(<CheckTrend suiteId="s1" check={thresholdCheck} />);
    expect(
      await screen.findByText('Thresholds: Warn ≥ 1 · Fail ≥ 5 · Critical ≥ 10'),
    ).toBeInTheDocument();
  });

  it('draws an aggregate check its two-sided config bands, not the empty threshold columns', async () => {
    mockHistory.mockResolvedValue([
      { run_id: 'r1', status: 'pass', metric_value: 42, created_at: '2026-06-10T00:00:00Z' },
    ]);
    const aggregateCheck = {
      id: 'c1',
      kind: 'aggregate',
      warn_threshold: null,
      fail_threshold: null,
      critical_threshold: null,
      config: { aggregate: 'mean', column: 'amount', min_value: 10, warn_max: 80, max_value: 90 },
    };
    render(<CheckTrend suiteId="s1" check={aggregateCheck} />);
    expect(
      await screen.findByText('Thresholds: Fail < 10 · Warn > 80 · Fail > 90'),
    ).toBeInTheDocument();
    expect(mockBaseline).not.toHaveBeenCalled();
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
    // Pin "now" to a Friday (UTC) so the weekday filter is deterministic.
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

    // Hand-computed over the Friday-only, last-2 subset [130, 150] — NOT all 5 observations, and
    // NOT the naive last-2-of-any-weekday [150, 8888]: mean = 140.
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

  it('draws a per-period check’s seasonal band for the next period’s weekday, not today’s', async () => {
    // Today is a Friday; the last stored point is Wednesday the 29th, so the next run judges
    // Thursday the 30th and is scored against Thursdays.
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
        target_metric: 'period_row_count',
        window: 2,
        seasonality: true,
        observations: [
          { ts: '2026-07-16T00:00:00Z', value: 130 }, // Thursday — kept
          { ts: '2026-07-17T00:00:00Z', value: 9999 }, // Friday — today’s weekday, must be excluded
          { ts: '2026-07-23T00:00:00Z', value: 150 }, // Thursday — kept
          { ts: '2026-07-24T00:00:00Z', value: 8888 }, // Friday — must be excluded
          { ts: '2026-07-29T00:00:00Z', value: 7777 }, // Wednesday — the last stored period
        ].map((o, i) => (i < 3 ? { ...o, source: 'backfilled' } : o)),
      },
    });
    render(
      <CheckTrend
        suiteId="s1"
        check={{
          ...anomalyCheck,
          config: { target_metric: 'period_row_count', date_column: 'ordered_at' },
        }}
      />,
    );

    expect(
      await screen.findByText(/Anomaly baseline — learned band for Thursdays/),
    ).toBeInTheDocument();
    expect(screen.getByText(/μ=140\.00/)).toBeInTheDocument();
    // Three of the five were read from the table on the first run, not measured by a run.
    expect(
      screen.getByText(/3 of the 5 stored points were seeded from the table’s own rows/),
    ).toBeInTheDocument();
  });

  it('shows the recent end of a three-year seeded baseline and marks which points were seeded', async () => {
    const start = Date.UTC(2023, 9, 13);
    const observations = Array.from({ length: 400 }, (_, i) => ({
      ts: new Date(start + i * 86_400_000).toISOString(),
      value: 100,
      ...(i < 399 ? { source: 'backfilled' } : {}),
    }));
    mockHistory.mockResolvedValue([
      { run_id: 'r1', status: 'pass', metric_value: 0.4, created_at: '2026-07-31T00:00:00Z' },
    ]);
    mockBaseline.mockResolvedValue({
      kind: 'anomaly',
      captured_at: '2026-07-31T00:00:00Z',
      baseline: { version: 1, target_metric: 'period_row_count', window: 14, observations },
    });
    render(
      <CheckTrend
        suiteId="s1"
        check={{
          ...anomalyCheck,
          config: { target_metric: 'period_row_count', date_column: 'ordered_at', yearly: true },
        }}
      />,
    );

    // No period has a result, so there is no scored-against band to draw or describe.
    expect(await screen.findByText(/No shown period has a result yet/)).toBeInTheDocument();
    expect(
      await screen.findByText(
        /399 of the 400 stored points were seeded.*the chart shows the most recent 180/,
      ),
    ).toBeInTheDocument();

    const { default: userEvent } = await import('@testing-library/user-event');
    await userEvent.setup().click(screen.getByText('Table'));
    expect(await screen.findByText('Source')).toBeInTheDocument();
    // Newest first: the one point a run measured leads, the rest of the page is seeded.
    expect(screen.getAllByText('Measured by a run')).toHaveLength(1);
    expect(screen.getAllByText('Seeded from the table')).toHaveLength(49);
  });

  it('says a column-profile baseline is per column, never "no observations captured yet"', async () => {
    mockHistory.mockResolvedValue([
      { run_id: 'r1', status: 'pass', metric_value: 1.2, created_at: '2026-06-10T00:00:00Z' },
    ]);
    mockBaseline.mockResolvedValue({
      kind: 'anomaly',
      captured_at: '2026-06-08T00:00:00Z',
      baseline: {
        version: 1,
        target_metric: 'column_profile',
        observations: [
          { ts: '2026-06-09T00:00:00Z', values: { 'email\u001fnull_pct': 1 } },
          { ts: '2026-06-10T00:00:00Z', values: { 'email\u001fnull_pct': 2 } },
        ],
      },
    });
    render(<CheckTrend suiteId="s1" check={anomalyCheck} />);

    expect(await screen.findByTestId('column-profile-baseline-note')).toHaveTextContent(
      '2 run(s) recorded',
    );
    expect(screen.queryByText(/no observations captured yet/)).not.toBeInTheDocument();
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
    // The dishonest phrasing this replaces was "learned band (mean ± …σ)" unconditionally.
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

    expect(await screen.findByText('Anomaly baseline points, newest first')).toBeInTheDocument();
    expect(screen.getByText('Dated')).toBeInTheDocument();
    expect(screen.getByText('100')).toBeInTheDocument();
  });
});

describe('CheckTrend — per-period seasonal overlays', () => {
  const day = (i: number) => new Date(Date.UTC(2026, 10, 20 + i)).toISOString();
  const yearlyCheck = {
    ...anomalyCheck,
    config: {
      target_metric: 'period_row_count',
      date_column: 'ordered_at',
      yearly: true,
      seasonal_periods: ['bf'],
    },
  };
  const BLACK_FRIDAY: SeasonalPeriod = {
    id: 'bf',
    name: 'Black Friday',
    kind: 'recurring',
    start_month: 11,
    start_day: 27,
    end_month: 11,
    end_day: 30,
    start_date: null,
    end_date: null,
    series: null,
    note: null,
    attached_checks: 1,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };

  function seed() {
    mockBaseline.mockResolvedValue({
      kind: 'anomaly',
      captured_at: day(0),
      baseline: {
        version: 1,
        target_metric: 'period_row_count',
        window: 14,
        observations: [0, 1, 2, 3, 4, 5, 6, 7].map((i) => ({
          ts: day(i),
          value: i === 7 ? 5100 : 1000 + i,
          ...(i < 5 ? { source: 'backfilled' } : {}),
        })),
      },
    });
    mockHistory.mockResolvedValue([
      {
        run_id: 'r6',
        status: 'pass',
        metric_value: 0.1,
        created_at: day(7),
        seasonal: {
          period_start: day(6).slice(0, 10),
          mean: 1003,
          stddev: 10,
          yearly_applied: false,
          yearly_reason: 'no_earlier_year',
          expected: null,
          declared_name: null,
          banded: true,
          verdict_changed: false,
        },
      },
      {
        run_id: 'r7',
        status: 'pass',
        metric_value: 0.4,
        created_at: day(8),
        seasonal: {
          period_start: day(7).slice(0, 10),
          mean: 5050,
          stddev: 125,
          yearly_applied: true,
          yearly_reason: null,
          expected: 5000,
          declared_name: 'Black Friday',
          banded: true,
          verdict_changed: true,
        },
      },
    ]);
  }

  it('reads 180 results so every plotted period can show what it was scored against', async () => {
    seed();
    render(<CheckTrend suiteId="s1" check={yearlyCheck} />);
    await screen.findByTestId('seasonal-caption');
    expect(mockHistory).toHaveBeenCalledWith('s1', 'c1', 180);
    expect(mockDetected).toHaveBeenCalledWith('s1', 'c1');
  });

  it('says the band follows the expected value where yearly applied, and why it did not elsewhere', async () => {
    seed();
    mockCalendar.mockResolvedValue([BLACK_FRIDAY]);
    render(<CheckTrend suiteId="s1" check={yearlyCheck} />);

    const caption = await screen.findByTestId('seasonal-caption');
    expect(caption).toHaveTextContent(/Where the yearly comparison applied \(1 of 2\)/);
    expect(caption).toHaveTextContent(/centred on the expected value, the dashed line/);
    expect(caption).toHaveTextContent(
      /Not applied on 1, so those bands are the recent window.*no earlier year in the history \(1\)/,
    );
    expect(caption).toHaveTextContent(/attached calendar entries \(Black Friday\)/);
    expect(caption).toHaveTextContent(/Hollow points were seeded/);
    // The superseded claim that a yearly band is only ever the recent window is gone.
    expect(screen.queryByText(/this band is the recent window only/)).not.toBeInTheDocument();
  });

  it('describes a non-yearly per-period band as the recent window each period was scored against', async () => {
    seed();
    render(
      <CheckTrend
        suiteId="s1"
        check={{ ...anomalyCheck, config: { target_metric: 'period_sum', date_column: 'd' } }}
      />,
    );
    const caption = await screen.findByTestId('seasonal-caption');
    expect(caption).toHaveTextContent(/the recent window it was scored against, mean ± 3σ/);
    expect(caption).not.toHaveTextContent(/expected value/);
  });

  it('says when a declared period withheld the band', async () => {
    seed();
    mockHistory.mockResolvedValue([
      {
        run_id: 'r7',
        status: 'skip',
        metric_value: null,
        created_at: day(8),
        seasonal: {
          period_start: day(7).slice(0, 10),
          mean: 1000,
          stddev: 10,
          yearly_applied: true,
          yearly_reason: null,
          expected: 1000,
          declared_name: 'Launch',
          banded: false,
          verdict_changed: true,
        },
      },
    ]);
    render(<CheckTrend suiteId="s1" check={yearlyCheck} />);
    expect(await screen.findByTestId('seasonal-caption')).toHaveTextContent(
      /1 judged period fell inside a declared period with no earlier occurrence/,
    );
  });

  it('lists the scored-against band, expected value and declared period in the table view', async () => {
    seed();
    const { default: userEvent } = await import('@testing-library/user-event');
    render(<CheckTrend suiteId="s1" check={yearlyCheck} />);
    await screen.findByTestId('seasonal-caption');
    await userEvent.setup().click(screen.getByText('Table'));
    expect(await screen.findByText('Scored against (centre ± spread)')).toBeInTheDocument();
    expect(screen.getByText('5050.00 ± 125.00')).toBeInTheDocument();
    expect(screen.getByText('5000.00')).toBeInTheDocument();
    expect(screen.getByText('not applied: no earlier year in the history')).toBeInTheDocument();
    expect(screen.getAllByText('Black Friday').length).toBeGreaterThan(0);
  });

  const DETECTED: DetectedSeasonalPeriods = {
    ...NO_DETECTION,
    periods: [
      {
        kind: 'recurring',
        start: '2026-11-27',
        end: '2026-11-30',
        peak_ratio: 5.1,
        years_seen: 3,
        range: { start_month: 11, start_day: 26, end_month: 11, end_day: 30 },
        occurrences: [
          {
            year: 2026,
            start: '2026-11-27',
            end: '2026-11-30',
            peak_ratio: 5.1,
            periods: 4,
            backfilled_periods: 0,
          },
        ],
        declared: [],
      },
    ],
  };

  it('offers an admin to promote a detected period and attaches it to a yearly check', async () => {
    seed();
    mockDetected.mockResolvedValue(DETECTED);
    mockPromote.mockResolvedValue({ ...BLACK_FRIDAY, id: 'new' });
    const onCheckChanged = vi.fn();
    const { default: userEvent } = await import('@testing-library/user-event');
    const user = userEvent.setup();
    render(
      <AntApp>
        <WithMe role="admin">
          <CheckTrend suiteId="s1" check={yearlyCheck} onCheckChanged={onCheckChanged} />
        </WithMe>
      </AntApp>,
    );
    expect(await screen.findByText('Recurring, 3 years')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Promote to calendar' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('checkbox', { name: /Also attach/ })).toBeChecked();
    await user.type(within(dialog).getByLabelText('Name'), 'Black Friday');
    await user.click(within(dialog).getByRole('button', { name: 'Add to calendar' }));
    await vi.waitFor(() =>
      expect(mockPromote).toHaveBeenCalledWith({
        suite_id: 's1',
        check_id: 'c1',
        start: '2026-11-27',
        name: 'Black Friday',
        series: null,
        note: null,
        attach: true,
      }),
    );
    await vi.waitFor(() => expect(onCheckChanged).toHaveBeenCalled());
  });

  it('does not attach when the check has no yearly seasonality, and says why', async () => {
    seed();
    mockDetected.mockResolvedValue(DETECTED);
    mockPromote.mockResolvedValue({ ...BLACK_FRIDAY, id: 'new' });
    const onCheckChanged = vi.fn();
    const { default: userEvent } = await import('@testing-library/user-event');
    const user = userEvent.setup();
    render(
      <AntApp>
        <WithMe role="admin">
          <CheckTrend
            suiteId="s1"
            check={{ ...anomalyCheck, config: { target_metric: 'period_sum', date_column: 'd' } }}
            onCheckChanged={onCheckChanged}
          />
        </WithMe>
      </AntApp>,
    );
    await user.click(await screen.findByRole('button', { name: 'Promote to calendar' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('checkbox', { name: /Also attach/ })).toBeDisabled();
    expect(within(dialog).getByText(/Attaching needs yearly seasonality/)).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText('Name'), 'Spring');
    await user.click(within(dialog).getByRole('button', { name: 'Add to calendar' }));
    await vi.waitFor(() =>
      expect(mockPromote).toHaveBeenCalledWith(expect.objectContaining({ attach: false })),
    );
    expect(onCheckChanged).not.toHaveBeenCalled();
  });

  it('shows detected periods to a member without the promote action', async () => {
    seed();
    mockDetected.mockResolvedValue(DETECTED);
    render(
      <WithMe role="member">
        <CheckTrend suiteId="s1" check={yearlyCheck} />
      </WithMe>,
    );
    expect(await screen.findByText('Recurring, 3 years')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Promote to calendar' })).not.toBeInTheDocument();
  });

  it('does not offer to promote a period an attached entry already covers', async () => {
    seed();
    mockDetected.mockResolvedValue({
      ...DETECTED,
      periods: [{ ...DETECTED.periods[0], declared: [{ id: 'bf', name: 'Black Friday' }] }],
    });
    render(
      <WithMe role="admin">
        <CheckTrend suiteId="s1" check={yearlyCheck} />
      </WithMe>,
    );
    expect(await screen.findByText(/Covered by Black Friday on the calendar/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Promote to calendar' })).not.toBeInTheDocument();
  });

  it('never asks a whole-table anomaly check for detected periods', async () => {
    mockHistory.mockResolvedValue([]);
    mockBaseline.mockResolvedValue(null);
    render(
      <CheckTrend
        suiteId="s1"
        check={{ ...anomalyCheck, config: { target_metric: 'row_count' } }}
      />,
    );
    await screen.findByText(/no observations captured yet/);
    expect(mockDetected).not.toHaveBeenCalled();
    expect(mockCalendar).not.toHaveBeenCalled();
  });
});
