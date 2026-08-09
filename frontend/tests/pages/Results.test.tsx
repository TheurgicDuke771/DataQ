import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Connection, listConnections } from '../../src/api/connections';
import {
  listPipelineRuns,
  listRuns,
  type PipelineRun,
  type PipelineRunListPage,
  type Run,
  type RunListPage,
} from '../../src/api/runs';
import { ORCHESTRATION_PROVIDERS, PROVIDER_LABELS } from '../../src/api/triggerBindings';
import { type Suite, listSuites } from '../../src/api/suites';
import { WINDOW_PRESETS } from '../../src/components/shared/windowPresets';
import { Results } from '../../src/pages/Results';
import { selectOption } from '../support/antd';

vi.mock('../../src/api/runs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/runs')>();
  return { ...actual, listRuns: vi.fn(), listPipelineRuns: vi.fn() };
});

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, listSuites: vi.fn() };
});

vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return { ...actual, listConnections: vi.fn() };
});

const mockListRuns = vi.mocked(listRuns);
const mockListPipelineRuns = vi.mocked(listPipelineRuns);
const mockListSuites = vi.mocked(listSuites);
const mockListConnections = vi.mocked(listConnections);

const snowflakeConn: Connection = {
  id: 'c1',
  name: 'Snowflake DEV',
  type: 'snowflake',
  env: 'dev',
  config: {},
  has_secret: true,
  created_by: 'u1',
};

const s3Conn: Connection = {
  ...snowflakeConn,
  id: 'c2',
  name: 'S3 PROD',
  type: 's3',
  env: 'prod',
};

const ordersSuite: Suite = {
  id: 's1',
  name: 'Orders quality',
  description: null,
  connection_id: 'c1',
  target: { table: 'ORDERS' },
  created_by: 'u1',
};

const eventsSuite: Suite = {
  ...ordersSuite,
  id: 's2',
  name: 'Events lake',
  connection_id: 'c2',
};

const succeededRun: Run = {
  id: 'r1',
  suite_id: 's1',
  status: 'succeeded',
  triggered_by: 'manual:u1',
  started_at: '2026-06-11T00:00:00Z',
  finished_at: '2026-06-11T00:00:12Z',
  created_at: '2026-06-11T00:00:00Z',
  checks_total: 3,
  checks_passed: 3,
  worst_severity: null,
  failure_reason: null,
};

const failedRun: Run = {
  ...succeededRun,
  id: 'r2',
  status: 'failed',
  triggered_by: 'seed:run:failed',
  finished_at: '2026-06-11T00:00:02Z',
  checks_total: 3,
  checks_passed: 1,
  worst_severity: 'fail',
  failure_reason: null,
};

/** A run on the S3 (flat-file, prod) suite, started "now" so it falls inside the
 *  recent date windows. */
const recentEventsRun: Run = {
  id: 'r3',
  suite_id: 's2',
  status: 'succeeded',
  triggered_by: 'schedule',
  started_at: new Date().toISOString(),
  finished_at: new Date().toISOString(),
  created_at: new Date().toISOString(),
  checks_total: 2,
  checks_passed: 2,
  worst_severity: null,
  failure_reason: null,
};

const pipelineRun: PipelineRun = {
  id: 'p1',
  provider: 'adf',
  connection_id: 'c2',
  provider_run_id: 'seed-adf-0001',
  pipeline_or_dag_id: 'daily_orders_load',
  env: 'prod',
  status: 'succeeded',
  started_at: '2026-06-11T00:00:00Z',
  finished_at: '2026-06-11T00:00:30Z',
  failure_reason: null,
  created_at: '2026-06-11T00:00:00Z',
};

/** `listPipelineRuns` now resolves a page (`{ items, total }`, #1108) — this
 *  wraps a bare fixture array as a full (untruncated) page, the shape every
 *  test below needs unless it's exercising the truncation note itself. */
function pipelineRunsPage(items: PipelineRun[], total = items.length): PipelineRunListPage {
  return { items, total };
}

