import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { SeasonalResultSummary } from '../../src/components/results/SeasonalResultSummary';

const period = { start: '2026-11-27', end: '2026-11-27', unit: 'day' };

describe('SeasonalResultSummary', () => {
  it('renders nothing for a result that judged no period', () => {
    const { container } = render(<SeasonalResultSummary observed={{ z_score: 1, mean: 3 }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('gives the expected value, the declared entry and that the calendar changed the verdict', () => {
    render(
      <SeasonalResultSummary
        observed={{
          period,
          z_score: 0.4,
          mean: 5050,
          stddev: 125,
          yearly: { applied: true, level: 1000, index: 5, expected: 5000, years: 2 },
          declared: {
            inside: {
              name: 'Black Friday',
              occurrence: { start: '2026-11-27', end: '2026-11-30' },
              first_year: false,
              banded: true,
            },
            verdict_without: 'fail',
            z_score_without: 12.5,
            verdict_changed: true,
            missing: [],
          },
          history: { observed: 40, backfilled: 300 },
        }}
      />,
    );
    const summary = screen.getByTestId('seasonal-result-summary');
    expect(summary).toHaveTextContent('Period judged2026-11-27');
    expect(summary).toHaveTextContent('expected 5000 (level 1000 × index 5, from 2 earlier years)');
    expect(summary).toHaveTextContent('inside Black Friday (2026-11-27 – 2026-11-30)');
    expect(summary).toHaveTextContent(
      'changed the verdict: without the calendar it would have been fail (z 12.50)',
    );
    expect(summary).toHaveTextContent('40 measured by runs, 300 seeded from the table');
  });

  it('says why the yearly comparison was not applied, and a first-year unbanded rise', () => {
    render(
      <SeasonalResultSummary
        observed={{
          period: { start: '2026-11-23', end: '2026-11-29', unit: 'week' },
          yearly: { applied: false, reason: 'no_earlier_year' },
          declared: {
            inside: { name: 'Launch', first_year: true, banded: false },
            verdict_changed: false,
            missing: ['x'],
          },
        }}
      />,
    );
    expect(screen.getByText('2026-11-23 – 2026-11-29')).toBeInTheDocument();
    expect(
      screen.getByText(
        'not applied: no earlier year in the history; scored against the recent window',
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        'inside Launch; its first occurrence in the history, so a rise was not banded',
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('did not change the verdict')).toBeInTheDocument();
    expect(screen.getByText('1 attached entry is no longer on the calendar')).toBeInTheDocument();
  });

  it('says a period outside every attached entry is outside', () => {
    render(
      <SeasonalResultSummary
        observed={{ period, declared: { inside: null, verdict_changed: false, missing: [] } }}
      />,
    );
    expect(screen.getByText('outside every attached entry')).toBeInTheDocument();
  });
});
