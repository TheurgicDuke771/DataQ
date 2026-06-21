import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  createSchedule,
  deleteSchedule,
  listSchedules,
  type Schedule,
  updateSchedule,
} from '../../src/api/schedules';
import { SchedulesPanel } from '../../src/components/suites/SchedulesPanel';

vi.mock('../../src/api/schedules', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/schedules')>();
  return {
    ...actual, // keep timezoneOptions real
    listSchedules: vi.fn(),
    createSchedule: vi.fn(),
    updateSchedule: vi.fn(),
    deleteSchedule: vi.fn(),
  };
});

const mockList = vi.mocked(listSchedules);
const mockCreate = vi.mocked(createSchedule);
const mockUpdate = vi.mocked(updateSchedule);
const mockDelete = vi.mocked(deleteSchedule);

const SCHEDULE: Schedule = {
  id: 'sch1',
  suite_id: 's1',
  cron: '0 9 * * 1-5',
  timezone: 'UTC',
  enabled: true,
  next_run_at: '2026-06-21T09:00:00Z',
  last_run_at: null,
};

function renderPanel(props: Partial<Parameters<typeof SchedulesPanel>[0]> = {}) {
  return render(
    <AntApp>
      <SchedulesPanel suiteId="s1" canManage {...props} />
    </AntApp>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('SchedulesPanel', () => {
  it('lists schedules with cron and a pause control', async () => {
    mockList.mockResolvedValue([SCHEDULE]);
    renderPanel();

    expect(await screen.findByText('0 9 * * 1-5')).toBeInTheDocument();
    // An enabled schedule offers a Pause toggle, labelled by cron + timezone
    // (cron alone isn't unique — the same cron can run in two timezones).
    expect(screen.getByRole('switch', { name: 'Pause 0 9 * * 1-5 (UTC)' })).toBeInTheDocument();
  });

  it('shows an empty state when there are no schedules', async () => {
    mockList.mockResolvedValue([]);
    renderPanel();

    expect(
      await screen.findByText('No schedules — this suite runs only on manual / triggered runs.'),
    ).toBeInTheDocument();
  });

  it('creates a schedule from the cron + timezone form (UTC default)', async () => {
    mockList.mockResolvedValue([]);
    mockCreate.mockResolvedValue(SCHEDULE);
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText(/No schedules/);

    await user.type(screen.getByLabelText('Cron expression'), '0 9 * * *');
    await user.click(screen.getByRole('button', { name: 'Add' }));

    await waitFor(() =>
      expect(mockCreate).toHaveBeenCalledWith({
        suite_id: 's1',
        cron: '0 9 * * *',
        timezone: 'UTC',
      }),
    );
  });

  it('pauses a schedule via the toggle', async () => {
    mockList.mockResolvedValue([SCHEDULE]);
    mockUpdate.mockResolvedValue({ ...SCHEDULE, enabled: false });
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('0 9 * * 1-5');

    await user.click(screen.getByRole('switch', { name: 'Pause 0 9 * * 1-5 (UTC)' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith('sch1', { enabled: false }));
  });

  it('deletes a schedule after confirmation', async () => {
    mockList.mockResolvedValue([SCHEDULE]);
    mockDelete.mockResolvedValue();
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('0 9 * * 1-5');

    await user.click(screen.getByRole('button', { name: 'Remove 0 9 * * 1-5 (UTC)' }));
    // Destructive → gated behind a confirm modal; nothing deleted until confirmed.
    expect(mockDelete).not.toHaveBeenCalled();
    await user.click(await screen.findByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith('sch1'));
  });

  it('is read-only for a non-editor (no add form, no toggle/remove)', async () => {
    mockList.mockResolvedValue([SCHEDULE]);
    renderPanel({ canManage: false });
    await screen.findByText('0 9 * * 1-5');

    expect(screen.queryByRole('button', { name: 'Add' })).not.toBeInTheDocument();
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Remove 0 9 * * 1-5 (UTC)' }),
    ).not.toBeInTheDocument();
    // The enabled state is shown read-only as a tag.
    expect(screen.getByText('enabled')).toBeInTheDocument();
  });
});
