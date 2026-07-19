import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { LineageEdge, LineageNode } from '../../src/api/assets';
import { ColumnLineagePanel } from '../../src/components/assets/ColumnLineagePanel';

const CENTER = 'aaaaaaaa-0000-0000-0000-000000000001';
const OPEN = 'aaaaaaaa-0000-0000-0000-000000000002';
const HIDDEN = 'aaaaaaaa-0000-0000-0000-000000000003';

const node = (overrides: Partial<LineageNode> & { id: string }): LineageNode => ({
  namespace: 'unitycatalog://ws',
  name: 'dataq_retail.silver.feedback',
  env: 'dev',
  is_monitored: true,
  depth: 1,
  ...overrides,
});

describe('ColumnLineagePanel (#901)', () => {
  it('renders the column pairs of an accessible direct edge', () => {
    const edges: LineageEdge[] = [
      {
        source: CENTER,
        target: OPEN,
        columns: [
          ['comment', 'sentiment'],
          ['customer_id', 'customer_id'],
        ],
      },
    ];
    render(
      <ColumnLineagePanel
        centerId={CENTER}
        centerName="dataq_retail.gold.feedback_sentiment"
        nodes={[node({ id: OPEN })]}
        edges={edges}
      />,
    );
    expect(screen.getByText('comment → sentiment')).toBeInTheDocument();
    expect(screen.getByText('customer_id → customer_id')).toBeInTheDocument();
    expect(screen.getByText('2 column links')).toBeInTheDocument();
    expect(screen.getByTestId('column-edge')).toBeInTheDocument();
  });

  it('degrades a dangling endpoint to a placeholder label, never crashes', () => {
    const edges: LineageEdge[] = [
      { source: CENTER, target: HIDDEN, columns: [['comment', 'sentiment']] },
    ];
    render(
      <ColumnLineagePanel
        centerId={CENTER}
        centerName="dataq_retail.silver.feedback"
        nodes={[]} // HIDDEN missing from the neighbourhood — defensive path
        edges={edges}
      />,
    );
    expect(screen.getByText(/Unknown asset/)).toBeInTheDocument();
    expect(screen.getByText('comment → sentiment')).toBeInTheDocument();
  });

  it('says so when no direct edge carries column grain (table-level edges omitted)', () => {
    const edges: LineageEdge[] = [
      { source: CENTER, target: OPEN }, // table-grain only
      { source: OPEN, target: HIDDEN, columns: [['a', 'b']] }, // not direct
    ];
    render(
      <ColumnLineagePanel
        centerId={CENTER}
        centerName="dataq_retail.silver.feedback"
        nodes={[node({ id: OPEN }), node({ id: HIDDEN })]}
        edges={edges}
      />,
    );
    expect(screen.getByText(/No column-level lineage recorded/)).toBeInTheDocument();
  });
});
