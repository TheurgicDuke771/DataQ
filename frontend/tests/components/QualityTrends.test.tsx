import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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

  it('offers the same numbers as a table (#1673)', async () => {
    render(<QualityTrends trend={days} />);
    // The switch is tied to its chart by name, not antd's generic "segmented control".
    expect(screen.getByRole('radiogroup', { name: 'Quality Trends view' })).toBeInTheDocument();
    await userEvent.click(screen.getByText('Table'));
    const table = screen.getByRole('table', { name: 'Runs per day' });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows.map((r) => r.textContent)).toEqual([
      '2026-06-1031',
      '2026-06-1100',
      '2026-06-1220',
    ]);
  });

  it('offers no table when there is nothing to show', () => {
    render(<QualityTrends trend={[{ day: '2026-06-10', succeeded: 0, failed: 0 }]} />);
    expect(screen.queryByText('Table')).not.toBeInTheDocument();
  });
});
