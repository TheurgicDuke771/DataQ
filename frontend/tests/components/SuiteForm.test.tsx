import { App as AntApp } from 'antd';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Connection } from '../../src/api/connections';
import { createSuite, type Suite, updateSuite } from '../../src/api/suites';
import { SuiteForm } from '../../src/components/suites/SuiteForm';
import { selectOption } from '../support/antd';

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, createSuite: vi.fn(), updateSuite: vi.fn() };
});

const mockCreate = vi.mocked(createSuite);
const mockUpdate = vi.mocked(updateSuite);

const adlsConnection: Connection = {
  id: 'conn-adls',
  name: 'adls-prod',
  type: 'adls_gen2',
  env: 'prod',
  config: {},
  has_secret: true,
  created_by: 'u1',
};

const snowflakeConnection: Connection = {
  id: 'conn-sf',
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
    name: 'logistics-suite',
    description: null,
    connection_id: 'conn-adls',
    target: null,
    created_by: 'u1',
    ...overrides,
  };
}

function renderForm(props: Partial<Parameters<typeof SuiteForm>[0]> = {}) {
  return render(
    <AntApp>
      <SuiteForm
        connections={[adlsConnection, snowflakeConnection]}
        onSaved={vi.fn()}
        onCancel={vi.fn()}
        {...props}
      />
    </AntApp>,
  );
}

async function pickConnection(user: ReturnType<typeof userEvent.setup>, name: RegExp) {
  await user.click(screen.getByLabelText('Connection'));
  await user.click(await screen.findByText(name));
}

afterEach(() => vi.clearAllMocks());

