import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Connection, getConnection, updateConnection } from '../../src/api/connections';
import { ConnectionEdit } from '../../src/pages/ConnectionEdit';

vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return { ...actual, getConnection: vi.fn(), updateConnection: vi.fn() };
});

const mockGet = vi.mocked(getConnection);
const mockUpdate = vi.mocked(updateConnection);

const existing: Connection = {
  id: 'c1',
  name: 'sf-dev',
  type: 'snowflake',
  env: 'dev',
  config: {
    account: 'acc1',
    user: 'svc',
    database: 'DB',
    schema: 'SC',
    warehouse: 'WH',
    auth_type: 'password',
  },
  has_secret: true,
  created_by: 'u1',
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/connections/c1/edit']}>
      <AntApp>
        <Routes>
          <Route path="/connections/:connectionId/edit" element={<ConnectionEdit />} />
          <Route path="/connections" element={<div>Connections list</div>} />
        </Routes>
      </AntApp>
    </MemoryRouter>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('ConnectionEdit', () => {
  it('shows type + env read-only (immutable) and omits the secret', async () => {
    mockGet.mockResolvedValue(existing);
    renderPage();

    await waitFor(() => expect(screen.getByLabelText('Account')).toHaveValue('acc1'));
    expect(screen.getByText('Snowflake')).toBeInTheDocument();
    expect(screen.getByText('DEV')).toBeInTheDocument();
    // Type/Environment are display-only (no editable control); secret is omitted
    // (rotation is the Re-auth flow).
    expect(screen.queryByLabelText('Type')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Environment')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument();
  });

  it('prefills, submits a PATCH, and navigates back to the list', async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValue(existing);
    mockUpdate.mockResolvedValue({ ...existing, name: 'sf-dev-2' });
    renderPage();

    await waitFor(() => expect(screen.getByLabelText('Account')).toHaveValue('acc1'));
    expect(screen.getByLabelText('Name')).toHaveValue('sf-dev');

    await user.clear(screen.getByLabelText('Name'));
    await user.type(screen.getByLabelText('Name'), 'sf-dev-2');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate).toHaveBeenCalledWith(
      'c1',
      expect.objectContaining({
        name: 'sf-dev-2',
        config: expect.objectContaining({ account: 'acc1', auth_type: 'password' }),
      }),
    );
    expect(await screen.findByText('Connections list')).toBeInTheDocument();
  });

  it('surfaces a load error', async () => {
    mockGet.mockRejectedValue(new Error('not found'));
    renderPage();

    expect(await screen.findByText('Failed to load connection')).toBeInTheDocument();
  });
});
