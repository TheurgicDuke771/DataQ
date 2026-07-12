import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from 'antd';
import { describe, expect, it, vi } from 'vitest';

import { downloadComparisonReport, type Result } from '../../src/api/runs';
import { ComparisonResultDetail } from '../../src/components/results/ComparisonResultDetail';

vi.mock('../../src/api/runs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/runs')>();
  return { ...actual, downloadComparisonReport: vi.fn().mockResolvedValue(undefined) };
});

const result: Result = {
  id: 'r1',
  check_id: 'chk1',
  status: 'fail',
  metric_value: 75,
  duration_ms: null,
  observed_value: {
    source_rows: 3,
    target_rows: 3,
    matched: 1,
    mismatched: 1,
    additional_in_source: 1,
    additional_in_target: 1,
    mismatch_percent: 75.0,
  },
  expected_value: null,
  sample_failures: {
    mismatched: [{ order_id: '3', amount_src: '30', amount_tgt: '31' }],
    additional_in_source: [{ order_id: '1', amount_src: '10' }],
  },
};

describe('ComparisonResultDetail', () => {
  it('renders bucket counts, sample tables, and download actions', async () => {
    render(
      <App>
        <ComparisonResultDetail runId="run1" result={result} />
      </App>,
    );
    expect(screen.getByText('Mismatch %')).toBeInTheDocument();
    // JSON numbers render via String() — 75.0 arrives as 75.
    expect(screen.getAllByText('75').length).toBeGreaterThan(0);
    expect(screen.getByText('Mismatched (sample)')).toBeInTheDocument();
    expect(screen.getByText('Only in source (sample)')).toBeInTheDocument();
    // Sample cells render (suffixed column headers appear once per bucket table).
    expect(screen.getAllByText('amount_src').length).toBeGreaterThan(0);
    expect(screen.getByText('31')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /CSV report/ }));
    expect(vi.mocked(downloadComparisonReport)).toHaveBeenCalledWith('run1', 'r1', 'csv');
    await userEvent.click(screen.getByRole('button', { name: /XLSX report/ }));
    expect(vi.mocked(downloadComparisonReport)).toHaveBeenCalledWith('run1', 'r1', 'xlsx');
  });

  it('renders no sample tables for a fully reconciled result', () => {
    render(
      <App>
        <ComparisonResultDetail
          runId="run1"
          result={{ ...result, sample_failures: null, observed_value: { matched: 5 } }}
        />
      </App>,
    );
    expect(screen.getByText('Matched')).toBeInTheDocument();
    expect(screen.queryByText('Mismatched (sample)')).not.toBeInTheDocument();
  });
});

describe('ComparisonResultDetail — columns grain (#799)', () => {
  it('renders value-grain counters and the per-column breakdown', () => {
    render(
      <App>
        <ComparisonResultDetail
          runId="run1"
          result={{
            ...result,
            observed_value: {
              source_rows: 3,
              target_rows: 3,
              matched_values: 4,
              mismatched_values: 1,
              additional_in_source_values: 1,
              additional_in_target_values: 0,
              mismatch_percent: 33.3333,
              per_column: {
                amount: {
                  matched: 2,
                  mismatched: 1,
                  additional_in_source: 0,
                  additional_in_target: 0,
                },
                status: {
                  matched: 2,
                  mismatched: 0,
                  additional_in_source: 1,
                  additional_in_target: 0,
                },
              },
            },
            sample_failures: {
              mismatched: [{ order_id: '3', amount_src: '30', amount_tgt: '31' }],
            },
          }}
        />
      </App>,
    );
    expect(screen.getByText('Mismatched values')).toBeInTheDocument();
    expect(screen.getByTestId('comparison-per-column')).toBeInTheDocument();
    expect(screen.getByText('amount')).toBeInTheDocument();
    expect(screen.getByText('status')).toBeInTheDocument();
  });
});
