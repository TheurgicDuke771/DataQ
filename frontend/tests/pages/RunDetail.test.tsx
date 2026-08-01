import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { getRun, type RunDetail as RunDetailType } from '../../src/api/runs';
import { type Check, type Suite, getSuite, listChecks } from '../../src/api/suites';
import { RunDetail } from '../../src/pages/RunDetail';
import { downloadCsv, downloadJson } from '../../src/utils/download';

vi.mock('../../src/api/runs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/runs')>();
  return { ...actual, getRun: vi.fn() };
});

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, getSuite: vi.fn(), listChecks: vi.fn() };
});

vi.mock('../../src/utils/download', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/utils/download')>();
  return { ...actual, downloadCsv: vi.fn(), downloadJson: vi.fn() };
});

const mockGetRun = vi.mocked(getRun);
const mockGetSuite = vi.mocked(getSuite);
const mockListChecks = vi.mocked(listChecks);

const suite: Suite = {
  id: 's1',
  name: 'Orders quality',
  description: null,
  connection_id: 'c1',
  target: { table: 'ORDERS' },
  created_by: 'u1',
};

const check: Check = {
  id: 'chk1',
  suite_id: 's1',
  name: 'order_id not null',
  kind: 'expectation',
  expectation_type: 'expect_column_values_to_not_be_null',
  config: { column: 'order_id' },
  warn_threshold: null,
  fail_threshold: null,
  critical_threshold: null,
  alert_snoozed_until: null,
};

const runDetail: RunDetailType = {
  id: 'r1',
  suite_id: 's1',
  status: 'succeeded',
  triggered_by: 'manual:u1',
  started_at: '2026-06-11T00:00:00Z',
  finished_at: '2026-06-11T00:00:12Z',
  created_at: '2026-06-11T00:00:00Z',
  checks_total: 1,
  checks_passed: 0,
  worst_severity: 'warn',
  failure_reason: null,
  results: [
    {
      id: 'res1',
      check_id: 'chk1',
      status: 'warn',
      metric_value: 2,
      duration_ms: null,
      observed_value: { unexpected_percent: 2 },
      expected_value: null,
      // Redacted at the API boundary (#226): counts kept, cell values masked.
      sample_failures: {
        unexpected_count: 2,
        unexpected_percent: 2,
        partial_unexpected_list: [{ order_id: '<redacted>' }, { order_id: '<redacted>' }],
      },
      // Every column masked (#424) — the base fixture's sample above.
      redaction: 'full',
      redacted_columns: ['order_id'],
    },
  ],
};

