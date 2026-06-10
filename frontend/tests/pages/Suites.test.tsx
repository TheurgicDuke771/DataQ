import { App as AntApp } from 'antd';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Connection, listConnections } from '../../src/api/connections';
import {
  type Check,
  deleteCheck,
  deleteSuite,
  listChecks,
  listSuites,
  type Suite,
} from '../../src/api/suites';
import { Suites } from '../../src/pages/Suites';

vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return { ...actual, listConnections: vi.fn() };
});

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return {
    ...actual,
    listSuites: vi.fn(),
    listChecks: vi.fn(),
    deleteSuite: vi.fn(),
    deleteCheck: vi.fn(),
  };
});

const mockListSuites = vi.mocked(listSuites);
const mockListConnections = vi.mocked(listConnections);
const mockListChecks = vi.mocked(listChecks);
const mockDeleteSuite = vi.mocked(deleteSuite);
const mockDeleteCheck = vi.mocked(deleteCheck);

const connection: Connection = {
  id: 'conn1',
  name: 'sf-dev',
  type: 'snowflake',
  env: 'dev',
  config: {},
  has_secret: true,
  created_by: 'u1',
};

function suite(overrides: Partial<Suite> = {}): Suite {
  return {
    id: 's1',
    name: 'orders-suite',
    description: 'Checks for the orders table',
    connection_id: 'conn1',
    created_by: 'u1',
    ...overrides,
  };
}

function check(overrides: Partial<Check> = {}): Check {
  return {
    id: 'chk1',
    suite_id: 's1',
    name: 'order_id not null',
    kind: 'expectation',
    expectation_type: 'expect_column_values_to_not_be_null',
    config: {},
    warn_threshold: null,
    fail_threshold: null,
    critical_threshold: null,
    ...overrides,
  };
}

function renderPage() {
  return render(
    <AntApp>
      <Suites />
    </AntApp>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('Suites', () => {
  it('lists suites and shows the detail panel on selection', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([suite()]);
    mockListChecks.mockResolvedValue([check()]);

    renderPage();

    await user.click(await screen.findByText('orders-suite'));

    // Detail panel: connection context + the check.
    expect(await screen.findByText('order_id not null')).toBeInTheDocument();
    expect(screen.getByText('sf-dev · Snowflake')).toBeInTheDocument();
    expect(screen.getByText('DEV')).toBeInTheDocument();
    expect(mockListChecks).toHaveBeenCalledWith('s1');
  });

  it('shows an empty state when there are no suites', async () => {
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([]);

    renderPage();

    expect(
      await screen.findByText('No suites yet — create one to start authoring checks.'),
    ).toBeInTheDocument();
  });

  it('warns when connections fail to load (create depends on them)', async () => {
    mockListConnections.mockRejectedValue(new Error('conn down'));
    mockListSuites.mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText('Couldn’t load connections')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /New suite/ })).toBeDisabled();
  });

  it('surfaces a load error', async () => {
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockRejectedValue(new Error('boom'));

    renderPage();

    expect(await screen.findByText('Failed to load suites')).toBeInTheDocument();
    expect(screen.getByText('boom')).toBeInTheDocument();
  });

  it('deletes a check from the detail panel after confirming', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([suite()]);
    mockListChecks.mockResolvedValue([check()]);
    mockDeleteCheck.mockResolvedValue();

    renderPage();
    await user.click(await screen.findByText('orders-suite'));
    await screen.findByText('order_id not null');

    // The check row's own Delete (link button), scoped to its confirm dialog.
    const checkRow = screen.getByText('order_id not null').closest('li') as HTMLElement;
    await user.click(within(checkRow).getByRole('button', { name: 'Delete' }));

    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(mockDeleteCheck).toHaveBeenCalledWith('s1', 'chk1'));
  });

  it('deletes a suite via the detail panel after confirming', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([suite()]);
    mockListChecks.mockResolvedValue([]);
    mockDeleteSuite.mockResolvedValue();

    renderPage();
    await user.click(await screen.findByText('orders-suite'));
    await user.click(await screen.findByRole('button', { name: 'Delete' }));

    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(mockDeleteSuite).toHaveBeenCalledWith('s1'));
  });
});
