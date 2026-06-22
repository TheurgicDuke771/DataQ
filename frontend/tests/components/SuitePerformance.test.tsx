import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { SuitePerformance as SuitePerf } from '../../src/api/dashboard';
import { SuitePerformance } from '../../src/components/dashboard/SuitePerformance';

const suites: SuitePerf[] = [
  { suite_id: 's1', name: 'Real-time Telemetry', score: 34, state: 'critical' },
  { suite_id: 's2', name: 'User Metadata', score: 74, state: 'stable' },
  { suite_id: 's3', name: 'Financial Transactions', score: 96, state: 'optimal' },
  { suite_id: 's4', name: 'Fresh Suite', score: null, state: 'unknown' },
];

describe('SuitePerformance', () => {
  it('renders each suite with its state label', () => {
    render(<SuitePerformance suites={suites} />);
    expect(screen.getByText('Real-time Telemetry')).toBeInTheDocument();
    expect(screen.getByText('Critical')).toBeInTheDocument();
    expect(screen.getByText('Stable')).toBeInTheDocument();
    expect(screen.getByText('Optimal')).toBeInTheDocument();
  });

  it('shows "No data" for a suite with a null score', () => {
    render(<SuitePerformance suites={suites} />);
    expect(screen.getByText('No data')).toBeInTheDocument();
  });

  it('shows an empty state when there are no suites', () => {
    render(<SuitePerformance suites={[]} />);
    expect(screen.getByText('No suites with runs yet')).toBeInTheDocument();
  });
});
