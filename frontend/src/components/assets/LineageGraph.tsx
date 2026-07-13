import { ApartmentOutlined } from '@ant-design/icons';
import { Alert, Card, Empty, Flex, Tag, Typography } from 'antd';
import { useMemo } from 'react';

import type { LineageEdge, LineageNode, LineageSourceHealth } from '../../api/assets';
import { BRAND } from '../../theme';
import { nameSegments } from './assetTree';
import { namespaceLabel } from './namespaceLabel';
import { type CenterAsset, NODE_H, NODE_W, buildLineageLayout } from './lineageLayout';

/**
 * Lineage graph (#805) — one left-to-right DAG replacing the two separate
 * upstream/downstream list boxes: provenance on the left, the asset under view in
 * the middle, blast radius on the right, one column per hop.
 *
 * Nodes are clickable and navigate to that asset. Depth ≥2 comes for free from the
 * existing blast-radius BFS, which now also hands back each node's hop depth and
 * the real edges between them — so a depth-2 node is drawn hanging off the node it
 * actually descends from.
 *
 * Plain inline SVG, no graph library: the layout is a layered DAG we place
 * ourselves (`lineageLayout.ts`), and an SVG in an `overflow-x` container scrolls
 * horizontally inside the card on a phone without ever widening the page — which a
 * pan/zoom canvas makes harder, not easier. It also keeps the dependency count
 * (and the ADR 0031 licence surface) at zero.
 */
export function LineageGraph({
  center,
  upstream,
  downstream,
  edges,
  failingSources = [],
  onOpenAsset,
}: {
  center: CenterAsset;
  upstream: LineageNode[];
  downstream: LineageNode[];
  edges: LineageEdge[];
  /** Lineage-feeding connections whose poll is failing (#828). Non-empty ⇒ what's
   *  below may be stale or missing for reasons unrelated to this asset. */
  failingSources?: LineageSourceHealth[];
  onOpenAsset: (assetId: string) => void;
}) {
  const layout = useMemo(
    () => buildLineageLayout(center, upstream, downstream, edges),
    [center, upstream, downstream, edges],
  );
  const isolated = upstream.length === 0 && downstream.length === 0;

  return (
    <Card
      size="small"
      title={
        <Flex gap={8} align="center">
          <ApartmentOutlined />
          Lineage
        </Flex>
      }
      extra={
        !isolated && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {upstream.length} upstream · {downstream.length} downstream
          </Typography.Text>
        )
      }
    >
      {/* Never show a clean empty state over a broken integration (#828). Prod lineage
          was dark for six days behind an expired credential and this card cheerfully
          said "No lineage recorded" — indistinguishable from an asset that genuinely
          has no upstreams. If a lineage source is failing, say so FIRST, and say it
          whether the graph is empty or not (a partial graph is just as misleading). */}
      {failingSources.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="Lineage may be incomplete — a source is failing"
          description={
            <>
              {failingSources.map((s) => (
                <div key={s.connection_id}>
                  <Typography.Text strong>{s.name}</Typography.Text> ({s.type}) has failed{' '}
                  {s.consecutive_failures}{' '}
                  {s.consecutive_failures === 1 ? 'poll' : 'consecutive polls'}
                  {s.last_error ? `: ${s.last_error}` : '.'}
                </div>
              ))}
              <div style={{ marginTop: 4 }}>
                Until it recovers, lineage here may be stale or missing — this is not necessarily
                the whole picture.
              </div>
            </>
          }
        />
      )}
      {isolated ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            failingSources.length > 0
              ? 'No lineage recorded — but a lineage source is currently failing (above), so this may not be the truth.'
              : 'No lineage recorded for this asset.'
          }
        />
      ) : (
        // The ONLY scroll container: a wide graph scrolls inside the card, so the
        // page itself never overflows horizontally on a phone (#805).
        <div style={{ overflowX: 'auto', overflowY: 'hidden' }}>
          <svg
            width={layout.width}
            height={layout.height}
            role="img"
            aria-label={`Lineage graph: ${upstream.length} upstream and ${downstream.length} downstream assets around ${center.name}`}
            style={{ display: 'block' }}
          >
            <defs>
              <marker
                id="dq-lineage-arrow"
                markerWidth="8"
                markerHeight="8"
                refX="7"
                refY="4"
                orient="auto"
              >
                <path d="M0,0 L8,4 L0,8 z" fill="#c4c8cf" />
              </marker>
            </defs>

            {/* Edges first so the node cards sit on top of the curves. */}
            {layout.edges.map((e) => (
              <path
                key={e.id}
                d={e.path}
                fill="none"
                stroke="#c4c8cf"
                strokeWidth={1.5}
                markerEnd="url(#dq-lineage-arrow)"
              />
            ))}

            {layout.nodes.map((n) => (
              <GraphNode
                key={n.id}
                node={n}
                onOpen={n.isCenter ? undefined : () => onOpenAsset(n.id)}
              />
            ))}
          </svg>
        </div>
      )}

      {/* The monitored/unmonitored distinction the old list boxes carried as tags
          — kept as a legend so the graph's border styling stays readable. */}
      {!isolated && (
        <Flex gap={16} align="center" style={{ marginTop: 8 }} wrap>
          <Flex gap={6} align="center">
            <Tag color="blue" style={{ marginInlineEnd: 0 }}>
              Monitored
            </Tag>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              has a suite targeting it
            </Typography.Text>
          </Flex>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Click any node to open that asset.
          </Typography.Text>
        </Flex>
      )}
    </Card>
  );
}

