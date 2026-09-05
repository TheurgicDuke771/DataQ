import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as adminApi from '../../../src/api/admin';
import type { AdminHealth, AdminOverview as AdminOverviewCounts } from '../../../src/api/admin';
import * as triggerApi from '../../../src/api/triggerBindings';
import { AdminOverview } from '../../../src/pages/admin/AdminOverview';
import { renderSubPage } from './adminFixtures';

vi.mock('../../../src/api/admin');
vi.mock('../../../src/api/triggerBindings', async (importOriginal) => {
  const actual = await importOriginal<typeof triggerApi>();
  return { ...actual, listEnvNearMisses: vi.fn() };
});

const COUNTS: AdminOverviewCounts = {
  members: { total: 4, pending_first_signin: null, pending_source: 'not_available' },
  suites: { total: 6, connections: 3 },
  incidents: { open: 5, acknowledged: 2 },
  runs_today: {
    total: 9,
    succeeded: 6,
    failed: 2,
    running: 1,
    since: '2026-09-05T00:00:00Z',
  },
  generated_at: '2026-09-05T09:00:00Z',
};

const HEALTHY: AdminHealth = {
  polling: [
    {
      connection_id: 'c1',
      name: 'airflow-prod',
      provider: 'airflow',
      last_polled_at: '2026-09-05T08:55:00Z',
      cadence_seconds: 600,
      next_expected_at: '2026-09-05T09:05:00Z',
      status: 'on_cadence',
      last_error: null,
    },
  ],
  beat: { last_tick_at: '2026-09-05T08:59:00Z', status: 'alive' },
  queues: [{ name: 'celery', depth: 0 }],
  queues_error: null,
  credentials: [
    {
      connection_id: 'd1',
      name: 'sf-prod',
      type: 'snowflake',
      env: 'prod',
      status: 'healthy',
      consecutive_auth_failures: 0,
      last_auth_failure_at: null,
      last_auth_success_at: '2026-09-05T08:00:00Z',
      last_error: null,
    },
  ],
  generated_at: '2026-09-05T09:00:00Z',
};

const CLEAN_SWEEP: adminApi.SecretSweepReport = {
  status: 'recorded',
  ran_at: '2026-09-05T02:00:00Z',
  mode: 'report',
  orphan_count: 0,
  orphan_names: [],
  truncated: false,
  scanned: 12,
  unknown_age_count: 0,
  too_young_count: 0,
  store: 'key_vault',
  error: null,
};

const mocked = vi.mocked(adminApi);
const mockNearMisses = vi.mocked(triggerApi.listEnvNearMisses);

beforeEach(() => {
  mocked.getAdminOverview.mockResolvedValue(COUNTS);
  mocked.getAdminHealth.mockResolvedValue(HEALTHY);
  mocked.getSecretSweep.mockResolvedValue(CLEAN_SWEEP);
  mockNearMisses.mockResolvedValue([]);
});
afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe('AdminOverview stat cards', () => {
  it('renders each count with the breakdown beside it', async () => {
    renderSubPage(<AdminOverview />);

    expect(await screen.findByText('6')).toBeInTheDocument(); // suites
    expect(screen.getByText('across 3 connection(s)')).toBeInTheDocument();
    expect(screen.getByText('2 acknowledged (still open)')).toBeInTheDocument();
    expect(screen.getByText('6 succeeded · 2 failed · 1 running (UTC day)')).toBeInTheDocument();
  });

  it('renders an untracked pending count as "not tracked", never as zero', async () => {
    renderSubPage(<AdminOverview />);

    expect(await screen.findByText('Pending first sign-in is not tracked yet')).toBeInTheDocument();
    expect(screen.queryByText('0 pending first sign-in')).not.toBeInTheDocument();
  });

  it('shows the load failure on the cards instead of a zero', async () => {
    mocked.getAdminOverview.mockRejectedValue(new Error('boom'));
    renderSubPage(<AdminOverview />);

    const failures = await screen.findAllByText(/Could not load:/);
    expect(failures).toHaveLength(4);
    expect(screen.queryByText('6')).not.toBeInTheDocument();
  });
});

