import { render, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ScrollableTableFocus } from '../../src/components/shared/ScrollableTableFocus';

// jsdom does no layout, so the overflow is stated rather than produced. What is under test is
// the decision: focusable while overflowing, untouched otherwise, reverted when it stops.
function sized(el: HTMLElement, scrollWidth: number, clientWidth: number): void {
  Object.defineProperty(el, 'scrollWidth', { configurable: true, value: scrollWidth });
  Object.defineProperty(el, 'clientWidth', { configurable: true, value: clientWidth });
}

describe('ScrollableTableFocus', () => {
  it('makes an overflowing table region focusable and leaves a fitting one alone', async () => {
    const wide = document.createElement('div');
    wide.className = 'ant-table-content';
    sized(wide, 1200, 600);
    const fits = document.createElement('div');
    fits.className = 'ant-table-content';
    sized(fits, 600, 600);
    document.body.append(wide, fits);

    render(<ScrollableTableFocus />);

    await waitFor(() => expect(wide.tabIndex).toBe(0));
    expect(wide).toHaveAttribute('role', 'group');
    expect(wide).toHaveAccessibleName('Scrollable table');
    expect(fits).not.toHaveAttribute('tabindex');

    sized(wide, 600, 600);
    window.dispatchEvent(new Event('resize'));
    await waitFor(() => expect(wide).not.toHaveAttribute('tabindex'));
    expect(wide).not.toHaveAttribute('role');

    wide.remove();
    fits.remove();
  });

  it('picks up a table mounted after it', async () => {
    render(<ScrollableTableFocus />);
    const late = document.createElement('div');
    late.className = 'ant-table-body';
    sized(late, 900, 300);
    document.body.append(late);
    await waitFor(() => expect(late.tabIndex).toBe(0));
    late.remove();
  });
});
