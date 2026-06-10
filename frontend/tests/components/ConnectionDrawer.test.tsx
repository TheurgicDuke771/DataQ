import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Connection, createConnection, updateConnection } from '../../src/api/connections';
import { ConnectionDrawer } from '../../src/components/connections/ConnectionDrawer';

vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return { ...actual, createConnection: vi.fn(), updateConnection: vi.fn() };
});

const mockCreate = vi.mocked(createConnection);
const mockUpdate = vi.mocked(updateConnection);

function renderDrawer(props: Partial<Parameters<typeof ConnectionDrawer>[0]> = {}) {
  return render(
    <AntApp>
      <ConnectionDrawer open onClose={vi.fn()} onSaved={vi.fn()} {...props} />
    </AntApp>,
  );
}

// antd Select renders options in a portal; pick by visible option text.
async function selectOption(
  user: ReturnType<typeof userEvent.setup>,
  label: string,
  option: string,
) {
  const field = screen.getByLabelText(label);
  await user.click(field);
  const opt = await screen.findByText(option, { selector: '.ant-select-item-option-content' });
  await user.click(opt);
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('ConnectionDrawer — create', () => {
  it('shows type-specific fields after picking a type (Snowflake)', async () => {
    const user = userEvent.setup();
    renderDrawer();

    await selectOption(user, 'Type', 'Snowflake');

    expect(await screen.findByLabelText('Account')).toBeInTheDocument();
    expect(screen.getByLabelText('Warehouse')).toBeInTheDocument();
    // Snowflake defaults to password auth → a Password secret field.
    expect(screen.getByLabelText('Password')).toBeInTheDocument();
  });

  it('swaps the secret field to a PEM textarea for Snowflake key-pair auth', async () => {
    const user = userEvent.setup();
    renderDrawer();

    await selectOption(user, 'Type', 'Snowflake');
    await selectOption(user, 'Auth type', 'Key pair (RSA)');

    expect(await screen.findByLabelText('Private key (PEM)')).toBeInTheDocument();
    expect(
      screen.queryByLabelText('Password', { selector: 'input,textarea' }),
    ).not.toBeInTheDocument();
  });

  it('shows S3 access-key fields with no auth toggle (IAM role deferred in v1)', async () => {
    const user = userEvent.setup();
    renderDrawer();

    await selectOption(user, 'Type', 'AWS S3');

    expect(await screen.findByLabelText('Access key ID')).toBeInTheDocument();
    expect(screen.getByLabelText('Secret access key')).toBeInTheDocument();
    expect(screen.queryByLabelText('Auth type')).not.toBeInTheDocument();
  });

  it('submits the assembled payload and calls onSaved', async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    mockCreate.mockResolvedValue({
      id: 'c1',
      name: 'uc-dev',
      type: 'unity_catalog',
      env: 'dev',
      config: { workspace_url: 'https://w', warehouse_id: 'wh1' },
      has_secret: true,
      created_by: 'u1',
    });
    renderDrawer({ onSaved });

    await user.type(screen.getByLabelText('Name'), 'uc-dev');
    await selectOption(user, 'Environment', 'DEV');
    await selectOption(user, 'Type', 'Unity Catalog');
    await user.type(screen.getByLabelText('Workspace URL'), 'https://w');
    await user.type(screen.getByLabelText('Warehouse ID'), 'wh1');
    await user.type(screen.getByLabelText('Personal access token (PAT)'), 'pat-xyz');

    await user.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate).toHaveBeenCalledWith({
      name: 'uc-dev',
      type: 'unity_catalog',
      env: 'dev',
      config: { workspace_url: 'https://w', warehouse_id: 'wh1' },
      secret: 'pat-xyz',
    });
    expect(onSaved).toHaveBeenCalled();
  });

  it('does not submit when required fields are missing', async () => {
    const user = userEvent.setup();
    renderDrawer();

    await user.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(screen.getAllByText('Name').length).toBeGreaterThan(0));
    expect(mockCreate).not.toHaveBeenCalled();
  });
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
    expect(mockCreate).not.toHaveBeenCalled();
    expect(onSaved).toHaveBeenCalled();
  });
});
