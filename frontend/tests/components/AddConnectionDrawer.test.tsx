import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { createConnection } from '../../src/api/connections';
import { AddConnectionDrawer } from '../../src/components/connections/AddConnectionDrawer';

vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return { ...actual, createConnection: vi.fn() };
});

const mockCreate = vi.mocked(createConnection);

function renderDrawer(onCreated = vi.fn()) {
  return render(
    <AntApp>
      <AddConnectionDrawer open onClose={vi.fn()} onCreated={onCreated} />
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

describe('AddConnectionDrawer', () => {
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

    // The secret control is now a PEM textarea, not a masked password input.
    expect(await screen.findByLabelText('Private key (PEM)')).toBeInTheDocument();
    expect(
      screen.queryByLabelText('Password', { selector: 'input,textarea' }),
    ).not.toBeInTheDocument();
  });

  it('hides the secret field for S3 IAM-role auth (no stored credential)', async () => {
    const user = userEvent.setup();
    renderDrawer();

    await selectOption(user, 'Type', 'AWS S3');
    expect(await screen.findByLabelText('Secret access key')).toBeInTheDocument();

    await selectOption(user, 'Auth type', 'IAM role');
    await waitFor(() =>
      expect(screen.queryByLabelText('Secret access key')).not.toBeInTheDocument(),
    );
    expect(screen.queryByLabelText('Access key ID')).not.toBeInTheDocument();
  });

  it('submits the assembled payload and calls onCreated', async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn();
    mockCreate.mockResolvedValue({
      id: 'c1',
      name: 'uc-dev',
      type: 'unity_catalog',
      env: 'dev',
      config: { workspace_url: 'https://w', warehouse_id: 'wh1' },
      has_secret: true,
      created_by: 'u1',
    });
    renderDrawer(onCreated);

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
    expect(onCreated).toHaveBeenCalled();
  });

  it('does not submit when required fields are missing', async () => {
    const user = userEvent.setup();
    renderDrawer();

    await user.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(screen.getAllByText('Name').length).toBeGreaterThan(0));
    expect(mockCreate).not.toHaveBeenCalled();
  });
});
