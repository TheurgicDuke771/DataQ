import { describe, expect, it } from 'vitest';

import type { LineageEdge, LineageNode } from '../../src/api/assets';
import {
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
