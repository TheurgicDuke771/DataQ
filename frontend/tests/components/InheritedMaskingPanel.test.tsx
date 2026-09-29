import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { InheritedMaskingPanel } from '../../src/components/assets/InheritedMaskingPanel';

const renderPanel = (props: Parameters<typeof InheritedMaskingPanel>[0]) =>
  render(
    <MemoryRouter>
      <InheritedMaskingPanel {...props} />
    </MemoryRouter>,
  );

describe('InheritedMaskingPanel (#2114)', () => {
  it('renders nothing for a pre-#2114 API or when nothing is inherited', () => {
    expect(renderPanel({ entries: undefined }).container).toBeEmptyDOMElement();
    expect(renderPanel({ entries: [], truncated: false }).container).toBeEmptyDOMElement();
  });

  it('names each inherited column, its sources, and the fix', () => {
    renderPanel({
      entries: [
        {
          column: 'email',
          sources: [{ asset_id: 'a1', asset_name: 'raw.feedback', column: 'email' }],
        },
      ],
    });
    expect(screen.getByText('Masked through lineage')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'raw.feedback' })).toHaveAttribute(
      'href',
      '/assets/a1',
    );
    expect(screen.getByText('public')).toBeInTheDocument();
  });

  it('says so when the answer is unknown rather than showing nothing', () => {
    renderPanel({ entries: null });
    expect(screen.getByTestId('inherited-masking-unknown')).toHaveTextContent(
      'reading the lineage failed',
    );
  });

  it('reports a capped walk even with no inherited columns', () => {
    renderPanel({ entries: [], truncated: true });
    expect(screen.getByText(/stopped at its depth limit/)).toBeInTheDocument();
  });
});
