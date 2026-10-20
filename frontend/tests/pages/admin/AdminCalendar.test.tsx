import { App as AntApp } from 'antd';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AxiosError, AxiosHeaders } from 'axios';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  createSeasonalPeriod,
  deleteSeasonalPeriod,
  listSeasonalPeriods,
  type SeasonalPeriod,
  updateSeasonalPeriod,
} from '../../../src/api/seasonalPeriods';
import { AdminCalendar } from '../../../src/pages/admin/AdminCalendar';

vi.mock('../../../src/api/seasonalPeriods', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../src/api/seasonalPeriods')>();
  return {
    ...actual,
    listSeasonalPeriods: vi.fn(),
    createSeasonalPeriod: vi.fn(),
    updateSeasonalPeriod: vi.fn(),
    deleteSeasonalPeriod: vi.fn(),
  };
});

const mockList = vi.mocked(listSeasonalPeriods);
const mockCreate = vi.mocked(createSeasonalPeriod);
const mockUpdate = vi.mocked(updateSeasonalPeriod);
const mockDelete = vi.mocked(deleteSeasonalPeriod);

const SUMMER: SeasonalPeriod = {
  id: 'p1',
  name: 'Summer sale 2026',
  kind: 'dated',
  start_month: null,
  start_day: null,
  end_month: null,
  end_day: null,
  start_date: '2026-07-01',
  end_date: '2026-07-14',
  series: 'Summer sale',
  note: null,
  attached_checks: 2,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

function apiError(status: number, code: string, message: string, detail: object) {
  const data = { error: { code, message, detail } };
  return new AxiosError(message, String(status), undefined, undefined, {
    status,
    statusText: '',
    headers: {},
    config: { headers: new AxiosHeaders() },
    data,
  });
}

/** Both month selects share option titles; the dropdown just opened is the last one. */
async function pickMonth(user: ReturnType<typeof userEvent.setup>, month: string, index: number) {
  await user.click(screen.getAllByRole('combobox')[index]);
  const options = await screen.findAllByTitle(month);
  await user.click(options[options.length - 1]);
}

function renderPage() {
  return render(
    <MemoryRouter>
      <AntApp>
        <AdminCalendar />
      </AntApp>
    </MemoryRouter>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('AdminCalendar', () => {
  it('lists entries with their span, series and attached checks', async () => {
    mockList.mockResolvedValue([SUMMER]);
    renderPage();
    expect(await screen.findByText('Summer sale 2026')).toBeInTheDocument();
    expect(screen.getByText('2026-07-01 – 2026-07-14')).toBeInTheDocument();
    expect(screen.getByText('Summer sale')).toBeInTheDocument();
    expect(screen.getByText('An entry changes no check until it is attached')).toBeInTheDocument();
  });

  it('adds a recurring entry, sending no dated fields', async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue([]);
    mockCreate.mockResolvedValue({ ...SUMMER, id: 'p2' });
    renderPage();
    await user.click(await screen.findByRole('button', { name: /New entry/ }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Name'), '  Carnival ');
    await pickMonth(user, 'Feb', 0);
    await user.type(within(dialog).getByLabelText('From day'), '27');
    await pickMonth(user, 'Mar', 1);
    await user.type(within(dialog).getByLabelText('To day'), '3');
    await user.click(within(dialog).getByRole('button', { name: 'Add' }));
    await waitFor(() =>
      expect(mockCreate).toHaveBeenCalledWith({
        name: 'Carnival',
        kind: 'recurring',
        start_month: 2,
        start_day: 27,
        end_month: 3,
        end_day: 3,
        start_date: null,
        end_date: null,
        series: null,
        note: null,
      }),
    );
  });

  it('refuses a dated entry that ends before it starts, without calling the API', async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue([]);
    renderPage();
    await user.click(await screen.findByRole('button', { name: /New entry/ }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Name'), 'Sale');
    await user.click(within(dialog).getByLabelText(/On specific dates/));
    await user.type(within(dialog).getByLabelText('First day'), '2026-07-10');
    await user.type(within(dialog).getByLabelText('Last day'), '2026-07-01');
    await user.click(within(dialog).getByRole('button', { name: 'Add' }));
    expect(await within(dialog).findByText('The last day is before the first')).toBeInTheDocument();
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it('edits an entry, keeping its kind and fields', async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue([SUMMER]);
    mockUpdate.mockResolvedValue(SUMMER);
    renderPage();
    await user.click(await screen.findByRole('button', { name: 'Edit Summer sale 2026' }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Note (optional)'), 'moved a week');
    await user.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(mockUpdate).toHaveBeenCalledWith('p1', {
        name: 'Summer sale 2026',
        kind: 'dated',
        start_month: null,
        start_day: null,
        end_month: null,
        end_day: null,
        start_date: '2026-07-01',
        end_date: '2026-07-14',
        series: 'Summer sale',
        note: 'moved a week',
      }),
    );
  });

  it('puts a taken name on the name field', async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue([]);
    mockCreate.mockRejectedValue(
      apiError(409, 'seasonal_period_name_taken', 'a seasonal period named "Sale" exists', {}),
    );
    renderPage();
    await user.click(await screen.findByRole('button', { name: /New entry/ }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Name'), 'Sale');
    await pickMonth(user, 'Jan', 0);
    await user.type(within(dialog).getByLabelText('From day'), '1');
    await pickMonth(user, 'Feb', 1);
    await user.type(within(dialog).getByLabelText('To day'), '2');
    await user.click(within(dialog).getByRole('button', { name: 'Add' }));
    expect(
      await within(dialog).findByText('a seasonal period named "Sale" exists'),
    ).toBeInTheDocument();
  });

  it('names the checks that still attach an entry when its delete is refused', async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue([SUMMER]);
    mockDelete.mockRejectedValue(
      apiError(409, 'seasonal_period_in_use', '2 check(s) still name this seasonal period', {
        checks: [
          { id: 'k1', name: 'orders per day', suite_id: 's1', suite_name: 'orders' },
          { id: 'k2', name: 'revenue per day', suite_id: 's2', suite_name: 'finance' },
        ],
      }),
    );
    renderPage();
    await user.click(await screen.findByRole('button', { name: 'Delete Summer sale 2026' }));
    await user.click(await screen.findByRole('button', { name: 'Delete' }));
    const blocked = await screen.findByTestId('calendar-delete-blocked');
    expect(blocked).toHaveTextContent('“Summer sale 2026” is still attached to 2 checks');
    expect(within(blocked).getByRole('link', { name: 'orders per day' })).toHaveAttribute(
      'href',
      '/suites/s1/checks/k1/edit',
    );
    expect(within(blocked).getByText('in finance')).toBeInTheDocument();
  });

  it('deletes an entry no check uses and reloads', async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue([{ ...SUMMER, attached_checks: 0 }]);
    mockDelete.mockResolvedValue();
    renderPage();
    await user.click(await screen.findByRole('button', { name: 'Delete Summer sale 2026' }));
    await user.click(await screen.findByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith('p1'));
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));
  });
});