/** One node card, drawn in SVG. Clickable (and keyboard-operable) unless it's the
 *  centre — you are already looking at that asset. */
function GraphNode({
  node,
  onOpen,
}: {
  node: ReturnType<typeof buildLineageLayout>['nodes'][number];
  onOpen?: () => void;
}) {
  const interactive = onOpen !== undefined;
  return (
    <g
      transform={`translate(${node.x}, ${node.y})`}
      onClick={onOpen}
      onKeyDown={
        interactive
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                onOpen();
              }
            }
          : undefined
      }
      role={interactive ? 'button' : undefined}
      tabIndex={interactive ? 0 : undefined}
      // The centre is labelled too (it just isn't actionable), so a screen reader
      // announces which asset the graph is centred on.
      aria-label={
        interactive
          ? `Open asset ${node.name}${node.isMonitored ? ' (monitored)' : ''}`
          : `${node.name} (this asset)`
      }
      style={{ cursor: interactive ? 'pointer' : 'default' }}
    >
      <title>{`${node.name}\n${node.namespace}`}</title>
      <rect
        width={NODE_W}
        height={NODE_H}
        rx={8}
        fill={node.isCenter ? BRAND.selectedBg : '#ffffff'}
        stroke={node.isCenter ? BRAND.primary : node.isMonitored ? '#91caff' : BRAND.border}
        strokeWidth={node.isCenter ? 2 : 1}
      />
      <text
        x={10}
        y={21}
        fontSize={12}
        fontWeight={600}
        fill={BRAND.ink}
        style={{ pointerEvents: 'none' }}
      >
        {truncate(leafName(node.name), 24)}
      </text>
      <text x={10} y={38} fontSize={10} fill="#8c8c8c" style={{ pointerEvents: 'none' }}>
        {/* The label, not the raw namespace: a node subtitle has ~28 characters, and
            an Iceberg namespace is a DSN — it truncated to `dev · postgresql+psy…`,
            which told the reader nothing. The full namespace stays in the <title>
            tooltip above (#830). */}
        {truncate(
          node.env
            ? `${node.env} · ${namespaceLabel(node.namespace)}`
            : namespaceLabel(node.namespace),
          28,
        )}
      </text>
      {/* Monitored must not be colour-only (WCAG 1.4.1): a filled dot marks it, so
          the state survives a colour-blind viewer and a greyscale print. */}
      {!node.isCenter && node.isMonitored && (
        <circle cx={NODE_W - 12} cy={12} r={3.5} fill={BRAND.primary} />
      )}
    </g>
  );
}

/** The last dotted/slashed segment — the table/file, not the whole path. The full
 *  identity stays in the node's <title> tooltip. Reuses the one segmentation rule
 *  (`assetTree.nameSegments`, #802) so the two views can't drift. */
function leafName(name: string): string {
  return nameSegments(name).at(-1) ?? name;
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}
