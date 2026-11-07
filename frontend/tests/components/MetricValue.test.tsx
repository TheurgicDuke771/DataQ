import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { MetricValue, metricMaskedText } from '../../src/components/results/MetricValue';

describe('MetricValue', () => {
  it('renders the number when nothing withheld it', () => {
    render(<MetricValue value={42.5} />);
    expect(screen.getByText('42.5')).toBeInTheDocument();
  });

  it('renders an em dash for a check that records no metric', () => {
    render(<MetricValue value={null} />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('renders a focusable Masked badge, never a dash, when the API withheld the value', async () => {
    render(<MetricValue value={null} redaction="own_tag" />);
    const badge = screen.getByText('Masked');
    expect(screen.queryByText('—')).not.toBeInTheDocument();
    // Keyboard users reach the reason the same way hover users do.
    await userEvent.setup().tab();
    expect(badge).toHaveFocus();
    expect(await screen.findByRole('tooltip')).toHaveTextContent('warehouse tag on this column');
  });

  it('names an unknown reason verbatim rather than dropping it', () => {
    expect(metricMaskedText('some_new_rung')).toContain('some_new_rung');
  });
});