/** Same for `listRuns` (#1108) — a bare fixture array as a full, untruncated
 *  page. Pass an explicit `total` to exercise the Runs tab's truncation note. */
function runsPage(items: Run[], total = items.length): RunListPage {
  return { items, total };
}

/** A stub for the run-detail route so a row click's navigation is observable. */
function RunDetailStub() {
  const { runId } = useParams<{ runId: string }>();
  return <div>run-detail:{runId}</div>;
}

function renderResults() {
  return render(
    <MemoryRouter initialEntries={['/results']}>
      <Routes>
        <Route path="/results" element={<Results />} />
        <Route path="/results/:runId" element={<RunDetailStub />} />
      </Routes>
    </MemoryRouter>,
  );
}

/** The runs-tab filter Selects, in DOM order. */
const FILTER = { status: 0, suite: 1, env: 2, datasource: 3, date: 4 } as const;

/** Open the Nth filter Select and pick the option titled `optionTitle`. */
const pickFilter = (user: ReturnType<typeof userEvent.setup>, index: number, optionTitle: string) =>
  selectOption(user, optionTitle, { index });

const tableRowCount = () => document.querySelectorAll('tr.ant-table-row').length;

afterEach(() => {
  vi.clearAllMocks();
  // Belt-and-braces for the fake-timer poll test below — a failed assertion
  // mid-test must not leave fake timers active for every test after it.
  vi.useRealTimers();
});