function renderAt(runId: string) {
  return render(
    <MemoryRouter initialEntries={[`/results/${runId}`]}>
      <Routes>
        <Route path="/results/:runId" element={<RunDetail />} />
        <Route path="/results" element={<div>results-list</div>} />
        <Route path="/assets/:assetId" element={<div>asset page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

/**
 * Scoped query bound to the interactive on-screen region (`data-testid`
 * `rd-screen`) — since the print-only `RunReport` (#345) renders a parallel
 * copy of the suite name / check names / statuses, plain `screen.getByText`
 * now matches twice for anything the report also shows. Tests asserting on
 * the *interactive page* scope through this helper; tests asserting on the
 * *report* scope through `screen.findByTestId('run-report')` instead. The
 * `rd-screen` wrapper itself renders synchronously (loading/error/ok are all
 * inside it), so this needs no `await` — callers still `await` the first
 * `findBy*` query on the returned bindings for the data to land.
 */
function screenRegion() {
  return within(screen.getByTestId('rd-screen'));
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('RunDetail page', () => {
  it('loads the run by id and renders its per-check results', async () => {
    mockGetRun.mockResolvedValue(runDetail);
    mockGetSuite.mockResolvedValue(suite);
    mockListChecks.mockResolvedValue([check]);

    renderAt('r1');
    const region = screenRegion();

    expect(await region.findByText('Orders quality')).toBeInTheDocument();
    // check_id → name + expectation + severity tag.
    expect(region.getByText('order_id not null')).toBeInTheDocument();
    expect(region.getByText('expect_column_values_to_not_be_null')).toBeInTheDocument();
    expect(region.getByText('warn')).toBeInTheDocument();
    // Checks-passed stat: 0 of 1 passed (the one result is a warn).
    expect(region.getByText('0 / 1')).toBeInTheDocument();
    expect(mockGetRun).toHaveBeenCalledWith('r1');
  });

  it('surfaces an Asset link that navigates to the asset (#773)', async () => {
    mockGetRun.mockResolvedValue({ ...runDetail, asset_id: 'asset-9' });
    mockGetSuite.mockResolvedValue(suite);
    mockListChecks.mockResolvedValue([check]);
    renderAt('r1');
    const user = userEvent.setup();

    await user.click(await screen.findByText('Asset'));
    expect(await screen.findByText('asset page')).toBeInTheDocument();
  });

  it('omits the Asset link when the run has no resolved asset (#773)', async () => {
    mockGetRun.mockResolvedValue({ ...runDetail, asset_id: null });
    mockGetSuite.mockResolvedValue(suite);
    mockListChecks.mockResolvedValue([check]);
    renderAt('r1');
    const region = screenRegion();
    expect(await region.findByText('Orders quality')).toBeInTheDocument();
    expect(region.queryByText('Asset')).not.toBeInTheDocument();
  });

  it('surfaces the failure reason for a failed run (#605)', async () => {
    mockGetRun.mockResolvedValue({
      ...runDetail,
      status: 'failed',
      failure_reason: 'The datasource rejected the credentials, or a required grant is missing.',
      results: [],
    });
    mockGetSuite.mockResolvedValue(suite);
    mockListChecks.mockResolvedValue([check]);

    renderAt('r1');
    const region = screenRegion();

    expect(await region.findByText('This run failed to execute')).toBeInTheDocument();
    expect(region.getByText(/The datasource rejected the credentials/)).toBeInTheDocument();
  });

  it('marks a snoozed check in the results table (#653 — triage surface)', async () => {
    mockGetRun.mockResolvedValue(runDetail);
    mockGetSuite.mockResolvedValue(suite);
    mockListChecks.mockResolvedValue([{ ...check, alert_snoozed_until: '2099-01-01T00:00:00Z' }]);

    renderAt('r1');
    const region = screenRegion();

    expect(await region.findByText('order_id not null')).toBeInTheDocument();
    expect(region.getByText(/Snoozed until/)).toBeInTheDocument();
  });

  it('still renders when the suite name and checks fail to load', async () => {
    mockGetRun.mockResolvedValue(runDetail);
    mockGetSuite.mockRejectedValue(new Error('forbidden'));
    mockListChecks.mockRejectedValue(new Error('forbidden'));

    renderAt('r1');
    const region = screenRegion();

    // Falls back to a suite-id stub heading; the result row still shows (by id).
    await waitFor(() => expect(region.getByText('warn')).toBeInTheDocument());
  });

  it('shows an error when the run fails to load', async () => {
    mockGetRun.mockRejectedValue(new Error('boom'));
    renderAt('rX');
    // #910: dedicated error page, not the old inline alert. A plain Error is a
    // CLIENT failure → 500; only a real network failure claims 503 (#930 review).
    expect(await screen.findByText('500 — Something went wrong')).toBeInTheDocument();
  });

  it('exports the run results as CSV with check names resolved', async () => {
    mockGetRun.mockResolvedValue(runDetail);
    mockGetSuite.mockResolvedValue(suite);
    mockListChecks.mockResolvedValue([check]);
    renderAt('r1');
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /download/i }));
    await user.click(await screen.findByText('Download CSV'));

    expect(downloadCsv).toHaveBeenCalledTimes(1);
    const [filename, headers, rows] = vi.mocked(downloadCsv).mock.calls[0];
    expect(filename).toBe('orders_quality_run_r1.csv');
    expect(headers).toEqual(['check', 'expectation', 'status', 'metric_value', 'observed']);
    // check_id → name, observed scalar JSON-stringified.
    expect(rows[0]).toEqual([
      'order_id not null',
      'expect_column_values_to_not_be_null',
      'warn',
      2,
      '{"unexpected_percent":2}',
    ]);
  });

  it('surfaces the redacted failing-row sample in a check’s expanded row', async () => {
    mockGetRun.mockResolvedValue(runDetail);
    mockGetSuite.mockResolvedValue(suite);
    mockListChecks.mockResolvedValue([check]);
    renderAt('r1');
    const region = screenRegion();
    const user = userEvent.setup();

    await region.findByText('order_id not null');
    await user.click(region.getByRole('button', { name: /expand row/i }));

    // Count is surfaced; the masked cell value shows the shape, not real data.
    expect(await region.findByText(/Failing rows/)).toBeInTheDocument();
    expect(region.getByText(/2 rows/)).toBeInTheDocument();
    expect(region.getAllByText('<redacted>').length).toBeGreaterThan(0);
    // #424: every column masked -> the header must say so, honestly.
    expect(region.getByText(/values redacted/)).toBeInTheDocument();
  });

  // -- #424: the sample header must match the actual per-column redaction state --
  // Scoped to the on-screen region throughout (`screenRegion()`) -- since the
  // print-only `RunReport` (#345) also renders the check name "order_id not
  // null", the unscoped `screen.findByText` these started as would now match
  // twice; the redaction-label assertions after each expand aren't duplicated
  // (the report omits samples entirely) but stay scoped for consistency.

  it('says "values shown" when the API reports no columns were redacted', async () => {
    mockGetRun.mockResolvedValue({
      ...runDetail,
      results: [
        {
          ...runDetail.results[0],
          sample_failures: {
            unexpected_count: 2,
            partial_unexpected_list: [-12.5, -5.0],
          },
          redaction: 'none',
          redacted_columns: [],
        },
      ],
    });
    mockGetSuite.mockResolvedValue(suite);
    mockListChecks.mockResolvedValue([check]);
    renderAt('r1');
    const region = screenRegion();
    const user = userEvent.setup();

    await region.findByText('order_id not null');
    await user.click(region.getByRole('button', { name: /expand row/i }));

    expect(await region.findByText(/Failing rows/)).toBeInTheDocument();
    expect(region.getByText(/values shown/)).toBeInTheDocument();
    expect(region.queryByText(/values redacted/)).not.toBeInTheDocument();
  });

  it('names the redacted columns when the API reports a partial mix', async () => {
    mockGetRun.mockResolvedValue({
      ...runDetail,
      results: [
        {
          ...runDetail.results[0],
          sample_failures: {
            unexpected_index_list: [{ order_id: 'ORD-1', email: '<redacted>' }],
          },
          redaction: 'partial',
          redacted_columns: ['email'],
        },
      ],
    });
    mockGetSuite.mockResolvedValue(suite);
    mockListChecks.mockResolvedValue([check]);
    renderAt('r1');
    const region = screenRegion();
    const user = userEvent.setup();

    await region.findByText('order_id not null');
    await user.click(region.getByRole('button', { name: /expand row/i }));

    expect(await region.findByText(/Failing rows/)).toBeInTheDocument();
    expect(region.getByText(/1 column redacted/)).toBeInTheDocument();
    expect(region.queryByText(/values redacted/)).not.toBeInTheDocument();
  });

  it('falls back to "partially redacted" when partial has no nameable column (#1115)', async () => {
    // Reachable when an anonymous mask (a scalar partial_unexpected_list with no
    // tested_column) coincides with some other column being shown: the tracker
    // reports "partial" but has no column name to attribute the mask to, so
    // redacted_columns is empty. "0 columns redacted" would be false-adjacent.
    mockGetRun.mockResolvedValue({
      ...runDetail,
      results: [
        {
          ...runDetail.results[0],
          sample_failures: {
            unexpected_index_list: [{ order_id: 'ORD-1' }],
            partial_unexpected_list: ['a@x.com'],
          },
          redaction: 'partial',
          redacted_columns: [],
        },
      ],
    });
    mockGetSuite.mockResolvedValue(suite);
    mockListChecks.mockResolvedValue([check]);
    renderAt('r1');
    const region = screenRegion();
    const user = userEvent.setup();

    await region.findByText('order_id not null');
    await user.click(region.getByRole('button', { name: /expand row/i }));

    expect(await region.findByText(/Failing rows/)).toBeInTheDocument();
    expect(region.getByText(/partially redacted/)).toBeInTheDocument();
    expect(region.queryByText(/column.*redacted/)).not.toBeInTheDocument();
    expect(region.queryByText(/^values redacted$/)).not.toBeInTheDocument();
    expect(region.queryByText(/values shown/)).not.toBeInTheDocument();
  });

  it('omits any redaction claim when the sample has no data-bearing content', async () => {
    mockGetRun.mockResolvedValue({
      ...runDetail,
      results: [
        {
          ...runDetail.results[0],
          sample_failures: { unexpected_count: 3, unexpected_percent: 12.5 },
          redaction: null,
          redacted_columns: [],
        },
      ],
    });
    mockGetSuite.mockResolvedValue(suite);
    mockListChecks.mockResolvedValue([check]);
    renderAt('r1');
    const region = screenRegion();
    const user = userEvent.setup();

    await region.findByText('order_id not null');
    await user.click(region.getByRole('button', { name: /expand row/i }));

    expect(await region.findByText(/Failing rows/)).toBeInTheDocument();
    expect(region.queryByText(/values redacted/)).not.toBeInTheDocument();
    expect(region.queryByText(/values shown/)).not.toBeInTheDocument();
    expect(region.queryByText(/column.*redacted/)).not.toBeInTheDocument();
  });

  it('exports the run as JSON (failing-row sample omitted from the payload)', async () => {
    mockGetRun.mockResolvedValue(runDetail);
    mockGetSuite.mockResolvedValue(suite);
    mockListChecks.mockResolvedValue([check]);
    renderAt('r1');
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /download/i }));
    await user.click(await screen.findByText('Download JSON'));

    expect(downloadJson).toHaveBeenCalledTimes(1);
    const [filename, payload] = vi.mocked(downloadJson).mock.calls[0];
    expect(filename).toBe('orders_quality_run_r1.json');
    const body = payload as { run: { suite_name: string }; checks: unknown[] };
    expect(body.run.suite_name).toBe('Orders quality');
    expect(body.checks).toHaveLength(1);
  });

  // ── PDF report export (#345) ───────────────────────────────────────────
  describe('PDF report export (#345)', () => {
    it('offers a Print / Save as PDF option in the download menu', async () => {
      mockGetRun.mockResolvedValue(runDetail);
      mockGetSuite.mockResolvedValue(suite);
      mockListChecks.mockResolvedValue([check]);
      renderAt('r1');
      const user = userEvent.setup();

      await user.click(await screen.findByRole('button', { name: /download/i }));

      expect(await screen.findByText('Print / Save as PDF')).toBeInTheDocument();
    });

    it('invokes window.print() — the browser IS the PDF export, zero new deps', async () => {
      mockGetRun.mockResolvedValue(runDetail);
      mockGetSuite.mockResolvedValue(suite);
      mockListChecks.mockResolvedValue([check]);
      const printSpy = vi.fn();
      vi.stubGlobal('print', printSpy);
      renderAt('r1');
      const user = userEvent.setup();

      await user.click(await screen.findByRole('button', { name: /download/i }));
      await user.click(await screen.findByText('Print / Save as PDF'));

      expect(printSpy).toHaveBeenCalledTimes(1);
      vi.unstubAllGlobals();
    });

    it('disables Print / Save as PDF alongside CSV/JSON when the run has no results', async () => {
      mockGetRun.mockResolvedValue({ ...runDetail, results: [] });
      mockGetSuite.mockResolvedValue(suite);
      mockListChecks.mockResolvedValue([check]);
      renderAt('r1');

      // The whole Dropdown trigger is disabled (same gate as CSV/JSON) — no
      // menu opens, so "Print / Save as PDF" never renders.
      expect(await screen.findByRole('button', { name: /download/i })).toBeDisabled();
    });

    it('renders the print-only report with the run meta + per-check table', async () => {
      mockGetRun.mockResolvedValue(runDetail);
      mockGetSuite.mockResolvedValue(suite);
      mockListChecks.mockResolvedValue([check]);
      renderAt('r1');

      const report = await screen.findByTestId('run-report');
      // Suite header, run meta, and the per-check row all present — the report
      // renders unconditionally (hidden by print CSS, not by React) so
      // `window.print()` has no async data-fetch to race.
      expect(within(report).getByText('Orders quality')).toBeInTheDocument();
      expect(within(report).getByText(/Run r1/)).toBeInTheDocument();
      expect(within(report).getByText('manual:u1')).toBeInTheDocument();
      expect(within(report).getByText('order_id not null')).toBeInTheDocument();
      expect(within(report).getByText('expect_column_values_to_not_be_null')).toBeInTheDocument();
      expect(within(report).getByText('warn')).toBeInTheDocument();
      expect(within(report).getByText('2')).toBeInTheDocument();
    });

    it('marks a snoozed check with a print-friendly "(snoozed)" suffix (#653 parity)', async () => {
      mockGetRun.mockResolvedValue(runDetail);
      mockGetSuite.mockResolvedValue(suite);
      mockListChecks.mockResolvedValue([{ ...check, alert_snoozed_until: '2099-01-01T00:00:00Z' }]);
      renderAt('r1');

      const report = await screen.findByTestId('run-report');
      // The interactive table gets a <SnoozedTag> Tag/Tooltip beside the check
      // name; a Tag doesn't survive print, so the report gets the same signal
      // as plain text instead of silently dropping it.
      expect(within(report).getByText('order_id not null (snoozed)')).toBeInTheDocument();
    });

    it('omits the "(snoozed)" suffix for a check that is not currently snoozed', async () => {
      mockGetRun.mockResolvedValue(runDetail);
      mockGetSuite.mockResolvedValue(suite);
      // Explicit null (not snoozed) and a lapsed-in-the-past snooze both count
      // as "not currently snoozed" per `isSnoozed` (#370).
      mockListChecks.mockResolvedValue([{ ...check, alert_snoozed_until: '2000-01-01T00:00:00Z' }]);
      renderAt('r1');

      const report = await screen.findByTestId('run-report');
      expect(within(report).getByText('order_id not null')).toBeInTheDocument();
      expect(within(report).queryByText(/\(snoozed\)/)).not.toBeInTheDocument();
    });

    it('em-dashes a null triggered_by / metric_value in the report', async () => {
      mockGetRun.mockResolvedValue({
        ...runDetail,
        triggered_by: null,
        results: [{ ...runDetail.results[0], metric_value: null }],
      });
      mockGetSuite.mockResolvedValue(suite);
      mockListChecks.mockResolvedValue([check]);
      renderAt('r1');

      const report = await screen.findByTestId('run-report');
      const emDashes = within(report).getAllByText('—');
      // One for "Triggered by", one for the check row's "Metric" cell.
      expect(emDashes.length).toBeGreaterThanOrEqual(2);
    });

    it('omits sample failing rows from the report (redaction parity with CSV/JSON, #226)', async () => {
      mockGetRun.mockResolvedValue(runDetail);
      mockGetSuite.mockResolvedValue(suite);
      mockListChecks.mockResolvedValue([check]);
      renderAt('r1');

      const report = await screen.findByTestId('run-report');
      // The fixture's sample_failures carries a partial_unexpected_list (even
      // though every cell is already API-redacted to "<redacted>"); the report
      // must not surface it at all — not the masked placeholder, not a count,
      // not a "Failing rows" section. Omission, not re-redaction, is the
      // chosen parity strategy (matches the existing CSV/JSON export).
      expect(within(report).queryByText(/Failing rows/)).not.toBeInTheDocument();
      expect(within(report).queryByText('<redacted>')).not.toBeInTheDocument();
      expect(within(report).queryByText(/unexpected_count/)).not.toBeInTheDocument();
    });

    it('sets the tab title to suite + short run id (#345 a11y ask)', async () => {
      mockGetRun.mockResolvedValue(runDetail);
      mockGetSuite.mockResolvedValue(suite);
      mockListChecks.mockResolvedValue([check]);
      renderAt('r1');

      await screenRegion().findByText('Orders quality');
      expect(document.title).toContain('Orders quality');
      expect(document.title).toContain('r1');
    });
  });
});

