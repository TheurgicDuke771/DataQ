import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { cancelRun, getRunProgress, type RunProgress, type RunStatus } from '../../src/api/runs';
import { LiveRunProgress } from '../../src/components/runs/LiveRunProgress';

vi.mock('../../src/api/runs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/runs')>();
  return { ...actual, getRunProgress: vi.fn(), cancelRun: vi.fn() };
});

const mockProgress = vi.mocked(getRunProgress);
const mockCancel = vi.mocked(cancelRun);

function progress(status: RunStatus, overrides: Partial<RunProgress> = {}): RunProgress {
  return {
    run_id: 'r1',
    suite_id: 's1',
    status,
    total_checks: 2,
    completed_checks: status === 'succeeded' ? 2 : 1,
    counts: {},
    checks: [
      { check_id: 'c1', name: 'not-null id', status: 'pass' },
      { check_id: 'c2', name: 'row count', status: status === 'succeeded' ? 'fail' : null },
    ],
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

function renderDrawer(props: Partial<Parameters<typeof LiveRunProgress>[0]> = {}) {
  return render(
    <MemoryRouter>
      <AntApp>
        <LiveRunProgress
          runId="r1"
          suiteName="Orders"
          canManage
          pollMs={1_000_000}
          onClose={() => {}}
          {...props}
        />
      </AntApp>
    </MemoryRouter>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('LiveRunProgress', () => {
  it('is closed (no content) when runId is null', () => {
    mockProgress.mockResolvedValue(progress('running'));
    renderDrawer({ runId: null });
    expect(screen.queryByText(/Run progress/)).not.toBeInTheDocument();
  });

  it('renders per-check status — a pending check spins, a resolved one tags', async () => {
    mockProgress.mockResolvedValue(progress('running'));
    renderDrawer();

    expect(await screen.findByText('not-null id')).toBeInTheDocument();
    expect(screen.getByText('pass')).toBeInTheDocument();
    // The unresolved check shows the pending affordance, not a status tag.
    expect(screen.getByText('pending')).toBeInTheDocument();
    expect(screen.getByText('1 / 2 checks')).toBeInTheDocument();
    // The results link is always offered — even mid-run — so closing the drawer
    // never strands the run.
    expect(screen.getByText('View full results →')).toBeInTheDocument();
  });

  it('renders the per-status histogram, omitting zero buckets (#316)', async () => {
    mockProgress.mockResolvedValue(
      progress('succeeded', { counts: { pass: 3, fail: 1, skip: 0 } }),
    );
    renderDrawer();

    expect(await screen.findByText('pass · 3')).toBeInTheDocument();
    expect(screen.getByText('fail · 1')).toBeInTheDocument();
    // A zero bucket is dropped rather than shown as `skip · 0`.
    expect(screen.queryByText(/skip/)).not.toBeInTheDocument();
  });

  it('shows no histogram while all checks are still pending', async () => {
    mockProgress.mockResolvedValue(progress('running', { counts: {} }));
    renderDrawer();

    await screen.findByText('not-null id');
    // No `status · count` histogram tag (the drawer title also uses a middot).
    expect(screen.queryByText(/\w+ · \d+/)).not.toBeInTheDocument();
  });

  it('polls until the run is terminal, then stops and links to results', async () => {
    mockProgress
      .mockResolvedValueOnce(progress('running'))
      .mockResolvedValue(progress('succeeded'));
    renderDrawer({ pollMs: 5 });

    // Wait on the terminal signal itself — the `succeeded` status tag — not the
    // "View full results →" link, which renders unconditionally from the first
    // (running) poll onward, so it can't tell us the run has reached terminal.
    // (This bare getByText after the link was the CI flake — the second poll
    // hadn't landed `succeeded` yet on slow runners. #640.)
    expect(await screen.findByText('succeeded')).toBeInTheDocument();
    expect(screen.getByText('View full results →')).toBeInTheDocument();
    const callsAtTerminal = mockProgress.mock.calls.length;
    // Give a couple of intervals: no further polling once terminal.
    await new Promise((r) => setTimeout(r, 30));
    expect(mockProgress.mock.calls.length).toBe(callsAtTerminal);
  });

  it('cancels an in-flight run for an editor', async () => {
    mockProgress.mockResolvedValue(progress('running'));
    mockCancel.mockResolvedValue({
      id: 'r1',
      suite_id: 's1',
      status: 'cancelled',
      triggered_by: null,
      started_at: null,
      finished_at: '2026-06-20T00:00:00Z',
      created_at: '2026-06-20T00:00:00Z',
      checks_total: 0,
      checks_passed: 0,
      worst_severity: null,
    });
    const user = userEvent.setup();
    renderDrawer();
    await screen.findByText('not-null id');

    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(mockCancel).toHaveBeenCalledWith('r1'));
    // The drawer reflects the terminal state and drops the cancel control.
    expect(await screen.findByText('cancelled')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
    // A never-run check on a now-terminal run reads "not run", not a live spinner.
    expect(screen.getByText('not run')).toBeInTheDocument();
    expect(screen.queryByText('pending')).not.toBeInTheDocument();
  });

  it('does not flip back to running when a late poll resolves after a cancel', async () => {
    // Backend cancel is cooperative — a poll already in flight can still read
    // `running`. The cancelled state must stick and polling must stop.
    mockProgress.mockResolvedValue(progress('running'));
    mockCancel.mockResolvedValue({
      id: 'r1',
      suite_id: 's1',
      status: 'cancelled',
      triggered_by: null,
      started_at: null,
      finished_at: '2026-06-20T00:00:00Z',
      created_at: '2026-06-20T00:00:00Z',
      checks_total: 0,
      checks_passed: 0,
      worst_severity: null,
    });
    const user = userEvent.setup();
    renderDrawer({ pollMs: 5 });
    await screen.findByText('not-null id');

    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await screen.findByText('cancelled');

    const callsAtCancel = mockProgress.mock.calls.length;
    // Let any in-flight / would-be-scheduled polls settle.
    await new Promise((r) => setTimeout(r, 40));
    expect(screen.getByText('cancelled')).toBeInTheDocument();
    expect(screen.queryByText('running')).not.toBeInTheDocument();
    // Polling has stopped — no further progress fetches after the cancel.
    expect(mockProgress.mock.calls.length).toBe(callsAtCancel);
  });

  it('recovers from a transient poll error and keeps polling', async () => {
    mockProgress.mockRejectedValueOnce(new Error('blip')).mockResolvedValue(progress('succeeded'));
    renderDrawer({ pollMs: 5 });

    // The first poll failed, but polling self-heals to the terminal state.
    expect(await screen.findByText('succeeded')).toBeInTheDocument();
  });

  it('hides the cancel button for a terminal run', async () => {
    mockProgress.mockResolvedValue(progress('succeeded'));
    renderDrawer();
    await screen.findByText('not-null id');
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
  });

  it('hides the cancel button for a non-editor', async () => {
    mockProgress.mockResolvedValue(progress('running'));
    renderDrawer({ canManage: false });
    await screen.findByText('not-null id');
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
  });

  it('surfaces a fetch error when there is no progress to show', async () => {
    mockProgress.mockRejectedValue(new Error('boom'));
    renderDrawer();
    expect(await screen.findByText('Failed to load run progress')).toBeInTheDocument();
  });
});
