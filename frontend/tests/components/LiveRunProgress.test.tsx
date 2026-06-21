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
  });

  it('polls until the run is terminal, then stops and links to results', async () => {
    mockProgress
      .mockResolvedValueOnce(progress('running'))
      .mockResolvedValue(progress('succeeded'));
    renderDrawer({ pollMs: 5 });

    expect(await screen.findByText('View full results →')).toBeInTheDocument();
    expect(screen.getByText('succeeded')).toBeInTheDocument();
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
    });
    const user = userEvent.setup();
    renderDrawer();
    await screen.findByText('not-null id');

    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(mockCancel).toHaveBeenCalledWith('r1'));
    // The drawer reflects the terminal state and drops the cancel control.
    expect(await screen.findByText('cancelled')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
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
