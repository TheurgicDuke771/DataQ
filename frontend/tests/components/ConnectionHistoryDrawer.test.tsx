import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  type ConnectionVersion,
  listConnectionVersions,
  redactConnectionVersion,
} from '../../src/api/connections';
import { useCanMutateConnections } from '../../src/auth/useMe';
import { ConnectionHistoryDrawer } from '../../src/components/connections/ConnectionHistoryDrawer';

vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return { ...actual, listConnectionVersions: vi.fn(), redactConnectionVersion: vi.fn() };
});
vi.mock('../../src/auth/useMe', () => ({ useCanMutateConnections: vi.fn(() => false) }));

const mockList = vi.mocked(listConnectionVersions);
const mockRedact = vi.mocked(redactConnectionVersion);
const mockCanMutate = vi.mocked(useCanMutateConnections);

function version(overrides: Partial<ConnectionVersion> = {}): ConnectionVersion {
  return {
    version_no: 1,
    name: 'finance-dev',
    type: 'snowflake',
    env: 'dev',
    config: { account: 'ab12345', role: 'DQ_ROLE' },
    changed_by: 'u-1',
    changed_by_name: 'Ada Admin',
    created_at: '2026-06-15T10:00:00Z',
    redacted: false,
    redacted_at: null,
    ...overrides,
  };
}

function renderDrawer() {
  return render(
    <AntApp>
      <ConnectionHistoryDrawer
        open
        connection={{ id: 'c1', name: 'finance-dev' }}
        onClose={vi.fn()}
      />
    </AntApp>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
  mockCanMutate.mockReturnValue(false);
});

describe('ConnectionHistoryDrawer', () => {
  it('lists versions newest-first with the config of each', async () => {
    mockList.mockResolvedValue([
      version({ version_no: 2, config: { account: 'ab12345', role: 'DQ_ROLE_2' } }),
      version({ version_no: 1 }),
    ]);
    renderDrawer();

    expect(await screen.findByText('v2')).toBeInTheDocument();
    expect(screen.getAllByText('Current')).toHaveLength(1);
    expect(screen.getByText(/"role":\s*"DQ_ROLE_2"/)).toBeInTheDocument();
    expect(screen.getByText(/"role":\s*"DQ_ROLE"/)).toBeInTheDocument();
  });

  it('renders a redacted version as a tombstone notice, never its config', async () => {
    mockList.mockResolvedValue([
      version({ version_no: 2 }),
      version({
        version_no: 1,
        redacted: true,
        redacted_at: '2026-10-25T09:30:00Z',
        config: { redacted: true, redacted_at: '2026-10-25T09:30:00Z', redacted_by: 'u-9' },
      }),
    ]);
    renderDrawer();

    expect(await screen.findByText('Redacted')).toBeInTheDocument();
    expect(screen.getByText('Configuration redacted')).toBeInTheDocument();
    expect(screen.getByText(/Redacted by an admin on/)).toBeInTheDocument();
    // The tombstone is not rendered as a config block.
    expect(screen.queryByText(/"redacted_by"/)).not.toBeInTheDocument();
    // The header fields survive the redaction.
    expect(screen.getAllByText('finance-dev').length).toBeGreaterThan(0);
  });

  it('offers no Redact control to a non-admin', async () => {
    mockList.mockResolvedValue([version({ version_no: 2 }), version({ version_no: 1 })]);
    renderDrawer();

    await screen.findByText('v2');
    expect(screen.queryByRole('button', { name: /Redact this version/ })).not.toBeInTheDocument();
  });

  it('offers Redact to an admin only on non-current, non-redacted rows', async () => {
    mockCanMutate.mockReturnValue(true);
    mockList.mockResolvedValue([
      version({ version_no: 3 }),
      version({ version_no: 2 }),
      version({ version_no: 1, redacted: true, redacted_at: '2026-10-25T09:30:00Z' }),
    ]);
    renderDrawer();

    await screen.findByText('v3');
    // Exactly one: v2 (v3 is current, v1 is already redacted).
    expect(screen.getAllByRole('button', { name: /Redact this version/ })).toHaveLength(1);
  });

  it('redacts on confirm, toasts, and refetches the list', async () => {
    const user = userEvent.setup();
    mockCanMutate.mockReturnValue(true);
    mockList.mockResolvedValue([version({ version_no: 2 }), version({ version_no: 1 })]);
    mockRedact.mockResolvedValue(version({ version_no: 1, redacted: true }));
    renderDrawer();

    await user.click(await screen.findByRole('button', { name: /Redact this version/ }));
    // The confirmation names the version and is reachable as a dialog control.
    expect(await screen.findByText('Redact v1?')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Redact' }));

    await waitFor(() => expect(mockRedact).toHaveBeenCalledWith('c1', 1));
    expect(await screen.findByText('Redacted v1')).toBeInTheDocument();
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));
  });

  it('toasts an error and does not refetch when the redaction fails', async () => {
    const user = userEvent.setup();
    mockCanMutate.mockReturnValue(true);
    mockList.mockResolvedValue([version({ version_no: 2 }), version({ version_no: 1 })]);
    mockRedact.mockRejectedValue(new Error('connection version is already redacted'));
    renderDrawer();

    const swallowExpectedRejection = () => {};
    process.on('unhandledRejection', swallowExpectedRejection);
    try {
      await user.click(await screen.findByRole('button', { name: /Redact this version/ }));
      await user.click(await screen.findByRole('button', { name: 'Redact' }));
      expect(
        await screen.findByText(/Redact failed: connection version is already redacted/),
      ).toBeInTheDocument();
    } finally {
      process.off('unhandledRejection', swallowExpectedRejection);
    }
    expect(mockList).toHaveBeenCalledTimes(1);
  });
});