describe('Results page', () => {
  it('lists runs with the suite name and a status tag', async () => {
    mockListRuns.mockResolvedValue(runsPage([succeededRun, failedRun]));
    mockListSuites.mockResolvedValue([ordersSuite]);
    mockListConnections.mockResolvedValue([snowflakeConn]);
    mockListPipelineRuns.mockResolvedValue(pipelineRunsPage([]));

    renderResults();

    // Both seeded runs resolve to the suite name, with their status tags.
    await waitFor(() => expect(screen.getAllByText('Orders quality').length).toBe(2));
    expect(screen.getByText('succeeded')).toBeInTheDocument();
    expect(screen.getByText('failed')).toBeInTheDocument();
  });

  it('navigates to the routed run-detail page on row click', async () => {
    mockListRuns.mockResolvedValue(runsPage([succeededRun]));
    mockListSuites.mockResolvedValue([ordersSuite]);
    mockListConnections.mockResolvedValue([snowflakeConn]);
    mockListPipelineRuns.mockResolvedValue(pipelineRunsPage([]));

    renderResults();
    const user = userEvent.setup();

    await waitFor(() => expect(screen.getByText('Orders quality')).toBeInTheDocument());
    await user.click(screen.getByText('Orders quality'));

    // The run-detail drawer is gone — the row deep-links to /results/:runId.
    expect(await screen.findByText('run-detail:r1')).toBeInTheDocument();
  });

  it('filters the runs table by status', async () => {
    mockListRuns.mockResolvedValue(runsPage([succeededRun, failedRun]));
    mockListSuites.mockResolvedValue([ordersSuite]);
    mockListConnections.mockResolvedValue([snowflakeConn]);
    mockListPipelineRuns.mockResolvedValue(pipelineRunsPage([]));

    renderResults();
    const user = userEvent.setup();

    await waitFor(() => expect(screen.getAllByText('Orders quality').length).toBe(2));

    // Pick "failed" in the status Select → only the failed run's row remains.
    await pickFilter(user, FILTER.status, 'failed');

    await waitFor(() => expect(tableRowCount()).toBe(1));
    const row = document.querySelector('tr.ant-table-row');
    expect(row?.textContent).toContain('failed');
    expect(row?.textContent).not.toContain('succeeded');
  });

  it('filters the runs table by suite', async () => {
    mockListRuns.mockResolvedValue(runsPage([succeededRun, recentEventsRun]));
    mockListSuites.mockResolvedValue([ordersSuite, eventsSuite]);
    mockListConnections.mockResolvedValue([snowflakeConn, s3Conn]);
    mockListPipelineRuns.mockResolvedValue(pipelineRunsPage([]));

    renderResults();
    const user = userEvent.setup();

    await waitFor(() => expect(tableRowCount()).toBe(2));

    await pickFilter(user, FILTER.suite, 'Events lake');

    await waitFor(() => expect(tableRowCount()).toBe(1));
    expect(document.querySelector('tr.ant-table-row')?.textContent).toContain('Events lake');
  });

  it('filters the runs table by environment', async () => {
    mockListRuns.mockResolvedValue(runsPage([succeededRun, recentEventsRun]));
    mockListSuites.mockResolvedValue([ordersSuite, eventsSuite]);
    mockListConnections.mockResolvedValue([snowflakeConn, s3Conn]);
    mockListPipelineRuns.mockResolvedValue(pipelineRunsPage([]));

    renderResults();
    const user = userEvent.setup();

    await waitFor(() => expect(tableRowCount()).toBe(2));

    // PROD env → only the run on the prod-connection suite (Events lake).
    await pickFilter(user, FILTER.env, 'PROD');

    await waitFor(() => expect(tableRowCount()).toBe(1));
    expect(document.querySelector('tr.ant-table-row')?.textContent).toContain('Events lake');
  });

  it('filters the runs table by datasource category', async () => {
    mockListRuns.mockResolvedValue(runsPage([succeededRun, recentEventsRun]));
    mockListSuites.mockResolvedValue([ordersSuite, eventsSuite]);
    mockListConnections.mockResolvedValue([snowflakeConn, s3Conn]);
    mockListPipelineRuns.mockResolvedValue(pipelineRunsPage([]));

    renderResults();
    const user = userEvent.setup();

    await waitFor(() => expect(tableRowCount()).toBe(2));

    // S3 collapses into the "Flat file" category → only the Events lake run.
    await pickFilter(user, FILTER.datasource, 'Flat file');

    await waitFor(() => expect(tableRowCount()).toBe(1));
    expect(document.querySelector('tr.ant-table-row')?.textContent).toContain('Events lake');
  });

  it('filters the runs table by date window', async () => {
    // succeededRun started 2026-06-11 (>7d before the 2026-06-22 fixture date);
    // recentEventsRun started now → only the recent run is inside "Last 7 days".
    mockListRuns.mockResolvedValue(runsPage([succeededRun, recentEventsRun]));
    mockListSuites.mockResolvedValue([ordersSuite, eventsSuite]);
    mockListConnections.mockResolvedValue([snowflakeConn, s3Conn]);
    mockListPipelineRuns.mockResolvedValue(pipelineRunsPage([]));

    renderResults();
    const user = userEvent.setup();

    await waitFor(() => expect(tableRowCount()).toBe(2));

    await pickFilter(user, FILTER.date, 'Last 7 days');

    await waitFor(() => expect(tableRowCount()).toBe(1));
    expect(document.querySelector('tr.ant-table-row')?.textContent).toContain('Events lake');
  });

  it('shows monitored pipeline runs on the Pipeline runs tab', async () => {
    mockListRuns.mockResolvedValue(runsPage([]));
    mockListSuites.mockResolvedValue([]);
    mockListConnections.mockResolvedValue([]);
    mockListPipelineRuns.mockResolvedValue(pipelineRunsPage([pipelineRun]));

    renderResults();
    const user = userEvent.setup();

    await user.click(screen.getByRole('tab', { name: 'Pipeline runs' }));

    await waitFor(() => expect(screen.getByText('daily_orders_load')).toBeInTheDocument());
    // Provider renders its human label (shared PROVIDER_LABELS), not the raw code.
    expect(screen.getByText('Azure Data Factory')).toBeInTheDocument();
    expect(screen.getByText('succeeded')).toBeInTheDocument();
    // The fetched page (1 row) matches the reported total (1) — no truncation note.
    expect(screen.queryByText(/of \d+ pipeline runs/)).not.toBeInTheDocument();
  });

  it('shows an honest truncation note when the monitored population exceeds the fetched page (#1108)', async () => {
    // The tab fetches one capped page; a `total` bigger than that page's length
    // means the table is silently NOT everything — #1108's actual defect on
    // `/pipeline_runs` (and the identical gap on `/assets` before #925).
    mockListRuns.mockResolvedValue(runsPage([]));
    mockListSuites.mockResolvedValue([]);
    mockListConnections.mockResolvedValue([]);
    mockListPipelineRuns.mockResolvedValue(pipelineRunsPage([pipelineRun], 211));

    renderResults();
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', { name: 'Pipeline runs' }));

    await waitFor(() =>
      expect(screen.getByText('Loaded the 1 most recent of 211 pipeline runs')).toBeInTheDocument(),
    );
    // The advice must not send the user to filters that cannot possibly help:
    // both selects are client-side over the page already fetched, so no choice
    // reaches the 210 runs that were never loaded.
    expect(screen.queryByText(/Narrow the provider or date filter/)).not.toBeInTheDocument();
    expect(screen.getByText(/filters below only narrow what's already loaded/)).toBeInTheDocument();
  });

  it('counts the FETCH, not the filtered table, in the pipeline truncation note (#1108)', async () => {
    // The note sits above a client-side-filtered table. If it counted the
    // filtered rows it would read "Loaded the 0 most recent of 211" over an
    // empty table — or vanish — the moment a filter excluded everything. It
    // describes the fetch, so it stays put and stays true.
    mockListRuns.mockResolvedValue(runsPage([]));
    mockListSuites.mockResolvedValue([]);
    mockListConnections.mockResolvedValue([]);
    mockListPipelineRuns.mockResolvedValue(pipelineRunsPage([pipelineRun], 211));

    renderResults();
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', { name: 'Pipeline runs' }));
    await waitFor(() =>
      expect(screen.getByText('Loaded the 1 most recent of 211 pipeline runs')).toBeInTheDocument(),
    );

    // Filter to a provider the single loaded row is NOT — the table empties.
    await user.click(screen.getByRole('combobox', { name: 'Provider' }));
    await user.click(await screen.findByTitle(PROVIDER_LABELS.dbt));
    await waitFor(() => expect(screen.queryByText('daily_orders_load')).not.toBeInTheDocument());
    // The note is unchanged: still the fetch's numbers, still on screen.
    expect(screen.getByText('Loaded the 1 most recent of 211 pipeline runs')).toBeInTheDocument();
  });

  it('discloses truncation on the Runs tab too, not only the Pipeline tab (#1108)', async () => {
    // `/runs` gained `X-Total-Count` in #1108, but the Runs tab — the PRIMARY
    // runs surface — ignored it, so a 500-run workspace rendered its capped
    // 200-row fetch as if complete. That is the exact silence #1108 names.
    mockListRuns.mockResolvedValue(runsPage([succeededRun], 500));
    mockListSuites.mockResolvedValue([]);
    mockListConnections.mockResolvedValue([]);
    mockListPipelineRuns.mockResolvedValue(pipelineRunsPage([]));

    renderResults();

    await waitFor(() =>
      expect(screen.getByText('Loaded the 1 most recent of 500 runs')).toBeInTheDocument(),
    );
  });

  it('shows no runs truncation note when the page IS the whole population (#1108)', async () => {
    // The note must be driven by the header, not printed unconditionally — a
    // complete page has nothing to disclose.
    mockListRuns.mockResolvedValue(runsPage([succeededRun]));
    mockListSuites.mockResolvedValue([]);
    mockListConnections.mockResolvedValue([]);
    mockListPipelineRuns.mockResolvedValue(pipelineRunsPage([]));

    renderResults();

    await waitFor(() => expect(screen.getByText('succeeded')).toBeInTheDocument());
    expect(screen.queryByText(/most recent of \d+ runs/)).not.toBeInTheDocument();
  });

  it('offers every orchestration provider in the pipeline-runs filter and filters by it (#652)', async () => {
    const dbtRun: PipelineRun = {
      ...pipelineRun,
      id: 'p2',
      provider: 'dbt',
      provider_run_id: 'inv-0001',
      pipeline_or_dag_id: 'analytics_build',
    };
    mockListRuns.mockResolvedValue(runsPage([]));
    mockListSuites.mockResolvedValue([]);
    mockListConnections.mockResolvedValue([]);
    mockListPipelineRuns.mockResolvedValue(pipelineRunsPage([pipelineRun, dbtRun]));

    renderResults();
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', { name: 'Pipeline runs' }));
    await waitFor(() => expect(screen.getByText('analytics_build')).toBeInTheDocument());

    // The provider filter must offer the full shared tuple (guards the next
    // ADR-0029-style provider addition too).
    await user.click(screen.getByRole('combobox', { name: 'Provider' }));
    for (const provider of ORCHESTRATION_PROVIDERS) {
      // findByTitle, matching pickFilter above: AntD's role=option list is a
      // truncated a11y mirror; the real items carry the label as `title`.
      expect(await screen.findByTitle(PROVIDER_LABELS[provider])).toBeInTheDocument();
    }
    await user.click(await screen.findByTitle(PROVIDER_LABELS.dbt));

    // Only the dbt pipeline run remains.
    await waitFor(() => expect(screen.queryByText('daily_orders_load')).not.toBeInTheDocument());
    expect(screen.getByText('analytics_build')).toBeInTheDocument();
  });

  it('bounds the Failure-reason column with an ellipsis + hover tooltip for long errors, and shows — for null (#1184)', async () => {
    const longReason =
      "ErrorCode=UserErrorOdbcInvalidQueryString,'Type=Microsoft.DataTransfer.Common.Shared.HybridDeliveryException," +
      "Message=ERROR [42S02] [Snowflake][Snowflake] (4) SQL compilation error: Object 'RETAIL.ORDERS_STAGING' does not exist or not authorized. " +
      'A'.repeat(300);
    const failingPipelineRun: PipelineRun = {
      ...pipelineRun,
      id: 'p3',
      pipeline_or_dag_id: 'nightly_orders_retry',
      failure_reason: longReason,
    };
    mockListRuns.mockResolvedValue(runsPage([]));
    mockListSuites.mockResolvedValue([]);
    mockListConnections.mockResolvedValue([]);
    // pipelineRun keeps its null failure_reason — covers the '—' placeholder
    // alongside the long-error row.
    mockListPipelineRuns.mockResolvedValue(pipelineRunsPage([pipelineRun, failingPipelineRun]));

    renderResults();
    const user = userEvent.setup();

    await user.click(screen.getByRole('tab', { name: 'Pipeline runs' }));
    await waitFor(() => expect(screen.getByText('nightly_orders_retry')).toBeInTheDocument());

    // The null-reason row (pipelineRun) shows the usual em-dash placeholder in
    // its Failure-reason cell — scoped via antd's own ellipsis-column class
    // (`ant-table-cell-ellipsis`, unique to this column) since the row's "DQ
    // run" cell also renders a '—' placeholder.
    const nullRow = screen.getByText('daily_orders_load').closest('tr') as HTMLElement;
    const nullReasonCell = nullRow.querySelector('td.ant-table-cell-ellipsis');
    expect(nullReasonCell).toHaveTextContent('—');

    // The long reason is present in the DOM as a whole string — the ellipsis
    // is CSS-only (text-overflow), antd never clips the actual text node —
    // wrapped in the tooltip's trigger element.
    const trigger = screen.getByText(longReason);
    await user.hover(trigger);

    // Hovering reveals the FULL string via antd's custom tooltip (not the
    // native `title` one, which `ellipsis: { showTitle: false }` suppresses).
    expect(await screen.findByRole('tooltip')).toHaveTextContent(longReason);
  });

  it('correlates a pipeline run to the DQ run it triggered', async () => {
    // A DQ run stamped with the pipeline run's marker (provider:dag:run_id).
    const triggeredRun: Run = {
      ...failedRun,
      id: 'rdq',
      suite_id: 's1',
      triggered_by: 'adf:daily_orders_load:seed-adf-0001',
    };
    mockListRuns.mockResolvedValue(runsPage([triggeredRun]));
    mockListSuites.mockResolvedValue([]);
    mockListConnections.mockResolvedValue([]);
    mockListPipelineRuns.mockResolvedValue(pipelineRunsPage([pipelineRun]));

    renderResults();
    const user = userEvent.setup();

    await user.click(screen.getByRole('tab', { name: 'Pipeline runs' }));
    await waitFor(() => expect(screen.getByText('daily_orders_load')).toBeInTheDocument());

    // The pipeline run's row carries a clickable DQ-run tag (the triggered run is
    // 'failed' — distinct from the pipeline status 'succeeded') that deep-links.
    const row = screen.getByText('daily_orders_load').closest('tr') as HTMLElement;
    await user.click(within(row).getByText('failed'));

    expect(await screen.findByText('run-detail:rdq')).toBeInTheDocument();
  });

  it('fetches runs once and shares them across both tabs (#349)', async () => {
    // A run stamped with the pipeline run's marker so the Pipeline runs tab
    // actually exercises the shared data (the "DQ run" column), not just an
    // empty join.
    const triggeredRun: Run = {
      ...failedRun,
      id: 'rdq',
      suite_id: 's1',
      triggered_by: 'adf:daily_orders_load:seed-adf-0001',
    };
    mockListRuns.mockResolvedValue(runsPage([succeededRun, triggeredRun]));
    mockListSuites.mockResolvedValue([ordersSuite]);
    mockListConnections.mockResolvedValue([snowflakeConn]);
    mockListPipelineRuns.mockResolvedValue(pipelineRunsPage([pipelineRun]));

    renderResults();
    const user = userEvent.setup();

    // Runs tab renders first (default active) — the shared fetch already ran.
    await waitFor(() => expect(screen.getAllByText('Orders quality').length).toBe(2));
    expect(mockListRuns).toHaveBeenCalledTimes(1);

    // Switching to the Pipeline runs tab must reuse the same runs data rather
    // than issuing a second `listRuns` call (that's the whole point of #349 —
    // antd's lazy pane mount used to make this a fresh fetch).
    await user.click(screen.getByRole('tab', { name: 'Pipeline runs' }));
    await waitFor(() => expect(screen.getByText('daily_orders_load')).toBeInTheDocument());
    // The correlated DQ run tag proves the shared data actually reached this
    // tab, not just that no second fetch happened. (The Runs tab, still
    // mounted-but-hidden behind this one, also renders a 'failed' tag for the
    // same run — scope to this row to disambiguate.)
    const row = screen.getByText('daily_orders_load').closest('tr') as HTMLElement;
    expect(within(row).getByText('failed')).toBeInTheDocument();

    expect(mockListRuns).toHaveBeenCalledTimes(1);
  });

  it('shares date-window presets with the Dashboard (#349)', async () => {
    mockListRuns.mockResolvedValue(runsPage([]));
    mockListSuites.mockResolvedValue([]);
    mockListConnections.mockResolvedValue([]);
    mockListPipelineRuns.mockResolvedValue(pipelineRunsPage([]));

    renderResults();
    const user = userEvent.setup();

    // Wait for the Runs tab to render past its loading Spin (the filter bar,
    // including the Date Select, only mounts once the shared runs fetch
    // resolves) before opening the Date filter.
    await waitFor(async () => expect((await screen.findAllByRole('combobox')).length).toBe(5));

    // Open the Date filter and confirm it offers exactly Results' own 'All
    // time' entry plus every shared WINDOW_PRESETS label — so a change to the
    // shared module (or a re-introduced local copy that drifts from it) shows
    // up here. Match against the dropdown option content, not `title` — the
    // currently-selected value ('All time') also carries a `title` on the
    // closed Select, which would otherwise match twice.
    await user.click((await screen.findAllByRole('combobox'))[FILTER.date]);
    const optionSelector = '.ant-select-item-option-content';
    expect(await screen.findByText('All time', { selector: optionSelector })).toBeInTheDocument();
    for (const preset of WINDOW_PRESETS) {
      expect(
        await screen.findByText(preset.label, { selector: optionSelector }),
      ).toBeInTheDocument();
    }
  });

  it('shows PageError with a working retry when the initial runs load fails (#1114)', async () => {
    // No prior successful load exists yet, so there is no last-good snapshot
    // to fall back to — this must stay a full-page failure, not a blank/empty
    // table pretending everything's fine.
    mockListRuns.mockRejectedValueOnce(new Error('boom'));
    mockListSuites.mockResolvedValue([ordersSuite]);
    mockListConnections.mockResolvedValue([snowflakeConn]);
    mockListPipelineRuns.mockResolvedValue(pipelineRunsPage([]));

    renderResults();
    const user = userEvent.setup();

    const retry = await screen.findByRole('button', { name: 'Try again' });
    // No filter bar / table rendered behind the error page.
    expect(screen.queryAllByRole('combobox')).toHaveLength(0);

    // The retry action isn't a dead end — it re-runs the shared fetch.
    expect(mockListRuns).toHaveBeenCalledTimes(1);
    mockListRuns.mockResolvedValueOnce(runsPage([succeededRun]));
    await user.click(retry);
    await waitFor(() => expect(mockListRuns).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('Orders quality')).toBeInTheDocument();
  });

  it('keeps the last-good runs table + shows an inline warning when a background poll fails (#1114)', async () => {
    // Regression coverage for the #1114 review finding: lifting the runs fetch
    // to the parent (#349) means PipelineRunsTab's 30s poll — armed once that
    // tab has been visited, since antd keeps panes mounted — also reloads the
    // SAME shared runs data RunsTab reads. Before this fix, a poll failure
    // flipped the shared state to 'error' and RunsTab's unconditional
    // `if (status === 'error') return <PageError/>` blanked the whole Runs
    // table+filters on a transient background hiccup that used to be cosmetic.
    vi.useFakeTimers();

    mockListRuns.mockResolvedValueOnce(runsPage([succeededRun]));
    mockListSuites.mockResolvedValue([ordersSuite]);
    mockListConnections.mockResolvedValue([snowflakeConn]);
    mockListPipelineRuns.mockResolvedValue(pipelineRunsPage([pipelineRun]));

    renderResults();
    await vi.advanceTimersByTimeAsync(0);
    await vi.waitFor(() => expect(screen.getByText('Orders quality')).toBeInTheDocument());

    // Visit the Pipeline tab (arms its poll, which also reloads the shared
    // runs data — see the effect in PipelineRunsTab), then return to Runs.
    // fireEvent, not userEvent: userEvent's async helpers use real
    // setTimeout-based delays internally, which hang forever under fake
    // timers; fireEvent dispatches synchronously (same pattern as
    // Settings.test.tsx's antd-tab clicks).
    fireEvent.click(screen.getByRole('tab', { name: 'Pipeline runs' }));
    await vi.advanceTimersByTimeAsync(0);
    fireEvent.click(screen.getByRole('tab', { name: 'Runs' }));
    await vi.advanceTimersByTimeAsync(0);

    // The next poll tick's shared-runs fetch fails.
    mockListRuns.mockRejectedValueOnce(new Error('background boom'));
    await vi.advanceTimersByTimeAsync(30_000);
    await vi.advanceTimersByTimeAsync(0);

    // Runs table still shows the last-good row plus an inline warning — NOT
    // the full-page PageError (that's exactly the regression this guards).
    await vi.waitFor(() =>
      expect(screen.getByText('Showing the last loaded runs')).toBeInTheDocument(),
    );
    expect(screen.getByText('Orders quality')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
  });
});
