import { describe, expect, it } from 'vitest';

import type { LineageEdge, LineageNode } from '../../src/api/assets';
import {
  NODE_H,
  NODE_W,
  type CenterAsset,
  buildLineageLayout,
} from '../../src/components/assets/lineageLayout';

const CENTER: CenterAsset = {
  id: 'c',
  name: 'DB.S.ORDERS',
  namespace: 'snowflake://acct',
  env: 'dev',
};

function node(id: string, depth: number, over: Partial<LineageNode> = {}): LineageNode {
  return {
    id,
    namespace: 'snowflake://acct',
    name: `DB.S.${id.toUpperCase()}`,
    env: 'dev',
    is_monitored: false,
    depth,
    ...over,
  };
}

const nodeById = (layout: ReturnType<typeof buildLineageLayout>, id: string) => {
  const n = layout.nodes.find((x) => x.id === id);
  if (!n) throw new Error(`node ${id} not laid out`);
  return n;
};

describe('buildLineageLayout (#805)', () => {
  it('lays provenance left, the asset centre, and blast radius right', () => {
    const layout = buildLineageLayout(CENTER, [node('up', 1)], [node('down', 1)], []);
    const up = nodeById(layout, 'up');
    const centre = nodeById(layout, 'c');
    const down = nodeById(layout, 'down');

    expect(up.x).toBeLessThan(centre.x);
    expect(centre.x).toBeLessThan(down.x);
    expect(centre.isCenter).toBe(true);
    expect(up.isCenter).toBe(false);
  });

  it('puts each hop in its own column, so depth-2 sits beyond depth-1', () => {
    const layout = buildLineageLayout(
      CENTER,
      [],
      [node('d1', 1), node('d2', 2), node('d3', 3)],
      [],
    );
    const xs = ['c', 'd1', 'd2', 'd3'].map((id) => nodeById(layout, id).x);
    // Strictly increasing: one column per hop, left → right.
    expect(xs[0]).toBeLessThan(xs[1]);
    expect(xs[1]).toBeLessThan(xs[2]);
    expect(xs[2]).toBeLessThan(xs[3]);
    // Columns are a fixed pitch apart.
    expect(xs[2] - xs[1]).toBe(xs[3] - xs[2]);
  });

  it('stacks same-depth siblings in one column (same x, different y)', () => {
    const layout = buildLineageLayout(CENTER, [], [node('a', 1), node('b', 1)], []);
    const a = nodeById(layout, 'a');
    const b = nodeById(layout, 'b');
    expect(a.x).toBe(b.x);
    expect(a.y).not.toBe(b.y);
  });

  it('draws an edge per real backend edge, from source right to target left', () => {
    const edges: LineageEdge[] = [{ source: 'up', target: 'c' }];
    const layout = buildLineageLayout(CENTER, [node('up', 1)], [], edges);
    expect(layout.edges).toHaveLength(1);
    const up = nodeById(layout, 'up');
    const centre = nodeById(layout, 'c');
    // Path starts at the source's RIGHT edge and ends at the target's LEFT edge.
    expect(layout.edges[0].path.startsWith(`M ${up.x + NODE_W} `)).toBe(true);
    expect(layout.edges[0].path.endsWith(`${centre.x} ${centre.y + 26}`)).toBe(true);
  });

  it('drops a dangling edge rather than drawing a line into empty space', () => {
    const edges: LineageEdge[] = [
      { source: 'up', target: 'c' },
      { source: 'ghost', target: 'c' }, // endpoint not in the neighbourhood
    ];
    const layout = buildLineageLayout(CENTER, [node('up', 1)], [], edges);
    expect(layout.edges).toHaveLength(1);
  });

  it('places a cyclic asset ONCE — a cycle must not duplicate a node id', () => {
    // A → B and B → A: the up-walk and the down-walk both return B. Placing it
    // twice would emit duplicate React keys and anchor edges to the wrong copy.
    const b = node('b', 1);
    const layout = buildLineageLayout(
      CENTER,
      [b],
      [b],
      [
        { source: 'b', target: 'c' },
        { source: 'c', target: 'b' },
      ],
    );
    expect(layout.nodes.filter((n) => n.id === 'b')).toHaveLength(1);
    expect(new Set(layout.nodes.map((n) => n.id)).size).toBe(layout.nodes.length);
  });

  it('never duplicates the centre, even on a self-edge', () => {
    const layout = buildLineageLayout(CENTER, [node('c', 1)], [], []);
    expect(layout.nodes.filter((n) => n.id === 'c')).toHaveLength(1);
    expect(layout.nodes[0].isCenter).toBe(true);
  });

  it('an isolated asset lays out just itself, with no edges', () => {
    const layout = buildLineageLayout(CENTER, [], [], []);
    expect(layout.nodes).toHaveLength(1);
    expect(layout.nodes[0].isCenter).toBe(true);
    expect(layout.edges).toEqual([]);
  });

  it('sizes the canvas to fit every column and the tallest stack', () => {
    const layout = buildLineageLayout(CENTER, [node('up', 1)], [node('a', 1), node('b', 1)], []);
    const widest = Math.max(...layout.nodes.map((n) => n.x + NODE_W));
    expect(layout.width).toBeGreaterThanOrEqual(widest);
    const lowest = Math.max(...layout.nodes.map((n) => n.y));
    expect(layout.height).toBeGreaterThan(lowest);
  });
});

