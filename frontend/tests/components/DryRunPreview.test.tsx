import { App as AntApp, Form } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useEffect } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { dryRunCheck } from '../../src/api/suites';
import { DryRunPreview } from '../../src/components/checks/DryRunPreview';

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, dryRunCheck: vi.fn() };
});

const mockDryRun = vi.mocked(dryRunCheck);

const NOT_NULL = 'expect_column_values_to_not_be_null';
const TARGET = { table: 'ORDERS', schema: 'PUBLIC' };

function Harness({
  expectationType,
  target,
  initialValues,
}: {
  expectationType?: string;
  target: Record<string, unknown> | null;
  initialValues?: Record<string, unknown>;
}) {
  const [form] = Form.useForm();
  // Seed the store imperatively — DryRunPreview reads getFieldsValue(true), and
  // these fields have no Form.Item registered in this harness.
  useEffect(() => {
    if (initialValues) form.setFieldsValue(initialValues);
  }, [form, initialValues]);
  return (
    <AntApp>
      <Form form={form}>
        <DryRunPreview suiteId="s1" expectationType={expectationType} target={target} form={form} />
      </Form>
    </AntApp>
  );
}

afterEach(() => vi.clearAllMocks());

describe('DryRunPreview', () => {
  it('is disabled with a reason until an expectation is picked', () => {
    render(<Harness expectationType={undefined} target={TARGET} />);
    expect(screen.getByRole('button', { name: 'Dry-run preview' })).toBeDisabled();
    expect(screen.getByText(/Pick an expectation/)).toBeInTheDocument();
  });

  it('is disabled with a reason when the suite has no table target', () => {
    render(<Harness expectationType={NOT_NULL} target={null} />);
    expect(screen.getByRole('button', { name: 'Dry-run preview' })).toBeDisabled();
    expect(screen.getByText(/Set a table target/)).toBeInTheDocument();
  });

  it('runs the preview and renders the severity outcome', async () => {
    mockDryRun.mockResolvedValue({
      status: 'warn',
      metric_value: 2.5,
      observed_value: { unexpected_percent: 2.5 },
      expected_value: { column: 'order_id' },
    });
    render(
      <Harness
        expectationType={NOT_NULL}
        target={TARGET}
        initialValues={{ config: { column: 'order_id' }, warn_threshold: 1 }}
      />,
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Dry-run preview' }));

    // Sends the suite's table/schema + the form's config + thresholds.
    await waitFor(() =>
      expect(mockDryRun).toHaveBeenCalledWith('s1', {
        expectation_type: NOT_NULL,
        config: { column: 'order_id' },
        warn_threshold: 1,
        fail_threshold: null,
        critical_threshold: null,
        table: 'ORDERS',
        schema: 'PUBLIC',
      }),
    );
    // The outcome renders: severity tag + metric.
    expect(await screen.findByText('warn')).toBeInTheDocument();
    expect(screen.getByText('2.5')).toBeInTheDocument();
    expect(screen.getByText(/"unexpected_percent":2.5/)).toBeInTheDocument();
  });

  it('surfaces the API error message when the dry-run fails', async () => {
    mockDryRun.mockRejectedValue(new Error('dry run could not execute against the datasource'));
    render(<Harness expectationType={NOT_NULL} target={TARGET} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Dry-run preview' }));

    expect(await screen.findByText('Dry-run failed')).toBeInTheDocument();
    expect(
      screen.getByText('dry run could not execute against the datasource'),
    ).toBeInTheDocument();
  });
});