describe('SuiteForm — flat-file batch target (#1180)', () => {
  it('offers the single/batch mode toggle only for flat-file connections', async () => {
    const user = userEvent.setup();
    renderForm();

    await pickConnection(user, /adls-prod/);
    expect(await screen.findByText('Batch pattern')).toBeInTheDocument();

    // Switching to a warehouse connection drops the toggle — the SQL target
    // shape has no batch concept.
    await pickConnection(user, /sf-dev/);
    expect(screen.queryByText('Batch pattern')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Table')).toBeInTheDocument();
  });

  it('serializes prefix/pattern/latest-strategy batch fields into the submitted target', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue(suite());
    renderForm();

    await user.type(screen.getByLabelText('Name'), 'logistics-suite');
    await pickConnection(user, /adls-prod/);
    await user.click(await screen.findByText('Batch pattern'));

    await user.type(
      screen.getByLabelText('Prefix (optional)'),
      'adls_flatfile/logistics_tracking/',
    );
    // fireEvent.change, not user.type — userEvent's keyboard parser treats
    // `[`/`]` as special-key syntax, which a batch pattern regex is full of.
    fireEvent.change(screen.getByLabelText('Filename pattern (regex)'), {
      target: { value: 'tracking_events_([a-z_]+)\\.csv' },
    });
    // Strategy defaults to 'latest' — leave it as-is.

    await user.click(screen.getByRole('button', { name: /Create & add checks/ }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate.mock.calls[0][0]).toMatchObject({
      connection_id: 'conn-adls',
      target: {
        prefix: 'adls_flatfile/logistics_tracking/',
        pattern: 'tracking_events_([a-z_]+)\\.csv',
        strategy: 'latest',
      },
    });
    // 'latest' never carries a batch key.
    expect(mockCreate.mock.calls[0][0].target).not.toHaveProperty('batch');
  });

  it('serializes a specific-strategy batch target with its batch key', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue(suite());
    renderForm();

    await user.type(screen.getByLabelText('Name'), 'logistics-suite');
    await pickConnection(user, /adls-prod/);
    await user.click(await screen.findByText('Batch pattern'));
    // fireEvent.change, not user.type — userEvent's keyboard parser treats
    // `[`/`]` as special-key syntax, which a batch pattern regex is full of.
    fireEvent.change(screen.getByLabelText('Filename pattern (regex)'), {
      target: { value: 'tracking_events_([a-z_]+)\\.csv' },
    });
    await selectOption(user, 'Specific batch key', { index: 1 });
    await user.type(await screen.findByLabelText('Batch key'), 'ready');

    await user.click(screen.getByRole('button', { name: /Create & add checks/ }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate.mock.calls[0][0].target).toEqual({
      pattern: 'tracking_events_([a-z_]+)\\.csv',
      strategy: 'specific',
      batch: 'ready',
    });
  });

  it('flags a specific-strategy pattern with no capture group before it hits the API', async () => {
    const user = userEvent.setup();
    renderForm();

    await user.type(screen.getByLabelText('Name'), 'logistics-suite');
    await pickConnection(user, /adls-prod/);
    await user.click(await screen.findByText('Batch pattern'));
    // No parentheses at all — can't extract a batch key.
    await user.type(screen.getByLabelText('Filename pattern (regex)'), 'tracking_events_ready.csv');
    await selectOption(user, 'Specific batch key', { index: 1 });
    await user.type(await screen.findByLabelText('Batch key'), 'ready');

    await user.click(screen.getByRole('button', { name: /Create & add checks/ }));

    expect(await screen.findByText(/needs a capture group/)).toBeInTheDocument();
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it('reopens an existing batch target in batch mode with its fields populated', async () => {
    const user = userEvent.setup();
    mockUpdate.mockResolvedValue(suite());
    renderForm({
      suite: suite({
        target: {
          prefix: 'adls_flatfile/logistics_tracking/',
          pattern: 'tracking_events_([a-z_]+)\\.csv',
          strategy: 'latest',
        },
      }),
    });

    await waitFor(() =>
      expect(screen.getByLabelText('Filename pattern (regex)')).toHaveValue(
        'tracking_events_([a-z_]+)\\.csv',
      ),
    );
    expect(screen.getByLabelText('Prefix (optional)')).toHaveValue(
      'adls_flatfile/logistics_tracking/',
    );
    expect(screen.getByRole('radio', { name: 'Batch pattern' })).toBeChecked();
    // The literal single-file field never mounted in this mode.
    expect(screen.queryByLabelText('File path')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate).toHaveBeenCalledWith('s1', {
      name: 'logistics-suite',
      description: null,
      target: {
        prefix: 'adls_flatfile/logistics_tracking/',
        pattern: 'tracking_events_([a-z_]+)\\.csv',
        strategy: 'latest',
      },
    });
  });

  it('reopens an existing specific-strategy batch target with its batch key', async () => {
    const user = userEvent.setup();
    mockUpdate.mockResolvedValue(suite());
    renderForm({
      suite: suite({
        target: {
          pattern: 'tracking_events_([a-z_]+)\\.csv',
          strategy: 'specific',
          batch: 'ready',
        },
      }),
    });

    await waitFor(() => expect(screen.getByLabelText('Batch key')).toHaveValue('ready'));

    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate).toHaveBeenCalledWith('s1', {
      name: 'logistics-suite',
      description: null,
      target: {
        pattern: 'tracking_events_([a-z_]+)\\.csv',
        strategy: 'specific',
        batch: 'ready',
      },
    });
  });

  it('leaves single-file mode unchanged for a literal path target', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue(suite());
    renderForm();

    await user.type(screen.getByLabelText('Name'), 'logistics-suite');
    await pickConnection(user, /adls-prod/);
    // Default mode is 'single' — batch fields never render without opting in.
    expect(screen.queryByLabelText('Filename pattern (regex)')).not.toBeInTheDocument();
    await user.type(screen.getByLabelText('File path'), 'container/path/to/data.csv');

    await user.click(screen.getByRole('button', { name: /Create & add checks/ }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate.mock.calls[0][0].target).toEqual({ path: 'container/path/to/data.csv' });
  });
});
