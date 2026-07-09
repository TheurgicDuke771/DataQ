import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Connection } from '../../src/api/connections';
import { importSuite, type SuiteDocument } from '../../src/api/suites';
import { ImportSuiteDrawer } from '../../src/components/suites/ImportSuiteDrawer';
import { selectOption } from '../support/antd';

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, importSuite: vi.fn() };
});

const mockImport = vi.mocked(importSuite);

const connection: Connection = {
  id: 'conn1',
  name: 'sf-dev',
  type: 'snowflake',
  env: 'dev',
  config: {},
  has_secret: true,
  created_by: 'u1',
};

const DOCUMENT: SuiteDocument = {
  version: 1,
  name: 'orders-suite',
  description: 'imported',
  // A string threshold (Decimal-as-string) must round-trip untouched.
  checks: [
    {
      name: 'order_id not null',
      kind: 'expectation',
      expectation_type: 'expect_column_values_to_not_be_null',
      config: { column: 'order_id' },
      warn_threshold: '5.0',
      fail_threshold: null,
      critical_threshold: null,
    },
  ],
};

function renderDrawer(props: Partial<Parameters<typeof ImportSuiteDrawer>[0]> = {}) {
  return render(
    <AntApp>
      <ImportSuiteDrawer
        open
        connections={[connection]}
        onClose={vi.fn()}
        onImported={vi.fn()}
        {...props}
      />
    </AntApp>,
  );
}

// The Drawer portals to document.body, so the file input isn't under `container`.
function uploadFile(user: ReturnType<typeof userEvent.setup>, text: string) {
  const input = document.body.querySelector('input[type="file"]') as HTMLInputElement;
  const file = new File([text], 'suite.json', { type: 'application/json' });
  return user.upload(input, file);
}

afterEach(() => vi.clearAllMocks());

describe('ImportSuiteDrawer', () => {
  it('parses a valid document and imports it onto the chosen connection unchanged', async () => {
    const user = userEvent.setup();
    const onImported = vi.fn();
    mockImport.mockResolvedValue({
      id: 's9',
      name: 'orders-suite',
      description: 'imported',
      connection_id: 'conn1',
      target: null,
      created_by: 'u1',
    });
    renderDrawer({ onImported });

    await uploadFile(user, JSON.stringify(DOCUMENT));

    // Preview confirms the parsed document (name + check count).
    expect(await screen.findByText('orders-suite')).toBeInTheDocument();

    await selectOption(user, 'sf-dev · Snowflake · DEV', { by: 'text' });
    await user.click(screen.getByRole('button', { name: 'Import' }));

    await waitFor(() => expect(mockImport).toHaveBeenCalledTimes(1));
    // The document is passed back byte-faithful — including the string threshold.
    expect(mockImport).toHaveBeenCalledWith({ connection_id: 'conn1', document: DOCUMENT });
    expect(onImported).toHaveBeenCalled();
  });

  it('rejects a non-suite JSON with an error and keeps Import disabled', async () => {
    const user = userEvent.setup();
    renderDrawer();

    await uploadFile(user, JSON.stringify({ not: 'a suite' }));

    expect(await screen.findByText('Invalid document')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import' })).toBeDisabled();
    expect(mockImport).not.toHaveBeenCalled();
  });

  it('rejects malformed JSON', async () => {
    const user = userEvent.setup();
    renderDrawer();

    await uploadFile(user, '{ not json');

    expect(await screen.findByText('Not valid JSON.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import' })).toBeDisabled();
  });

  it('keeps Import disabled until a connection is chosen', async () => {
    const user = userEvent.setup();
    renderDrawer();

    await uploadFile(user, JSON.stringify(DOCUMENT));
    await screen.findByText('orders-suite');

    // Document parsed but no connection selected yet.
    expect(screen.getByRole('button', { name: 'Import' })).toBeDisabled();
  });

  it('excludes orchestration connections from the picker (#242)', async () => {
    const user = userEvent.setup();
    const adf: Connection = {
      id: 'conn-adf',
      name: 'adf-prod',
      type: 'adf',
      env: 'prod',
      config: {},
      has_secret: true,
      created_by: 'u1',
    };
    // Only the datasource (Snowflake) connection should be offered, never ADF.
    renderDrawer({ connections: [connection, adf] });

    await user.click(screen.getByRole('combobox'));
    expect(
      await screen.findByText('sf-dev · Snowflake · DEV', {
        selector: '.ant-select-item-option-content',
      }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/adf-prod/)).not.toBeInTheDocument();
  });
});
