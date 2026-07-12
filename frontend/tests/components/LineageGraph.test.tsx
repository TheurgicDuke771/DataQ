import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { LineageEdge, LineageNode } from '../../src/api/assets';
import { LineageGraph } from '../../src/components/assets/LineageGraph';

const CENTER = {
  id: 'c',
  name: 'ANALYTICS.PUBLIC.ORDERS',
  namespace: 'snowflake://acct',
  env: 'dev',
};

function node(id: string, name: string, depth: number, isMonitored = false): LineageNode {
  return { id, name, namespace: 'snowflake://acct', env: 'dev', is_monitored: isMonitored, depth };
}

const UP = [node('u1', 'RAW.ORDERS', 1)];
const DOWN = [node('d1', 'ANALYTICS.MART.REVENUE', 1, true), node('d2', 'BI.DASH.SALES', 2)];
const EDGES: LineageEdge[] = [
  { source: 'u1', target: 'c' },
  { source: 'c', target: 'd1' },
  { source: 'd1', target: 'd2' },
];

function renderGraph(onOpenAsset = vi.fn()) {
  render(
    <LineageGraph
      center={CENTER}
      upstream={UP}
      downstream={DOWN}
      edges={EDGES}
      onOpenAsset={onOpenAsset}
    />,
  );
  return onOpenAsset;
}

describe('LineageGraph (#805)', () => {
  it('draws one edge per real backend edge, with direction arrows', () => {
    renderGraph();
    const graph = screen.getByRole('img', { name: /Lineage graph/ });
    expect(graph.querySelectorAll('path[marker-end]')).toHaveLength(3);
  });

  it('opens the asset a node points at when clicked', async () => {
    const onOpen = renderGraph();
    await userEvent.click(screen.getByLabelText(/Open asset ANALYTICS\.MART\.REVENUE/));
    expect(onOpen).toHaveBeenCalledWith('d1');
  });

  it('reaches a depth-2 node — the blast radius is not truncated to one hop', async () => {
    const onOpen = renderGraph();
    await userEvent.click(screen.getByLabelText('Open asset BI.DASH.SALES'));
    expect(onOpen).toHaveBeenCalledWith('d2');
  });

  it('is keyboard operable (Enter on a focused node)', async () => {
    const onOpen = renderGraph();
    const target = screen.getByLabelText('Open asset RAW.ORDERS');
    target.focus();
    await userEvent.keyboard('{Enter}');
    expect(onOpen).toHaveBeenCalledWith('u1');
  });

  it('signals "monitored" without relying on colour alone (WCAG 1.4.1)', () => {
    renderGraph();
    // d1 is monitored: it says so in its accessible name…
    expect(screen.getByLabelText('Open asset ANALYTICS.MART.REVENUE (monitored)')).toBeVisible();
    // …and an unmonitored node does not claim to be.
    expect(screen.getByLabelText('Open asset BI.DASH.SALES')).toBeVisible();
    // …plus a non-colour glyph marks it on the node itself.
    const graph = screen.getByRole('img', { name: /Lineage graph/ });
    expect(graph.querySelectorAll('circle')).toHaveLength(1);
  });

  it('labels but does not make the centre asset actionable', () => {
    renderGraph();
    expect(screen.getByLabelText('ANALYTICS.PUBLIC.ORDERS (this asset)')).toBeInTheDocument();
    expect(screen.queryByLabelText('Open asset ANALYTICS.PUBLIC.ORDERS')).not.toBeInTheDocument();
  });

  it('scrolls the graph inside its card rather than overflowing the page (mobile)', () => {
    renderGraph();
    const svg = screen.getByRole('img', { name: /Lineage graph/ });
    const scroller = svg.parentElement as HTMLElement;
    expect(scroller.style.overflowX).toBe('auto');
  });

  it('keeps a graceful empty state for a 0-edge asset', () => {
    render(
      <LineageGraph
        center={CENTER}
        upstream={[]}
        downstream={[]}
        edges={[]}
        onOpenAsset={vi.fn()}
      />,
    );
    expect(screen.getByText('No lineage recorded for this asset.')).toBeInTheDocument();
    expect(screen.queryByRole('img', { name: /Lineage graph/ })).not.toBeInTheDocument();
  });
});
