import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Connection, listConnections } from '../../src/api/connections';
import { getSuite, type Suite, updateSuite } from '../../src/api/suites';
import { SuiteEdit } from '../../src/pages/SuiteEdit';

vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return { ...actual, listConnections: vi.fn() };
});

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, getSuite: vi.fn(), updateSuite: vi.fn() };
});

const mockGetSuite = vi.mocked(getSuite);
const mockListConnections = vi.mocked(listConnections);
const mockUpdate = vi.mocked(updateSuite);

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
    description: 'old desc',
    connection_id: 'conn1',
    target: null,
    created_by: 'u1',
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/suites/s1/edit']}>
      <AntApp>
        <Routes>
          <Route path="/suites/:suiteId/edit" element={<SuiteEdit />} />
          <Route path="/suites/:suiteId" element={<div>Suite detail</div>} />
        </Routes>
      </AntApp>
    </MemoryRouter>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('SuiteEdit', () => {
  it('prefills, locks the connection, updates, and navigates back', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockGetSuite.mockResolvedValue(suite());
    mockUpdate.mockResolvedValue(suite({ name: 'orders-suite-2' }));
    renderPage();

    await waitFor(() => expect(screen.getByLabelText('Name')).toHaveValue('orders-suite'));
    // Connection is locked in edit mode.
    expect(screen.getByRole('combobox')).toBeDisabled();

    await user.clear(screen.getByLabelText('Name'));
    await user.type(screen.getByLabelText('Name'), 'orders-suite-2');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate).toHaveBeenCalledWith('s1', {
      name: 'orders-suite-2',
      description: 'old desc',
      target: null,
    });
    expect(await screen.findByText('Suite detail')).toBeInTheDocument();
  });

  it('prefills the existing target and round-trips it on save', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockGetSuite.mockResolvedValue(
      suite({ target: { table: 'ANALYTICS.ORDERS', schema: 'PUBLIC' } }),
    );
    mockUpdate.mockResolvedValue(suite());
    renderPage();

    await waitFor(() => expect(screen.getByLabelText('Table')).toHaveValue('ANALYTICS.ORDERS'));
    expect(screen.getByLabelText('Schema (optional)')).toHaveValue('PUBLIC');

    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate).toHaveBeenCalledWith('s1', {
      name: 'orders-suite',
      description: 'old desc',
      target: { table: 'ANALYTICS.ORDERS', schema: 'PUBLIC' },
    });
  });

  it('refuses to clear an existing target (backend keeps the last one)', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockGetSuite.mockResolvedValue(suite({ target: { table: 'ANALYTICS.ORDERS' } }));
    renderPage();

    await waitFor(() => expect(screen.getByLabelText('Table')).toHaveValue('ANALYTICS.ORDERS'));
    await user.clear(screen.getByLabelText('Table'));
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText(/can’t be removed once set/)).toBeInTheDocument();
    expect(mockUpdate).not.toHaveBeenCalled();
  });
});