// ── mutation-spike gaps (#898) ────────────────────────────────────────────────
//
// Worst file in the Stryker run at 66.3% (26 survivors): the layout's OUTPUT —
// the per-node flags and the fields carried through from the API — was almost
// entirely unasserted. The existing tests pin geometry (columns, x/y, edges) and
// the cycle/dangling defences, so a mutant that corrupts what each node SAYS
// sails through, and every one of those fields drives what the graph renders.

describe('the laid-out node carries the asset it stands for (#898)', () => {
  it('marks the centre as the centre, and nothing else', () => {
    const layout = buildLineageLayout(CENTER, [node('u', 1)], [node('d', 1)], []);
    expect(nodeById(layout, 'c').isCenter).toBe(true);
    expect(nodeById(layout, 'u').isCenter).toBe(false);
    expect(nodeById(layout, 'd').isCenter).toBe(false);
  });

  it('treats the asset under view as monitored', () => {
    // You reached this graph FROM the asset, so it is one we track — the flag
    // drives the centre's rendering, and `isMonitored: false` survived the spike.
    expect(nodeById(buildLineageLayout(CENTER, [], [], []), 'c').isMonitored).toBe(true);
  });

  it('carries each neighbour’s own is_monitored through, not a constant', () => {
    // A constant `true` or `false` here would repaint the whole graph: an
    // unmonitored neighbour is exactly the gap the lineage view exists to reveal.
    const layout = buildLineageLayout(
      CENTER,
      [node('tracked', 1, { is_monitored: true })],
      [node('untracked', 1, { is_monitored: false })],
      [],
    );
    expect(nodeById(layout, 'tracked').isMonitored).toBe(true);
    expect(nodeById(layout, 'untracked').isMonitored).toBe(false);
  });

  it('carries name, namespace and env through unchanged', () => {
    const layout = buildLineageLayout(
      CENTER,
      [
        node('u', 1, {
          name: 'OTHER_DB.S.UPSTREAM',
          namespace: 'unitycatalog://ws',
          env: 'prod',
        }),
      ],
      [],
      [],
    );
    expect(nodeById(layout, 'u')).toMatchObject({
      id: 'u',
      name: 'OTHER_DB.S.UPSTREAM',
      namespace: 'unitycatalog://ws',
      env: 'prod',
    });
    // The centre's own identity too — it is spread from a different source.
    expect(nodeById(layout, 'c')).toMatchObject({
      id: CENTER.id,
      name: CENTER.name,
      namespace: CENTER.namespace,
      env: CENTER.env,
    });
  });

  it('preserves a null env rather than inventing one', () => {
    const layout = buildLineageLayout(
      { ...CENTER, env: null },
      [node('u', 1, { env: null })],
      [],
      [],
    );
    expect(nodeById(layout, 'c').env).toBeNull();
    expect(nodeById(layout, 'u').env).toBeNull();
  });
});

/** The eight numbers of an SVG cubic path: sx sy c1x c1y c2x c2y tx ty. */
function pathNumbers(path: string): number[] {
  const found = path.match(/-?\d+(\.\d+)?/g);
  if (!found) throw new Error(`no numbers in path ${path}`);
  return found.map(Number);
}

