import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Connection, listConnections } from '../../src/api/connections';
import { listPipelineRuns, listRuns, type PipelineRun, type Run } from '../../src/api/runs';
import { ORCHESTRATION_PROVIDERS, PROVIDER_LABELS } from '../../src/api/triggerBindings';
import { type Suite, listSuites } from '../../src/api/suites';
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
});

describe('Results page', () => {
  it('lists runs with the suite name and a status tag', async () => {
    mockListRuns.mockResolvedValue([succeededRun, failedRun]);
    mockListSuites.mockResolvedValue([ordersSuite]);
    mockListConnections.mockResolvedValue([snowflakeConn]);
    mockListPipelineRuns.mockResolvedValue([]);

    renderResults();

    // Both seeded runs resolve to the suite name, with their status tags.
    await waitFor(() => expect(screen.getAllByText('Orders quality').length).toBe(2));
    expect(screen.getByText('succeeded')).toBeInTheDocument();
    expect(screen.getByText('failed')).toBeInTheDocument();
  });

  it('navigates to the routed run-detail page on row click', async () => {
    mockListRuns.mockResolvedValue([succeededRun]);
    mockListSuites.mockResolvedValue([ordersSuite]);
    mockListConnections.mockResolvedValue([snowflakeConn]);
    mockListPipelineRuns.mockResolvedValue([]);

    renderResults();
    const user = userEvent.setup();

    await waitFor(() => expect(screen.getByText('Orders quality')).toBeInTheDocument());
    await user.click(screen.getByText('Orders quality'));

    // The run-detail drawer is gone — the row deep-links to /results/:runId.
    expect(await screen.findByText('run-detail:r1')).toBeInTheDocument();
  });

  it('filters the runs table by status', async () => {
    mockListRuns.mockResolvedValue([succeededRun, failedRun]);
    mockListSuites.mockResolvedValue([ordersSuite]);
    mockListConnections.mockResolvedValue([snowflakeConn]);
    mockListPipelineRuns.mockResolvedValue([]);

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
    mockListRuns.mockResolvedValue([succeededRun, recentEventsRun]);
    mockListSuites.mockResolvedValue([ordersSuite, eventsSuite]);
    mockListConnections.mockResolvedValue([snowflakeConn, s3Conn]);
    mockListPipelineRuns.mockResolvedValue([]);

    renderResults();
    const user = userEvent.setup();

    await waitFor(() => expect(tableRowCount()).toBe(2));

    await pickFilter(user, FILTER.suite, 'Events lake');

    await waitFor(() => expect(tableRowCount()).toBe(1));
    expect(document.querySelector('tr.ant-table-row')?.textContent).toContain('Events lake');
  });

  it('filters the runs table by environment', async () => {
    mockListRuns.mockResolvedValue([succeededRun, recentEventsRun]);
    mockListSuites.mockResolvedValue([ordersSuite, eventsSuite]);
    mockListConnections.mockResolvedValue([snowflakeConn, s3Conn]);
    mockListPipelineRuns.mockResolvedValue([]);

    renderResults();
    const user = userEvent.setup();

    await waitFor(() => expect(tableRowCount()).toBe(2));

    // PROD env → only the run on the prod-connection suite (Events lake).
    await pickFilter(user, FILTER.env, 'PROD');

    await waitFor(() => expect(tableRowCount()).toBe(1));
    expect(document.querySelector('tr.ant-table-row')?.textContent).toContain('Events lake');
  });

  it('filters the runs table by datasource category', async () => {
    mockListRuns.mockResolvedValue([succeededRun, recentEventsRun]);
    mockListSuites.mockResolvedValue([ordersSuite, eventsSuite]);
    mockListConnections.mockResolvedValue([snowflakeConn, s3Conn]);
    mockListPipelineRuns.mockResolvedValue([]);

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
    mockListRuns.mockResolvedValue([succeededRun, recentEventsRun]);
    mockListSuites.mockResolvedValue([ordersSuite, eventsSuite]);
    mockListConnections.mockResolvedValue([snowflakeConn, s3Conn]);
    mockListPipelineRuns.mockResolvedValue([]);

    renderResults();
    const user = userEvent.setup();

    await waitFor(() => expect(tableRowCount()).toBe(2));

    await pickFilter(user, FILTER.date, 'Last 7 days');

    await waitFor(() => expect(tableRowCount()).toBe(1));
    expect(document.querySelector('tr.ant-table-row')?.textContent).toContain('Events lake');
  });

  it('shows monitored pipeline runs on the Pipeline runs tab', async () => {
    mockListRuns.mockResolvedValue([]);
    mockListSuites.mockResolvedValue([]);
    mockListConnections.mockResolvedValue([]);
    mockListPipelineRuns.mockResolvedValue([pipelineRun]);

    renderResults();
    const user = userEvent.setup();

    await user.click(screen.getByRole('tab', { name: 'Pipeline runs' }));

    await waitFor(() => expect(screen.getByText('daily_orders_load')).toBeInTheDocument());
    // Provider renders its human label (shared PROVIDER_LABELS), not the raw code.
    expect(screen.getByText('Azure Data Factory')).toBeInTheDocument();
    expect(screen.getByText('succeeded')).toBeInTheDocument();
  });

  it('offers every orchestration provider in the pipeline-runs filter and filters by it (#652)', async () => {
    const dbtRun: PipelineRun = {
      ...pipelineRun,
      id: 'p2',
      provider: 'dbt',
      provider_run_id: 'inv-0001',
      pipeline_or_dag_id: 'analytics_build',
    };
    mockListRuns.mockResolvedValue([]);
    mockListSuites.mockResolvedValue([]);
    mockListConnections.mockResolvedValue([]);
    mockListPipelineRuns.mockResolvedValue([pipelineRun, dbtRun]);

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

  it('correlates a pipeline run to the DQ run it triggered', async () => {
    // A DQ run stamped with the pipeline run's marker (provider:dag:run_id).
    const triggeredRun: Run = {
      ...failedRun,
      id: 'rdq',
      suite_id: 's1',
      triggered_by: 'adf:daily_orders_load:seed-adf-0001',
    };
    mockListRuns.mockResolvedValue([triggeredRun]);
    mockListSuites.mockResolvedValue([]);
    mockListConnections.mockResolvedValue([]);
    mockListPipelineRuns.mockResolvedValue([pipelineRun]);

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
});
