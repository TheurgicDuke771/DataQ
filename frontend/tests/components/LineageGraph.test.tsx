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
  return {
    id,
    name,
    namespace: 'snowflake://acct',
    env: 'dev',
    is_monitored: isMonitored,
    depth,
  };
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
    const graph = screen.getByRole('group', { name: /Lineage graph/ });
    expect(graph.querySelectorAll('path[marker-end]')).toHaveLength(3);
  });

  it('opens the asset a node points at when clicked', async () => {
    const onOpen = renderGraph();
    await userEvent.click(screen.getByLabelText(/Open asset ANALYTICS\.MART\.REVENUE/));
    expect(onOpen).toHaveBeenCalledWith('d1');
  });

  it('reaches a depth-2 node — the blast radius is not truncated to one hop', async () => {
    const onOpen = renderGraph();
    await userEvent.click(screen.getByLabelText('Open asset BI.DASH.SALES (downstream, 2 hops)'));
    expect(onOpen).toHaveBeenCalledWith('d2');
  });

  it('is keyboard operable (Enter on a focused node)', async () => {
    const onOpen = renderGraph();
    const target = screen.getByLabelText('Open asset RAW.ORDERS (upstream, 1 hop)');
    target.focus();
    await userEvent.keyboard('{Enter}');
    expect(onOpen).toHaveBeenCalledWith('u1');
  });

  it('signals "monitored" without relying on colour alone (WCAG 1.4.1)', () => {
    renderGraph();
    // d1 is monitored: it says so in its accessible name…
    expect(
      screen.getByLabelText('Open asset ANALYTICS.MART.REVENUE (downstream, 1 hop, monitored)'),
    ).toBeVisible();
    // …and an unmonitored node does not claim to be.
    expect(screen.getByLabelText('Open asset BI.DASH.SALES (downstream, 2 hops)')).toBeVisible();
    // …plus a non-colour glyph marks it on the node itself.
    const graph = screen.getByRole('group', { name: /Lineage graph/ });
    expect(graph.querySelectorAll('circle')).toHaveLength(1);
  });

  it('exposes every node to a screen reader, saying where it sits (#1672)', () => {
    renderGraph();
    // A named group, not an image: an image's children are hidden from assistive technology.
    expect(screen.queryByRole('img', { name: /Lineage graph/ })).not.toBeInTheDocument();
    expect(
      screen
        .getAllByRole('button', { name: /^Open asset / })
        .map((b) => b.getAttribute('aria-label')),
    ).toEqual([
      'Open asset RAW.ORDERS (upstream, 1 hop)',
      'Open asset ANALYTICS.MART.REVENUE (downstream, 1 hop, monitored)',
      'Open asset BI.DASH.SALES (downstream, 2 hops)',
    ]);
  });

  it('says which asset feeds which, in a list after the graph', () => {
    renderGraph();
    const links = screen.getByRole('list', { name: 'Lineage links' });
    expect([...links.querySelectorAll('li')].map((li) => li.textContent)).toEqual([
      'RAW.ORDERS feeds ANALYTICS.PUBLIC.ORDERS',
      'ANALYTICS.PUBLIC.ORDERS feeds ANALYTICS.MART.REVENUE',
      'ANALYTICS.MART.REVENUE feeds BI.DASH.SALES',
    ]);
  });

  it('describes a node by its namespace rather than repeating its name', () => {
    renderGraph();
    const node = screen.getByRole('button', { name: /RAW\.ORDERS/ });
    const desc = document.getElementById(node.getAttribute('aria-describedby') ?? '');
    expect(desc?.tagName.toLowerCase()).toBe('desc');
    expect(desc?.textContent).not.toContain('RAW.ORDERS');
  });

  it('announces a node on the side it is drawn, when lineage has a cycle', () => {
    // X is both one hop upstream and two hops downstream; the layout draws it upstream.
    render(
      <LineageGraph
        center={CENTER}
        upstream={[node('x', 'LOOP.X', 1)]}
        downstream={[node('x', 'LOOP.X', 2)]}
        edges={[]}
        onOpenAsset={vi.fn()}
      />,
    );
    expect(
      screen.getByRole('button', { name: 'Open asset LOOP.X (upstream, 1 hop)' }),
    ).toBeVisible();
    expect(
      screen.getByRole('group', { name: /Lineage graph: 1 upstream and 0 downstream/ }),
    ).toBeInTheDocument();
  });

  it('labels but does not make the centre asset actionable', () => {
    renderGraph();
    expect(
      screen.getByRole('img', { name: 'ANALYTICS.PUBLIC.ORDERS (this asset)' }),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText('Open asset ANALYTICS.PUBLIC.ORDERS')).not.toBeInTheDocument();
  });

  it('scrolls the graph inside its card rather than overflowing the page (mobile)', () => {
    renderGraph();
    const svg = screen.getByRole('group', { name: /Lineage graph/ });
    const scroller = svg.parentElement as HTMLElement;
    expect(scroller.style.overflowX).toBe('auto');
  });

  it('still draws the asset itself when it has no lineage', () => {
    render(
      <LineageGraph
        center={CENTER}
        upstream={[]}
        downstream={[]}
        edges={[]}
        onOpenAsset={vi.fn()}
      />,
    );
    // The asset's own box stays on screen — an <Empty> icon in its place read as "there is nothing
    // here", when the truth is "here is the asset, with nothing attached to it yet".
    const graph = screen.getByRole('group', { name: /Lineage graph/ });
    expect(graph).toBeInTheDocument();
    expect(graph.querySelectorAll('path[marker-end]')).toHaveLength(0);
    expect(screen.getByLabelText(`${CENTER.name} (this asset)`)).toBeInTheDocument();
    expect(screen.getByText('No lineage recorded for this asset.')).toBeInTheDocument();
  });
});
