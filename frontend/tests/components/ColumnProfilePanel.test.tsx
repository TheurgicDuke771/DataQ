import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { profileColumns } from '../../src/api/suites';
import { ColumnProfilePanel } from '../../src/components/checks/ColumnProfilePanel';

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, profileColumns: vi.fn() };
});

const mockProfile = vi.mocked(profileColumns);

const TARGET = { table: 'ORDERS', schema: 'PUBLIC' };

function Harness({ target, column }: { target: Record<string, unknown> | null; column?: string }) {
  return (
    <AntApp>
      <ColumnProfilePanel suiteId="s1" target={target} column={column} />
    </AntApp>
  );
}

afterEach(() => vi.clearAllMocks());

describe('ColumnProfilePanel', () => {
  it('is disabled with a reason when the suite has no table/file target', async () => {
    render(<Harness target={null} column="order_id" />);
    const user = userEvent.setup();
    await user.click(screen.getByText('Column profiler')); // expand the collapse
    expect(screen.getByRole('button', { name: 'Profile' })).toBeDisabled();
    expect(screen.getByText(/Set a table or file target/)).toBeInTheDocument();
  });

  it('is disabled with a reason until a column is entered', async () => {
    render(<Harness target={TARGET} column={undefined} />);
    const user = userEvent.setup();
    await user.click(screen.getByText('Column profiler'));
    expect(screen.getByRole('button', { name: 'Profile' })).toBeDisabled();
    expect(screen.getByText(/Enter a column to profile/)).toBeInTheDocument();
  });

  it('pre-fills the check column and profiles it against the suite target', async () => {
    mockProfile.mockResolvedValue({
      row_count: 1000,
      columns: [
        {
          column: 'order_id',
          null_count: 5,
          null_fraction: 0.005,
          distinct_count: 990,
          min_value: 1,
          max_value: 9999,
          top_values: [
            { value: 'A', count: 12 },
            { value: 'B', count: 7 },
          ],
        },
      ],
      table: 'ORDERS',
      schema: 'PUBLIC',
    });
    render(<Harness target={TARGET} column="order_id" />);
    const user = userEvent.setup();
    await user.click(screen.getByText('Column profiler'));

    await user.click(screen.getByRole('button', { name: 'Profile' }));

    // Sends the pre-filled column + the suite's table/schema identity.
    await waitFor(() =>
      expect(mockProfile).toHaveBeenCalledWith('s1', {
        columns: ['order_id'],
        table: 'ORDERS',
        schema: 'PUBLIC',
        catalog: undefined,
        path: undefined,
        file_format: undefined,
      }),
    );
    // The stats render: distinct + null + min/max + a top-values row.
    expect(await screen.findByText('5 (0.5%)')).toBeInTheDocument();
    expect(screen.getByText('990')).toBeInTheDocument();
    expect(screen.getByText('9999')).toBeInTheDocument();
    expect(screen.getByText('Top value')).toBeInTheDocument();
  });

  it('surfaces the API error message when the profile fails', async () => {
    mockProfile.mockRejectedValue(new Error('profile could not execute against the datasource'));
    render(<Harness target={TARGET} column="order_id" />);
    const user = userEvent.setup();
    await user.click(screen.getByText('Column profiler'));

    await user.click(screen.getByRole('button', { name: 'Profile' }));

    expect(await screen.findByText('Profile failed')).toBeInTheDocument();
    expect(
      screen.getByText('profile could not execute against the datasource'),
    ).toBeInTheDocument();
  });
});
