import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { SuggestionLineage } from '../../src/api/llm';
import { SuggestionLineageNote } from '../../src/components/suites/SuggestionLineageNote';

const origin = (over: Partial<NonNullable<SuggestionLineage['origins']>[number]> = {}) => ({
  asset_id: 'a1',
  asset_name: 'dataq_retail.raw.feedback',
  column: 'customer_id',
  confirmed: true,
  pass_through: true,
  has_equivalent_check: false,
  ...over,
});

describe('SuggestionLineageNote (#1710)', () => {
  it('renders nothing without advice', () => {
    const { container } = render(<SuggestionLineageNote />);
    expect(container).toBeEmptyDOMElement();
  });

  it('names a visible upstream equivalent and counts hidden ones', () => {
    render(
      <SuggestionLineageNote
        lineage={{
          upstream_status: 'traced',
          complete: true,
          recommendation: 'already_covered_upstream',
          equivalent_upstream_checks: [
            {
              asset_id: 'a1',
              asset_name: 'dataq_retail.silver.feedback',
              column: 'customer_id',
              suite_id: 's',
              suite_name: 'S',
              check_id: 'c',
              check_name: 'C',
            },
          ],
          restricted_equivalent_checks: 2,
        }}
      />,
    );
    expect(
      screen.getByText(/feedback\.customer_id; 2 in suites you can't view/),
    ).toBeInTheDocument();
    expect(screen.getByText(/joins and filters can still break it/)).toBeInTheDocument();
  });

  it('suggests placing at the pass-through origin, and says when lineage is partial', () => {
    render(
      <SuggestionLineageNote
        lineage={{
          upstream_status: 'traced',
          complete: false,
          recommendation: 'place_at_origin',
          origins: [origin()],
        }}
      />,
    );
    expect(
      screen.getByText(/passed through unchanged from feedback\.customer_id/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Lineage is incomplete here/)).toBeInTheDocument();
  });

  it('says a derived column is not covered by an upstream check', () => {
    render(
      <SuggestionLineageNote
        lineage={{
          upstream_status: 'traced',
          complete: true,
          recommendation: null,
          origins: [origin({ column: 'comment', pass_through: false })],
        }}
      />,
    );
    expect(screen.getByText(/Derived from feedback\.comment — not a plain/)).toBeInTheDocument();
  });

  it('states an error rather than omitting it', () => {
    render(<SuggestionLineageNote lineage={{ upstream_status: 'error' }} />);
    expect(screen.getByText(/could not be checked/)).toBeInTheDocument();
  });

  it('says nothing when no lineage was found', () => {
    const { container } = render(
      <SuggestionLineageNote
        lineage={{ upstream_status: 'no_table_lineage', complete: true, origins: [] }}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