describe('AdminOverview needs-attention feed', () => {
  it('says nothing needs attention only when every source loaded clean', async () => {
    renderSubPage(<AdminOverview />);

    expect(await screen.findByText(/Nothing needs attention/)).toBeInTheDocument();
    expect(screen.queryByText('Needs action')).not.toBeInTheDocument();
  });

  it('raises a stalled poll with a link to the connection', async () => {
    mocked.getAdminHealth.mockResolvedValue({
      ...HEALTHY,
      polling: [{ ...HEALTHY.polling[0], status: 'stalled' }],
    });
    renderSubPage(<AdminOverview />);

    expect(
      await screen.findByText('Apache Airflow polling has stalled — airflow-prod'),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'View connection' })).toHaveAttribute(
      'href',
      '/connections/c1/edit',
    );
  });

  it('raises a rejected credential with a re-auth link', async () => {
    mocked.getAdminHealth.mockResolvedValue({
      ...HEALTHY,
      credentials: [
        {
          ...HEALTHY.credentials[0],
          status: 'failing',
          consecutive_auth_failures: 3,
          last_error: 'authentication failed',
        },
      ],
    });
    renderSubPage(<AdminOverview />);

    expect(await screen.findByText('Stored credential rejected — sf-prod')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Re-auth' })).toHaveAttribute(
      'href',
      '/connections/d1/edit',
    );
  });

  it('renders a never-observed credential as not monitored rather than omitting it', async () => {
    mocked.getAdminHealth.mockResolvedValue({
      ...HEALTHY,
      credentials: [{ ...HEALTHY.credentials[0], status: 'unknown', last_auth_success_at: null }],
    });
    renderSubPage(<AdminOverview />);

    expect(
      await screen.findByText('1 datasource credential(s) never observed'),
    ).toBeInTheDocument();
    expect(screen.getByText('Not monitored')).toBeInTheDocument();
  });

  it('treats a health-endpoint error as unknown, not as an all-clear', async () => {
    mocked.getAdminHealth.mockRejectedValue(new Error('health exploded'));
    renderSubPage(<AdminOverview />);

    expect(await screen.findByText('Workspace health could not be loaded')).toBeInTheDocument();
    expect(screen.queryByText(/Nothing needs attention/)).not.toBeInTheDocument();
    expect(screen.getByText(/poll staleness is unknown, not on cadence/)).toBeInTheDocument();
  });

  it('reports an unreachable broker as unknown queue depth, never zero', async () => {
    mocked.getAdminHealth.mockResolvedValue({
      ...HEALTHY,
      queues: null,
      queues_error: 'broker unreachable',
    });
    renderSubPage(<AdminOverview />);

    expect(await screen.findByText('Queue depth unknown')).toBeInTheDocument();
    // The feed row and the checklist row both say it; neither may say "0".
    expect(screen.getAllByText(/it is not zero/).length).toBeGreaterThan(0);
  });

  it('reports a never-run sweep as unknown rather than "no orphans"', async () => {
    mocked.getSecretSweep.mockResolvedValue({
      ...CLEAN_SWEEP,
      status: 'never_run',
      ran_at: null,
      orphan_count: null,
      scanned: null,
      store: null,
      mode: null,
    });
    renderSubPage(<AdminOverview />);

    expect(await screen.findByText('Orphan-secret sweep has never run')).toBeInTheDocument();
    expect(screen.getByText('Never run')).toBeInTheDocument();
  });

  it('lists a trigger env mismatch with a way to reach the suites', async () => {
    mockNearMisses.mockResolvedValue([
      {
        provider: 'airflow',
        pipeline_or_dag_id: 'flow_a',
        run_env: 'prod',
        binding_env: 'qa',
        updated_at: '2026-09-05T08:00:00Z',
      },
    ]);
    renderSubPage(<AdminOverview />);

    expect(await screen.findByText('Trigger env mismatch — flow_a')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'View suites' })).toHaveAttribute('href', '/suites');
  });
});

describe('AdminOverview workspace health', () => {
  it('never verifies the audit chain on mount', async () => {
    renderSubPage(<AdminOverview />);

    expect(await screen.findByText('Not verified this session')).toBeInTheDocument();
    expect(mocked.verifyAuditChain).not.toHaveBeenCalled();
  });

  it('verifies the chain only when asked', async () => {
    mocked.verifyAuditChain.mockResolvedValue({
      status: 'ok',
      verified_count: 12,
      unverifiable_legacy_count: 0,
      chain_head_hash: 'abc',
      anchor_mode: 'none',
      first_break: null,
    });
    renderSubPage(<AdminOverview />);

    await userEvent.click(await screen.findByRole('button', { name: 'Verify now' }));

    expect(await screen.findByText('Intact')).toBeInTheDocument();
    expect(mocked.verifyAuditChain).toHaveBeenCalledTimes(1);
  });

  it('re-polls after a sweep run and shows the new report', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    mocked.runSecretSweep.mockResolvedValue({ status: 'queued', task_id: 't1' });
    mocked.getSecretSweep
      .mockResolvedValueOnce(CLEAN_SWEEP)
      .mockResolvedValueOnce(CLEAN_SWEEP) // the worker hasn't recorded yet
      .mockResolvedValue({ ...CLEAN_SWEEP, ran_at: '2026-09-05T09:10:00Z', orphan_count: 2 });
    renderSubPage(<AdminOverview />);

    await user.click(await screen.findByRole('button', { name: 'Run sweep' }));
    await vi.advanceTimersByTimeAsync(6000);

    await waitFor(() => expect(screen.getByText('2 orphan(s)')).toBeInTheDocument());
    expect(mocked.runSecretSweep).toHaveBeenCalledTimes(1);
  });

  it('says the sweep is still queued rather than showing the old numbers as new', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    mocked.runSecretSweep.mockResolvedValue({ status: 'queued', task_id: 't1' });
    renderSubPage(<AdminOverview />);

    await user.click(await screen.findByRole('button', { name: 'Run sweep' }));
    await vi.advanceTimersByTimeAsync(40_000);

    await waitFor(() => expect(screen.getByText('Sweep queued')).toBeInTheDocument());
  });

  it('fetches only its own endpoints — no member or suite listings', async () => {
    renderSubPage(<AdminOverview />);

    await screen.findByText('6');
    expect(mocked.getAdminOverview).toHaveBeenCalledTimes(1);
    expect(mocked.listAdminUsers).not.toHaveBeenCalled();
    expect(mocked.listAdminSuites).not.toHaveBeenCalled();
    expect(mocked.listAdminAccess).not.toHaveBeenCalled();
  });
});
