import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Connection, updateConnection } from '../../src/api/connections';
import { ConnectionDrawer } from '../../src/components/connections/ConnectionDrawer';

// The drawer is edit-only — creating a connection is the dedicated /connections/new
// page (ConnectionNew). So only the update path is mocked + exercised here.
vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return { ...actual, updateConnection: vi.fn() };
});

const mockUpdate = vi.mocked(updateConnection);

function renderDrawer(props: Partial<Parameters<typeof ConnectionDrawer>[0]> = {}) {
  return render(
    <AntApp>
      <ConnectionDrawer open onClose={vi.fn()} onSaved={vi.fn()} {...props} />
    </AntApp>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('ConnectionDrawer — edit', () => {
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

  it('shows type + env read-only (immutable) with no way to change them', async () => {
    renderDrawer({ connection: existing });

    await waitFor(() => expect(screen.getByLabelText('Account')).toHaveValue('acc1'));
    // Type + env are displayed, not editable — no Type/Environment Select.
    expect(screen.getByText('Snowflake')).toBeInTheDocument();
    expect(screen.getByText('DEV')).toBeInTheDocument();
    expect(screen.queryByLabelText('Type')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Environment')).not.toBeInTheDocument();
  });

  it('prefills config, omits the secret field, and submits an update via PATCH', async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    mockUpdate.mockResolvedValue({ ...existing, name: 'sf-dev-2' });
    renderDrawer({ connection: existing, onSaved });

    // Prefilled name + config (effect-driven, so wait for it).
    await waitFor(() => expect(screen.getByLabelText('Account')).toHaveValue('acc1'));
    expect(screen.getByLabelText('Name')).toHaveValue('sf-dev');
    // Edit mode omits the secret — rotation is the Re-auth flow.
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument();

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
    expect(onSaved).toHaveBeenCalled();
  });
});
