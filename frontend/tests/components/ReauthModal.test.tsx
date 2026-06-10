import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Connection, reauthConnection } from '../../src/api/connections';
import { ReauthModal } from '../../src/components/connections/ReauthModal';

vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return { ...actual, reauthConnection: vi.fn() };
});

const mockReauth = vi.mocked(reauthConnection);

const connection: Connection = {
  id: 'c1',
  name: 'sf-dev',
  type: 'snowflake',
  env: 'dev',
  config: {},
  has_secret: true,
  created_by: 'u1',
};

afterEach(() => {
  vi.clearAllMocks();
});

describe('ReauthModal', () => {
  it('rotates the credential and calls onDone', async () => {
    const user = userEvent.setup();
    const onDone = vi.fn();
    mockReauth.mockResolvedValue({ ok: true });

    render(
      <AntApp>
        <ReauthModal connection={connection} onClose={vi.fn()} onDone={onDone} />
      </AntApp>,
    );

    await user.type(await screen.findByLabelText('New credential'), 'new-secret');
    await user.click(screen.getByRole('button', { name: 'Rotate credential' }));

    await waitFor(() => expect(mockReauth).toHaveBeenCalledWith('c1', 'new-secret'));
    expect(onDone).toHaveBeenCalled();
  });

  it('does not call the API with an empty credential', async () => {
    const user = userEvent.setup();

    render(
      <AntApp>
        <ReauthModal connection={connection} onClose={vi.fn()} onDone={vi.fn()} />
      </AntApp>,
    );

    await user.click(screen.getByRole('button', { name: 'Rotate credential' }));

    await waitFor(() => expect(screen.getAllByText('New credential').length).toBeGreaterThan(0));
    expect(mockReauth).not.toHaveBeenCalled();
  });
});