describe('the geometry itself (#898)', () => {
  // 24 survivors lived here: column centring, row spacing, the canvas box and the
  // bezier control points were all unasserted, because the existing tests check
  // RELATIVE facts ("depth-2 sits beyond depth-1") that survive almost any
  // arithmetic mutation. Asserted below as invariants rather than by hardcoding
  // the private spacing constants — a layout tweak should not break these, but an
  // inverted sign or a swapped operator must.
  const threeUp = [node('u1', 1), node('u2', 1), node('u3', 1)];

  it('spaces stacked siblings evenly, downward, clear of each other', () => {
    const layout = buildLineageLayout(CENTER, threeUp, [], []);
    const ys = ['u1', 'u2', 'u3'].map((id) => nodeById(layout, id).y);
    const gaps = [ys[1] - ys[0], ys[2] - ys[1]];
    expect(gaps[0]).toBe(gaps[1]); // even
    expect(gaps[0]).toBeGreaterThan(NODE_H); // and non-overlapping, so downward
  });

  it('centres a short column against the tallest one', () => {
    // One node upstream, three downstream: the lone node must sit on the spine,
    // not at the top. `PAD - (…)` and `Math.min(…)` both survived this being
    // unasserted, and both visibly break the graph.
    const layout = buildLineageLayout(
      CENTER,
      [node('u', 1)],
      threeUp.map((n) => ({ ...n })),
      [],
    );
    const mid = (id: string) => nodeById(layout, id).y + NODE_H / 2;
    const tallMid = (mid('u1') + mid('u3')) / 2;
    expect(mid('u')).toBeCloseTo(tallMid, 6);
    expect(mid('c')).toBeCloseTo(tallMid, 6);
  });

  it('sizes the canvas to contain every node it laid out', () => {
    const layout = buildLineageLayout(CENTER, threeUp, [node('d', 1)], []);
    for (const n of layout.nodes) {
      expect(n.x + NODE_W).toBeLessThanOrEqual(layout.width);
      expect(n.y + NODE_H).toBeLessThanOrEqual(layout.height);
      expect(n.x).toBeGreaterThanOrEqual(0);
      expect(n.y).toBeGreaterThanOrEqual(0);
    }
  });

  it('draws each edge from the source’s right edge to the target’s left', () => {
    const layout = buildLineageLayout(
      CENTER,
      [node('u', 1)],
      [],
      [{ source: 'u', target: 'c' } as LineageEdge],
    );
    const from = nodeById(layout, 'u');
    const to = nodeById(layout, 'c');
    const [sx, sy, c1x, , c2x, , tx, ty] = pathNumbers(layout.edges[0].path);

    // Endpoints: right-middle of the source, left-middle of the target.
    expect(sx).toBeCloseTo(from.x + NODE_W, 6);
    expect(sy).toBeCloseTo(from.y + NODE_H / 2, 6);
    expect(tx).toBeCloseTo(to.x, 6);
    expect(ty).toBeCloseTo(to.y + NODE_H / 2, 6);
    // Control points bow OUTWARD from each end — the sign mutants (`sx - dx`,
    // `tx + dx`) invert the curve into a loop and survived unasserted.
    expect(c1x).toBeGreaterThan(sx);
    expect(c2x).toBeLessThan(tx);
  });

  it('keeps a readable curve for a same-column edge, where the span is zero', () => {
    // Two siblings in one column: `tx - sx` is negative, so an unclamped control
    // offset folds the curve back on itself. `Math.min(24, …)` survived here.
    const layout = buildLineageLayout(
      CENTER,
      [node('a', 1), node('b', 1)],
      [],
      [{ source: 'a', target: 'b' } as LineageEdge],
    );
    const [sx, , c1x] = pathNumbers(layout.edges[0].path);
    expect(c1x - sx).toBeGreaterThanOrEqual(24);
  });

  it('gives each edge an id naming both endpoints', () => {
    // `id: ''` survived — duplicate React keys across every edge.
    const layout = buildLineageLayout(
      CENTER,
      [node('u', 1)],
      [],
      [{ source: 'u', target: 'c' } as LineageEdge],
    );
    expect(layout.edges[0].id).toContain('u');
    expect(layout.edges[0].id).toContain('c');
  });
});
