import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  type Connection,
  createConnection,
  testDraftConnection,
  updateConnection,
} from '../../src/api/connections';
import { ConnectionForm } from '../../src/components/connections/ConnectionForm';
import { selectOption } from '../support/antd';

// Every field below is antd's own `requiredMark="optional"` marker appended
// to a rule-less field's label ("Catalog name" → "Catalog name(optional)"),
// same idiom `ReauthModal.test.tsx` uses for `/Key passphrase/` — match by
// regex rather than the field's own exact text.
const CATALOG_NAME = /Catalog name/;
const CATALOG_URI = /Catalog URI/;
const CATALOG_PASSWORD = /Catalog DB password/;
// antd icons carry their own `aria-label` (e.g. "plus"), which the accessible
// name algorithm prepends to a button's visible text — so "Add property"
// resolves to "plus Add property". Match by regex rather than exact text.
const ADD_PROPERTY = /Add property/;

vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return {
    ...actual,
    createConnection: vi.fn(),
    updateConnection: vi.fn(),
    testDraftConnection: vi.fn(),
    testConnection: vi.fn(),
  };
});

const mockCreate = vi.mocked(createConnection);
const mockUpdate = vi.mocked(updateConnection);
const mockDraftTest = vi.mocked(testDraftConnection);

const icebergConnection: Connection = {
  id: 'conn-iceberg-1',
  name: 'harness-iceberg',
  type: 'iceberg',
  env: 'dev',
  config: { catalog_type: 'sql', catalog_uri: 'sqlite:///w', catalog_name: 'harness' },
  has_secret: false,
  created_by: 'u1',
};

afterEach(() => {
  vi.clearAllMocks();
});

