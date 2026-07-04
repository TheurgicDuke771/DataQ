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

    // A snowflake connection with no auth_type resolves to the default
    // password mode, so the field is labelled after it.
    await user.type(await screen.findByLabelText('New: Password'), 'new-secret');
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

    await waitFor(() => expect(screen.getAllByText('New: Password').length).toBeGreaterThan(0));
    expect(mockReauth).not.toHaveBeenCalled();
  });

  it("labels the field after a single-secret type's credential", async () => {
    render(
      <AntApp>
        <ReauthModal
          connection={{ ...connection, type: 's3' }}
          onClose={vi.fn()}
          onDone={vi.fn()}
        />
      </AntApp>,
    );

    expect(await screen.findByLabelText('New: Secret access key')).toBeInTheDocument();
  });

  it('composes the combined payload when rotating a key-pair credential with a passphrase', async () => {
    const user = userEvent.setup();
    mockReauth.mockResolvedValue({ ok: true });

    render(
      <AntApp>
        <ReauthModal
          connection={{ ...connection, config: { auth_type: 'key_pair' } }}
          onClose={vi.fn()}
          onDone={vi.fn()}
        />
      </AntApp>,
    );

    await user.type(await screen.findByLabelText('New: Private key (PEM)'), 'PEM-KEY');
    await user.type(screen.getByLabelText('Key passphrase (optional)'), 'pp');
    await user.click(screen.getByRole('button', { name: 'Rotate credential' }));

    await waitFor(() =>
      expect(mockReauth).toHaveBeenCalledWith(
        'c1',
        JSON.stringify({ private_key: 'PEM-KEY', passphrase: 'pp' }),
      ),
    );
  });

  it('sends the bare key when the key-pair passphrase is left blank', async () => {
    const user = userEvent.setup();
    mockReauth.mockResolvedValue({ ok: true });

    render(
      <AntApp>
        <ReauthModal
          connection={{ ...connection, config: { auth_type: 'key_pair' } }}
          onClose={vi.fn()}
          onDone={vi.fn()}
        />
      </AntApp>,
    );

    await user.type(await screen.findByLabelText('New: Private key (PEM)'), 'PEM-KEY');
    await user.click(screen.getByRole('button', { name: 'Rotate credential' }));

    await waitFor(() => expect(mockReauth).toHaveBeenCalledWith('c1', 'PEM-KEY'));
  });
});
