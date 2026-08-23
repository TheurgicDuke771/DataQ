import type { LineageEdge, LineageNode } from '../../api/assets';

/**
 * Lineage graph layout (#805) — pure, so the geometry is unit-testable without rendering anything
 * (kept out of the `.tsx`).
 */

export const NODE_W = 190;
export const NODE_H = 52;
const COL_GAP = 72;
const ROW_GAP = 14;
const PAD = 12;

export interface LaidOutNode {
  id: string;
  name: string;
  namespace: string;
  env: string | null;
  isMonitored: boolean;
  /** The asset under view — rendered as the anchor and not clickable. */
  isCenter: boolean;
  x: number;
  y: number;
}

export interface LaidOutEdge {
  id: string;
  /** SVG cubic-bezier `d`, left edge of the source to right edge of the target. */
  path: string;
}

export interface LineageLayout {
  nodes: LaidOutNode[];
  edges: LaidOutEdge[];
  width: number;
  height: number;
}

/** The asset under view, as the graph's centre node. */
export interface CenterAsset {
  id: string;
  name: string;
  namespace: string;
  env: string | null;
}

/** Lay the neighbourhood out into hop columns and bezier edges. */
export function buildLineageLayout(
  center: CenterAsset,
  upstream: LineageNode[],
  downstream: LineageNode[],
  edges: LineageEdge[],
): LineageLayout {
  // Signed-depth column per node: upstream left (negative), downstream right.
  const placed: { node: LaidOutNode; col: number }[] = [
    {
      node: { ...center, isMonitored: true, isCenter: true, x: 0, y: 0 },
      col: 0,
    },
  ];
  // Nothing enforces an acyclic lineage graph (the per-direction BFS is cycle-safe via its own
  // visited set, but the up-walk and down-walk are independent, and a catalog can emit a cycle).
  const seen = new Set<string>([center.id]);
  const place = (n: LineageNode, col: number) => {
    if (seen.has(n.id)) return;
    seen.add(n.id);
    placed.push({
      node: {
        id: n.id,
        name: n.name,
        namespace: n.namespace,
        env: n.env,
        isMonitored: n.is_monitored,
        isCenter: false,
        x: 0,
        y: 0,
      },
      col,
    });
  };
  for (const n of upstream) place(n, -n.depth);
  for (const n of downstream) place(n, n.depth);

  // Group by column, then order the columns left → right by signed depth.
  const byCol = new Map<number, LaidOutNode[]>();
  for (const { node, col } of placed) {
    const bucket = byCol.get(col);
    if (bucket) bucket.push(node);
    else byCol.set(col, [node]);
  }
  const cols = [...byCol.keys()].sort((a, b) => a - b);

  const colHeight = (n: number) => n * NODE_H + Math.max(0, n - 1) * ROW_GAP;
  const tallest = Math.max(...cols.map((c) => colHeight(byCol.get(c)?.length ?? 0)));

  cols.forEach((col, i) => {
    const nodes = byCol.get(col) ?? [];
    // Centre each column vertically against the tallest one, so the graph reads
    // as a spine rather than a ragged top-aligned stack.
    const top = PAD + (tallest - colHeight(nodes.length)) / 2;
    nodes.forEach((node, j) => {
      node.x = PAD + i * (NODE_W + COL_GAP);
      node.y = top + j * (NODE_H + ROW_GAP);
    });
  });

  const nodes = placed.map((p) => p.node);
  const byId = new Map(nodes.map((n) => [n.id, n]));

  const laidOutEdges: LaidOutEdge[] = [];
  for (const e of edges) {
    const from = byId.get(e.source);
    const to = byId.get(e.target);
    if (!from || !to) continue; // dangling — never draw a line into empty space
    laidOutEdges.push({ id: `${e.source}->${e.target}`, path: bezier(from, to) });
  }

  return {
    nodes,
    edges: laidOutEdges,
    width: PAD * 2 + cols.length * NODE_W + Math.max(0, cols.length - 1) * COL_GAP,
    height: PAD * 2 + tallest,
  };
}

/** A cubic bezier from the source node's right edge to the target's left edge. */
function bezier(from: LaidOutNode, to: LaidOutNode): string {
  const sx = from.x + NODE_W;
  const sy = from.y + NODE_H / 2;
  const tx = to.x;
  const ty = to.y + NODE_H / 2;
  // Clamped control offset: keeps the curve readable even for a same-column or
  // backwards edge, where (tx - sx) would otherwise be zero or negative.
  const dx = Math.max(24, (tx - sx) / 2);
  return `M ${sx} ${sy} C ${sx + dx} ${sy}, ${tx - dx} ${ty}, ${tx} ${ty}`;
}
