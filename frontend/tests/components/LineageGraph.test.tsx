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

function node(
  id: string,
  name: string,
  depth: number,
  isMonitored = false,
  isAccessible = true,
): LineageNode {
  return {
    id,
    name,
    namespace: 'snowflake://acct',
    env: 'dev',
    is_monitored: isMonitored,
    is_accessible: isAccessible,
    depth,
  };
}

/** A neighbour outside the viewer's grants: the backend already stripped its identity
 *  (#845), so the fixture must too — this is exactly what the API sends. */
function redactedNode(id: string, depth: number): LineageNode {
  return {
    id,
    name: null,
    namespace: null,
    env: null,
    is_monitored: false,
    is_accessible: false,
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
    // The asset's own box stays on screen — an <Empty> icon in its place read as
    // "there is nothing here", when the truth is "here is the asset, with nothing
    // attached to it yet". The words still say so.
    const graph = screen.getByRole('img', { name: /Lineage graph/ });
    expect(graph).toBeInTheDocument();
    expect(graph.querySelectorAll('path[marker-end]')).toHaveLength(0);
    expect(screen.getByLabelText(`${CENTER.name} (this asset)`)).toBeInTheDocument();
    expect(screen.getByText('No lineage recorded for this asset.')).toBeInTheDocument();
  });

  // ── neighbours outside the viewer's grants (#845) ──────────────────────────
  //
  // Found in prod: Olivia saw a downstream mart, clicked it, got "asset not found".
  // The graph was offering a door the API refuses to open — and, worse, naming an
  // asset the endpoint 404s no-leak precisely so it can't be named.

  describe('a neighbour outside your grants', () => {
    const REDACTED = [redactedNode('r1', 1)];
    const R_EDGES: LineageEdge[] = [{ source: 'c', target: 'r1' }];

    function renderRedacted(onOpenAsset = vi.fn()) {
      render(
        <LineageGraph
          center={CENTER}
          upstream={[]}
          downstream={REDACTED}
          edges={R_EDGES}
          onOpenAsset={onOpenAsset}
        />,
      );
      return onOpenAsset;
    }

    it('is still drawn — omitting it would claim nothing consumes this table', () => {
      renderRedacted();
      expect(screen.getByRole('img', { name: /Lineage graph/ })).toBeInTheDocument();
      expect(screen.getByText(/Restricted/)).toBeInTheDocument();
      expect(screen.queryByText('No lineage recorded for this asset.')).not.toBeInTheDocument();
    });

    it('is not clickable — no dead link to a 404', async () => {
      const onOpenAsset = renderRedacted();
      const node = screen.getByLabelText('A connected asset outside your access');
      await userEvent.click(node);
      expect(onOpenAsset).not.toHaveBeenCalled();
      expect(node).not.toHaveAttribute('role', 'button');
    });

    it('states the redaction rather than leaving it to be inferred', () => {
      renderRedacted();
      expect(screen.getByText('1 connected asset is outside your access.')).toBeInTheDocument();
    });

    it('renders no identity for the redacted node — not even a stray null', () => {
      renderRedacted();
      // Scoped to the redacted node itself (the CENTRE legitimately shows its own name
      // and namespace — it's the asset you are looking at).
      const node = screen.getByLabelText('A connected asset outside your access');
      expect(node.textContent).toMatch(/Restricted/);
      expect(node.textContent).not.toMatch(/null|undefined|snowflake:\/\//);
    });
  });
});
