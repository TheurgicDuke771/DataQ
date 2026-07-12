import { render, screen } from '@testing-library/react';
import { App, Form } from 'antd';
import { describe, expect, it } from 'vitest';

import type { Connection } from '../../src/api/connections';
import { buildComparisonPayload } from '../../src/components/checks/checkForm';
import { ComparisonCheckForm } from '../../src/components/checks/ComparisonCheckForm';

const connections: Connection[] = [
  {
    id: 'src1',
    name: 'snowflake-qa',
    type: 'snowflake',
    env: 'qa',
    config: {},
    has_secret: true,
    created_by: 'u1',
  },
  {
    id: 'orch1',
    name: 'airflow-dev',
    type: 'airflow',
    env: 'dev',
    config: {},
    has_secret: true,
    created_by: 'u1',
  },
];

function renderForm() {
  return render(
    <App>
      <Form>
        <ComparisonCheckForm
          connections={connections}
          suiteConnectionName="snowflake-dev"
          suiteConnectionType="snowflake"
          targetSummary="RETAIL.ORDERS_COPY"
        />
      </Form>
    </App>,
  );
}

describe('ComparisonCheckForm', () => {
  it('locks the target connection to the suite (ADR 0015 §1 made visible)', () => {
    renderForm();
    const target = screen.getByTestId('comparison-target-connection');
    expect(target).toBeDisabled();
    expect(target).toHaveValue('snowflake-dev');
    // Structural: no combobox exists inside the target pane — the connection
    // is not user-changeable, only displayed.
    const pane = screen.getByTestId('comparison-target-pane');
    expect(pane.querySelector('.ant-select')).toBeNull();
  });

  it('renders source and target panes side by side with common options below', () => {
    renderForm();
    expect(screen.getByTestId('comparison-source-pane')).toBeInTheDocument();
    expect(screen.getByText('Join key columns')).toBeInTheDocument();
    expect(screen.getByText('Row cap (per side)')).toBeInTheDocument();
  });
});

describe('buildComparisonPayload', () => {
  it('assembles the ADR 0015 config shape from table-mode values', () => {
    const payload = buildComparisonPayload({
      name: 'orders reconcile',
      source_connection_id: 'src1',
      source_mode: 'table',
      source: { table: 'ORDERS', schema: 'RETAIL', catalog: '' },
      keys: ['order_id'],
      max_rows: 50000,
      warn_threshold: 1,
      fail_threshold: 5,
    });
    expect(payload).toEqual({
      name: 'orders reconcile',
      kind: 'comparison',
      expectation_type: 'comparison:records',
      source_connection_id: 'src1',
      config: {
        source: { table: 'ORDERS', schema: 'RETAIL' }, // empty catalog dropped
        keys: ['order_id'],
        max_rows: 50000,
      },
      warn_threshold: 1,
      fail_threshold: 5,
      critical_threshold: null,
    });
  });

  it('uses the query spec in query mode and carries the target projection', () => {
    const payload = buildComparisonPayload({
      name: 'recon',
      source_connection_id: 'src1',
      source_mode: 'query',
      source_query: 'SELECT id FROM T',
      source: { table: 'ignored-in-query-mode' },
      target_query: 'SELECT id FROM COPY',
      keys: ['id'],
    });
    expect(payload.config).toEqual({
      source: { query: 'SELECT id FROM T' },
      keys: ['id'],
      target_query: 'SELECT id FROM COPY',
    });
  });
});
