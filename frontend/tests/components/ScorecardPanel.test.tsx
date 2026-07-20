import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { Scorecard } from '../../src/api/assets';
import { ScorecardPanel } from '../../src/components/assets/ScorecardPanel';

const card = (over: Partial<Scorecard> = {}): Scorecard => ({
  covered: [],
  uncovered: [],
  unclassified_checks: 0,
  ...over,
});

describe('ScorecardPanel (#889)', () => {
  it('renders nothing when the API did not send a scorecard', () => {
    // Absent ≠ empty. A pre-#889 API says nothing about coverage, so asserting
    // "no coverage" would be a claim we cannot vouch for.
    const { container } = render(<ScorecardPanel scorecard={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows a per-dimension score and check counts', () => {
    render(
      <ScorecardPanel
        scorecard={card({
          covered: [{ dimension: 'completeness', checks_total: 4, checks_passing: 3, score: 87.5 }],
        })}
      />,
    );
    expect(screen.getByText('Completeness')).toBeInTheDocument();
    expect(screen.getByText('3/4 checks')).toBeInTheDocument();
  });

  it('never renders a green score for an asset with no checks', () => {
    // The single most dangerous thing this feature could do: a clean tick over
    // an asset nobody watches reads as "verified good".
    render(<ScorecardPanel scorecard={card({ uncovered: ['completeness', 'timeliness'] })} />);
    expect(screen.getByText(/nothing to score/i)).toBeInTheDocument();
    expect(screen.queryByText('100%')).not.toBeInTheDocument();
  });

  it('shows "no signal" rather than 0 when checks exist but none evaluated', () => {
    // Score null = all skip/error. A 0 would read as "everything failed" — the
    // opposite fact.
    render(
      <ScorecardPanel
        scorecard={card({
          covered: [{ dimension: 'timeliness', checks_total: 0, checks_passing: 0, score: null }],
        })}
      />,
    );
    expect(screen.getByText('No signal')).toBeInTheDocument();
    expect(screen.queryByText('0%')).not.toBeInTheDocument();
  });

  it('lists uncovered dimensions separately from failing ones', () => {
    // Different problems, different fixes: write a check vs. fix the data.
    render(
      <ScorecardPanel
        scorecard={card({
          covered: [{ dimension: 'validity', checks_total: 2, checks_passing: 0, score: 50 }],
          uncovered: ['timeliness'],
        })}
      />,
    );
    expect(screen.getByText('Not covered')).toBeInTheDocument();
    expect(screen.getByText('Timeliness')).toBeInTheDocument();
    expect(screen.getByText('Validity')).toBeInTheDocument();
  });

  it('reports unclassified checks without bucketing them', () => {
    render(
      <ScorecardPanel
        scorecard={card({
          covered: [{ dimension: 'validity', checks_total: 1, checks_passing: 1, score: 100 }],
          unclassified_checks: 3,
        })}
      />,
    );
    expect(screen.getByText(/3 checks have no dimension set/i)).toBeInTheDocument();
    expect(screen.getByText('1/1 checks')).toBeInTheDocument(); // the 3 did not leak in
  });

  it('uses singular wording for one unclassified check', () => {
    render(<ScorecardPanel scorecard={card({ unclassified_checks: 1, uncovered: [] })} />);
    expect(screen.getByText(/1 check has no dimension set/i)).toBeInTheDocument();
  });
});
