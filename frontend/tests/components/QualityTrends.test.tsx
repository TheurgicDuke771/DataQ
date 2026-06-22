import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { TrendPoint } from '../../src/api/dashboard';
import { QualityTrends } from '../../src/components/dashboard/QualityTrends';

// recharts' SVG doesn't lay out under jsdom (zero-size container), so these
// assert the card chrome + the empty-vs-chart branch, not the rendered bars.

const days: TrendPoint[] = [
  { day: '2026-06-10', succeeded: 3, failed: 1 },
  { day: '2026-06-11', succeeded: 0, failed: 0 },
  { day: '2026-06-12', succeeded: 2, failed: 0 },
];

describe('QualityTrends', () => {
  it('renders the card title and subtitle', () => {
    render(<QualityTrends trend={days} />);
    expect(screen.getByText('Quality Trends')).toBeInTheDocument();
    expect(screen.getByText('Succeeded vs failed runs per day')).toBeInTheDocument();
  });

  it('shows the empty state when every day has no runs', () => {
    render(
      <QualityTrends
        trend={[
          { day: '2026-06-10', succeeded: 0, failed: 0 },
          { day: '2026-06-11', succeeded: 0, failed: 0 },
        ]}
      />,
    );
    expect(screen.getByText('No runs in this range yet')).toBeInTheDocument();
  });

  it('does not show the empty state when some day has runs', () => {
    render(<QualityTrends trend={days} />);
    expect(screen.queryByText('No runs in this range yet')).not.toBeInTheDocument();
  });
});
