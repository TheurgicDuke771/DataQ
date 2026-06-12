import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ScalarValue } from '../../src/components/results/ScalarValue';

describe('ScalarValue', () => {
  it('renders an em dash for null or undefined', () => {
    const { rerender } = render(<ScalarValue value={null} />);
    expect(screen.getByText('—')).toBeInTheDocument();
    rerender(<ScalarValue value={undefined} />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('JSON-stringifies an object value in a code box', () => {
    render(<ScalarValue value={{ unexpected_percent: 2 }} />);
    expect(screen.getByText('{"unexpected_percent":2}')).toBeInTheDocument();
  });

  it('renders a falsy scalar as itself, not the em dash', () => {
    render(<ScalarValue value={0} />);
    expect(screen.getByText('0')).toBeInTheDocument();
    expect(screen.queryByText('—')).not.toBeInTheDocument();
  });
});