describe('RunDetail — anomaly cold-start hint (#593)', () => {
  const anomalyCheck: Check = {
    id: 'chk2',
    suite_id: 's1',
    name: 'orders volume anomaly',
    kind: 'anomaly',
    expectation_type: 'monitor:anomaly',
    config: { target_metric: 'row_count', window: 14, min_points: 7, seasonality: false },
    warn_threshold: null,
    fail_threshold: 3,
    critical_threshold: null,
    alert_snoozed_until: null,
  };

  const coldStartRun: RunDetailType = {
    ...runDetail,
    results: [
      {
        id: 'res2',
        check_id: 'chk2',
        status: 'skip',
        metric_value: null,
        duration_ms: null,
        observed_value: {
          target_metric: 'row_count',
          value: 32840,
          points: 3,
          window: 14,
          min_points: 7,
          seasonality: false,
          insufficient_history: true,
          reason: 'insufficient_history',
        },
        expected_value: null,
        sample_failures: null,
        redaction: null,
        redacted_columns: [],
      },
    ],
  };

  it('shows a friendly "collecting history" hint instead of the raw observed_value JSON', async () => {
    mockGetRun.mockResolvedValue(coldStartRun);
    mockGetSuite.mockResolvedValue(suite);
    mockListChecks.mockResolvedValue([anomalyCheck]);
    renderAt('r1');
    // Scoped (#345): the print-only RunReport also renders the check name.
    const region = screenRegion();
    const user = userEvent.setup();

    await region.findByText('orders volume anomaly');
    await user.click(region.getByRole('button', { name: /expand row/i }));

    expect(await region.findByText('Collecting history: 3 of 7 points')).toBeInTheDocument();
  });

  it('does not show the hint for a scored (non-cold-start) anomaly result', async () => {
    const scoredRun: RunDetailType = {
      ...runDetail,
      results: [
        {
          id: 'res3',
          check_id: 'chk2',
          status: 'fail',
          metric_value: 4.2,
          duration_ms: null,
          observed_value: {
            target_metric: 'row_count',
            value: 32840,
            points: 14,
            z_score: 4.2,
            mean: 30000,
            stddev: 600,
            deviation: 2840,
            degenerate_stddev: false,
          },
          expected_value: null,
          redaction: null,
          redacted_columns: [],
          // Non-null with zero rows (unlike a raw GX sample, an anomaly result
          // never carries one — this shape just gives the "No sample rows
          // captured." branch something concrete to render so the test can
          // confirm the row actually expanded).
          sample_failures: {
            unexpected_count: 0,
            unexpected_percent: 0,
            partial_unexpected_list: [],
          },
        },
      ],
    };
    mockGetRun.mockResolvedValue(scoredRun);
    mockGetSuite.mockResolvedValue(suite);
    mockListChecks.mockResolvedValue([anomalyCheck]);
    renderAt('r1');
    // Scoped (#345): the print-only RunReport also renders the check name.
    const region = screenRegion();
    const user = userEvent.setup();

    await region.findByText('orders volume anomaly');
    await user.click(region.getByRole('button', { name: /expand row/i }));

    expect(await region.findByText('No sample rows captured.')).toBeInTheDocument();
    expect(region.queryByText(/Collecting history/)).not.toBeInTheDocument();
  });

  it('does not show the hint for a non-anomaly check even if a cold-start-shaped payload appears', async () => {
    mockGetRun.mockResolvedValue({
      ...runDetail,
      results: [
        {
          ...coldStartRun.results[0],
          check_id: 'chk1',
          sample_failures: {
            unexpected_count: 0,
            unexpected_percent: 0,
            partial_unexpected_list: [],
          },
        },
      ],
    });
    mockGetSuite.mockResolvedValue(suite);
    mockListChecks.mockResolvedValue([check]); // kind: 'expectation'
    renderAt('r1');
    // Scoped (#345): the print-only RunReport also renders the check name.
    const region = screenRegion();
    const user = userEvent.setup();

    await region.findByText('order_id not null');
    await user.click(region.getByRole('button', { name: /expand row/i }));

    await region.findByText('No sample rows captured.');
    expect(region.queryByText(/Collecting history/)).not.toBeInTheDocument();
  });
});
