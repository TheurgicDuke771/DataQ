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
  is_accessible: true,
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
        column_count: 2,
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

  it('renders a redacted edge as a locked count-only box — no names, never an empty list', () => {
    // The server's #845 one-rule: far endpoint outside the viewer's grants ⇒
    // columns null, count only, node identity null.
    const edges: LineageEdge[] = [
      { source: CENTER, target: HIDDEN, columns: null, column_count: 3 },
    ];
    render(
      <ColumnLineagePanel
        centerId={CENTER}
        centerName="dataq_retail.silver.feedback"
        nodes={[node({ id: HIDDEN, name: null, namespace: null, env: null, is_accessible: false })]}
        edges={edges}
      />,
    );
    expect(screen.getByTestId('column-edge-redacted')).toBeInTheDocument();
    expect(screen.getByText('3 column links')).toBeInTheDocument();
    expect(screen.getByText(/Restricted asset/)).toBeInTheDocument();
    expect(screen.getByText(/hidden/)).toBeInTheDocument();
    // No pair rows exist to leak.
    expect(screen.queryByTestId('column-edge')).not.toBeInTheDocument();
  });

  it('says so when no direct edge carries column grain (table-level edges omitted)', () => {
    const edges: LineageEdge[] = [
      { source: CENTER, target: OPEN }, // table-grain only
      { source: OPEN, target: HIDDEN, columns: [['a', 'b']], column_count: 1 }, // not direct
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
