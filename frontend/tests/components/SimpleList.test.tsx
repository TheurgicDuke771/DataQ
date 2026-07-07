import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import SimpleList from '../../src/components/SimpleList';

/**
 * #692 — SimpleList.Item rows must wrap on narrow containers: the actions
 * region never shrinks, so without `flex-wrap` (and a min-content floor on the
 * content region) the content collapses to a char-per-line sliver on phone
 * widths. jsdom has no layout, so these assert the load-bearing inline styles.
 */
describe('SimpleList.Item', () => {
  const rows = [{ id: 'r1' }];

  it('wraps actions instead of squeezing the content (#692)', () => {
    render(
      <SimpleList
        dataSource={rows}
        renderItem={() => (
          <SimpleList.Item actions={[<button key="a">Snooze</button>]}>
            <SimpleList.Item.Meta title="order_id not null" description="expect_not_null" />
          </SimpleList.Item>
        )}
      />,
    );
    const listitem = screen.getByText('order_id not null').closest('[role="listitem"]');
    const row = listitem?.firstElementChild as HTMLElement;
    expect(row).toHaveStyle({ flexWrap: 'wrap' });
    // The content region (the row's first flex child) needs a real min-width
    // floor — with bare minWidth:0 it shrinks to a sliver before the actions
    // ever wrap.
    const content = row.firstElementChild as HTMLElement;
    expect(content.style.minWidth).toBe('min(100%, 180px)');
  });

  it('keeps the bare two-child row layout when there are no actions', () => {
    render(
      <SimpleList
        dataSource={rows}
        renderItem={() => (
          <SimpleList.Item>
            <span>label</span>
            <span>status</span>
          </SimpleList.Item>
        )}
      />,
    );
    expect(screen.getByText('label')).toBeInTheDocument();
    expect(screen.getByText('status')).toBeInTheDocument();
  });
});