describe('ConnectionForm — Iceberg catalog fields (#1181)', () => {
  it('renders catalog name and the properties editor for iceberg, and gates the catalog password on catalog_type', async () => {
    render(
      <AntApp>
        <ConnectionForm type="iceberg" onSaved={vi.fn()} onCancel={vi.fn()} />
      </AntApp>,
    );

    expect(await screen.findByLabelText(CATALOG_NAME)).toBeInTheDocument();
    expect(screen.getByText(/Catalog \/ storage properties/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: ADD_PROPERTY })).toBeInTheDocument();
    // The catalog password is gated on catalog_type — not visible until it's sql/hive.
    expect(screen.queryByLabelText(CATALOG_PASSWORD)).not.toBeInTheDocument();
  });

  it('does not render any catalog fields for a non-Iceberg type', async () => {
    render(
      <AntApp>
        <ConnectionForm type="snowflake" onSaved={vi.fn()} onCancel={vi.fn()} />
      </AntApp>,
    );

    await screen.findByLabelText('Account'); // form is mounted
    expect(screen.queryByLabelText(CATALOG_NAME)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: ADD_PROPERTY })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(CATALOG_PASSWORD)).not.toBeInTheDocument();
  });

  it('shows the catalog password field once catalog_type is set to sql', async () => {
    const user = userEvent.setup();
    render(
      <AntApp>
        <ConnectionForm type="iceberg" onSaved={vi.fn()} onCancel={vi.fn()} />
      </AntApp>,
    );

    expect(screen.queryByLabelText(CATALOG_PASSWORD)).not.toBeInTheDocument();
    await user.type(await screen.findByLabelText('Catalog type'), 'sql');
    expect(await screen.findByLabelText(CATALOG_PASSWORD)).toBeInTheDocument();
  });

  it('shows the catalog password field for catalog_type "hive" too', async () => {
    const user = userEvent.setup();
    render(
      <AntApp>
        <ConnectionForm type="iceberg" onSaved={vi.fn()} onCancel={vi.fn()} />
      </AntApp>,
    );

    await user.type(await screen.findByLabelText('Catalog type'), 'hive');
    expect(await screen.findByLabelText(CATALOG_PASSWORD)).toBeInTheDocument();
  });

  it('does not show the catalog password field for catalog_type "rest"', async () => {
    const user = userEvent.setup();
    render(
      <AntApp>
        <ConnectionForm type="iceberg" onSaved={vi.fn()} onCancel={vi.fn()} />
      </AntApp>,
    );

    await user.type(await screen.findByLabelText('Catalog type'), 'rest');
    expect(screen.queryByLabelText(CATALOG_PASSWORD)).not.toBeInTheDocument();
  });

  it('serializes added property rows into a config.properties dict on create', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue(icebergConnection);

    render(
      <AntApp>
        <ConnectionForm type="iceberg" onSaved={vi.fn()} onCancel={vi.fn()} />
      </AntApp>,
    );

    await user.type(await screen.findByLabelText('Name'), 'harness-iceberg');
    await selectOption(user, 'DEV');
    await user.type(screen.getByLabelText('Catalog type'), 'rest');
    await user.type(screen.getByLabelText(CATALOG_URI), 'https://catalog.example.com');

    await user.click(screen.getByRole('button', { name: ADD_PROPERTY }));
    const [propKey] = screen.getAllByPlaceholderText('Property (e.g. s3.endpoint)');
    const [propValue] = screen.getAllByPlaceholderText('Value');
    await user.type(propKey, 's3.endpoint');
    await user.type(propValue, 'http://minio:9000');

    await user.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    const payload = mockCreate.mock.calls[0][0];
    expect(payload.config.properties).toEqual({ 's3.endpoint': 'http://minio:9000' });
  });

  it('supports two properties at once and removing one', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue(icebergConnection);

    render(
      <AntApp>
        <ConnectionForm type="iceberg" onSaved={vi.fn()} onCancel={vi.fn()} />
      </AntApp>,
    );

    await user.type(await screen.findByLabelText('Name'), 'harness-iceberg');
    await selectOption(user, 'DEV');
    await user.type(screen.getByLabelText('Catalog type'), 'rest');

    await user.click(screen.getByRole('button', { name: ADD_PROPERTY }));
    await user.click(screen.getByRole('button', { name: ADD_PROPERTY }));
    let keys = screen.getAllByPlaceholderText('Property (e.g. s3.endpoint)');
    const values = screen.getAllByPlaceholderText('Value');
    await user.type(keys[0], 's3.endpoint');
    await user.type(values[0], 'http://minio:9000');
    await user.type(keys[1], 's3.path-style-access');
    await user.type(values[1], 'true');

    // Remove the first row.
    const removeButtons = screen.getAllByRole('button', { name: 'Remove property' });
    await user.click(removeButtons[0]);

    keys = screen.getAllByPlaceholderText('Property (e.g. s3.endpoint)');
    expect(keys).toHaveLength(1);

    await user.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    const payload = mockCreate.mock.calls[0][0];
    expect(payload.config.properties).toEqual({ 's3.path-style-access': 'true' });
  });

  it('sends catalog_secret on create', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue(icebergConnection);

    render(
      <AntApp>
        <ConnectionForm type="iceberg" onSaved={vi.fn()} onCancel={vi.fn()} />
      </AntApp>,
    );

    await user.type(await screen.findByLabelText('Name'), 'harness-iceberg');
    await selectOption(user, 'DEV');
    await user.type(screen.getByLabelText('Catalog type'), 'sql');
    await user.type(screen.getByLabelText(CATALOG_URI), 'postgresql://catalog_user@h/db');
    await user.type(await screen.findByLabelText(CATALOG_PASSWORD), 'db-pw');

    await user.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    const payload = mockCreate.mock.calls[0][0];
    expect(payload.catalog_secret).toBe('db-pw');
  });

  it('omits catalog_secret on create when the field is left blank', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue(icebergConnection);

    render(
      <AntApp>
        <ConnectionForm type="iceberg" onSaved={vi.fn()} onCancel={vi.fn()} />
      </AntApp>,
    );

    await user.type(await screen.findByLabelText('Name'), 'harness-iceberg');
    await selectOption(user, 'DEV');
    await user.type(screen.getByLabelText('Catalog type'), 'rest');

    await user.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    const payload = mockCreate.mock.calls[0][0];
    expect(payload.catalog_secret).toBeUndefined();
  });

  it('sends catalog_secret in the draft Test Connection payload', async () => {
    const user = userEvent.setup();
    mockDraftTest.mockResolvedValue({ ok: true });

    render(
      <AntApp>
        <ConnectionForm type="iceberg" onSaved={vi.fn()} onCancel={vi.fn()} />
      </AntApp>,
    );

    await user.type(await screen.findByLabelText('Name'), 'harness-iceberg');
    await selectOption(user, 'DEV');
    await user.type(screen.getByLabelText('Catalog type'), 'sql');
    await user.type(screen.getByLabelText(CATALOG_URI), 'postgresql://catalog_user@h/db');
    await user.type(await screen.findByLabelText(CATALOG_PASSWORD), 'db-pw');

    await user.click(screen.getByRole('button', { name: 'Test connection' }));

    await waitFor(() => expect(mockDraftTest).toHaveBeenCalled());
    const payload = mockDraftTest.mock.calls[0][0];
    expect(payload.catalog_secret).toBe('db-pw');
  });

  it('renders and sends the catalog password field in EDIT mode too (no separate reauth flow for it)', async () => {
    const user = userEvent.setup();
    mockUpdate.mockResolvedValue(icebergConnection);

    render(
      <AntApp>
        <ConnectionForm
          type="iceberg"
          connection={icebergConnection}
          onSaved={vi.fn()}
          onCancel={vi.fn()}
        />
      </AntApp>,
    );

    const field = await screen.findByLabelText(CATALOG_PASSWORD);
    // Write-only: never prefilled from the (never-returned) stored value.
    expect(field).toHaveValue('');
    await user.type(field, 'rotated-pw');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    const [, payload] = mockUpdate.mock.calls[0];
    expect(payload.catalog_secret).toBe('rotated-pw');
  });

  it('does not rotate the catalog secret on save when left blank in edit mode', async () => {
    const user = userEvent.setup();
    mockUpdate.mockResolvedValue(icebergConnection);

    render(
      <AntApp>
        <ConnectionForm
          type="iceberg"
          connection={icebergConnection}
          onSaved={vi.fn()}
          onCancel={vi.fn()}
        />
      </AntApp>,
    );

    await screen.findByLabelText(CATALOG_PASSWORD);
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    const [, payload] = mockUpdate.mock.calls[0];
    expect(payload.catalog_secret).toBeUndefined();
  });
});
