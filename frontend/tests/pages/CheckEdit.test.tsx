import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Connection, getConnection } from '../../src/api/connections';
import {
  type Check,
  getCheck,
  getSuite,
  listCheckVersions,
  type Suite,
  updateCheck,
} from '../../src/api/suites';
import { CheckEdit } from '../../src/pages/CheckEdit';

vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return { ...actual, getConnection: vi.fn() };
});

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return {
    ...actual,
    getSuite: vi.fn(),
    getCheck: vi.fn(),
    updateCheck: vi.fn(),
    listCheckVersions: vi.fn(),
  };
});

const mockGetSuite = vi.mocked(getSuite);
const mockGetCheck = vi.mocked(getCheck);
const mockGetConnection = vi.mocked(getConnection);
const mockUpdate = vi.mocked(updateCheck);
const mockVersions = vi.mocked(listCheckVersions);

const suite: Suite = {
  id: 's1',
  name: 'orders-suite',
  description: null,
  connection_id: 'conn1',
  target: { table: 'ORDERS' },
  created_by: 'u1',
};

const connection: Connection = {
  id: 'conn1',
  name: 'sf-dev',
  type: 'snowflake',
  env: 'dev',
  config: {},
  has_secret: true,
  created_by: 'u1',
};

const existing: Check = {
  id: 'chk1',
  suite_id: 's1',
  name: 'amount range',
  kind: 'expectation',
  expectation_type: 'expect_column_values_to_be_between',
  config: { column: 'amount', min_value: 0, max_value: 100 },
  warn_threshold: 5,
  fail_threshold: 10,
  critical_threshold: null,
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/suites/s1/checks/chk1/edit']}>
      <AntApp>
        <Routes>
          <Route path="/suites/:suiteId/checks/:checkId/edit" element={<CheckEdit />} />
          <Route path="/suites/:suiteId" element={<div>Suite detail</div>} />
        </Routes>
      </AntApp>
    </MemoryRouter>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('CheckEdit', () => {
  it('prefills config + thresholds, updates, and navigates back', async () => {
    const user = userEvent.setup();
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(existing);
    mockGetConnection.mockResolvedValue(connection);
    mockUpdate.mockResolvedValue(existing);
    renderPage();

    await waitFor(() => expect(screen.getByLabelText('Column')).toHaveValue('amount'));
    expect(screen.getByLabelText('Warn ≥')).toHaveValue('5');

    await user.clear(screen.getByLabelText('Name'));
    await user.type(screen.getByLabelText('Name'), 'amount range v2');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate).toHaveBeenCalledWith('s1', 'chk1', {
      name: 'amount range v2',
      kind: 'expectation',
      expectation_type: 'expect_column_values_to_be_between',
      config: { column: 'amount', min_value: 0, max_value: 100 },
      warn_threshold: 5,
      fail_threshold: 10,
      critical_threshold: null,
    });
    expect(await screen.findByText('Suite detail')).toBeInTheDocument();
  });

  it('opens the version-history drawer from the History button (#280)', async () => {
    const user = userEvent.setup();
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(existing);
    mockGetConnection.mockResolvedValue(connection);
    mockVersions.mockResolvedValue([
      {
        version_no: 1,
        name: 'amount range',
        kind: 'expectation',
        expectation_type: 'expect_column_values_to_be_between',
        config: { column: 'amount' },
        warn_threshold: null,
        fail_threshold: null,
        critical_threshold: null,
        changed_by: 'u1',
        changed_by_name: 'Ed Editor',
        created_at: '2026-06-15T10:00:00Z',
      },
    ]);
    renderPage();
    await waitFor(() => expect(screen.getByLabelText('Column')).toHaveValue('amount'));

    await user.click(screen.getByRole('button', { name: /History/ }));

    expect(await screen.findByText(/History — /)).toBeInTheDocument();
    await waitFor(() => expect(mockVersions).toHaveBeenCalledWith('s1', 'chk1'));
    expect(await screen.findByText('v1')).toBeInTheDocument();
  });

  it('still loads when the connection is unreadable (shared suite)', async () => {
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(existing);
    mockGetConnection.mockRejectedValue(new Error('forbidden'));
    renderPage();

    // The form renders from the check even though the connection 403s.
    await waitFor(() => expect(screen.getByLabelText('Column')).toHaveValue('amount'));
  });
});
