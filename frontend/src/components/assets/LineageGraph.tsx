import { ApartmentOutlined } from '@ant-design/icons';
import { Alert, Card, Flex, Tag, Typography } from 'antd';
import { useMemo } from 'react';

import type {
  LineageEdge,
  LineageNode,
  LineageSourceHealth,
  WarehouseLineageStatus,
} from '../../api/assets';
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
  warehouseStatus = [],
  onOpenAsset,
}: {
  center: CenterAsset;
  upstream: LineageNode[];
  downstream: LineageNode[];
  edges: LineageEdge[];
  /** Lineage-feeding connections whose poll is failing (#828). Non-empty ⇒ what's
   *  below may be stale or missing for reasons unrelated to this asset. */
  failingSources?: LineageSourceHealth[];
  /** Warehouse-native lineage sources that are degraded (coarser tier) or failing
   *  (#858). Split at render (#915): a degraded source is working but coarse
   *  (view-level only) — the graph is real, just not the richest possible, so INFO.
   *  A source carrying `last_error` actually FAILED its last refresh, which is an
   *  operational problem and must not read as a mere qualifier — so WARNING. */
  warehouseStatus?: WarehouseLineageStatus[];
  onOpenAsset: (assetId: string) => void;
}) {
  const layout = useMemo(
    () => buildLineageLayout(center, upstream, downstream, edges),
    [center, upstream, downstream, edges],
  );
  const isolated = upstream.length === 0 && downstream.length === 0;
  // #915: one Alert covering both states let "last refresh FAILED" render at the
  // same INFO weight as "answers at a coarser tier". Partition on `last_error`,
  // taking failure as the dominant state — a source that is BOTH coarse and
  // currently failing is a failing source first.
  //
  // The two fields are not mutually exclusive: `warehouse_refresh` sets
  // `degraded_reason` on a successful coarse refresh and does not clear it when a
  // later refresh fails, so a row can carry both. Such a row shows only its error
  // here, exactly as the single-alert version did — the tier note is not lost by
  // this change, it was never shown alongside an error. Surfacing both is a
  // separate improvement, filed rather than smuggled in.
  const warehouseFailing = useMemo(
    () => warehouseStatus.filter((s) => s.last_error),
    [warehouseStatus],
  );
  const warehouseDegraded = useMemo(
    () => warehouseStatus.filter((s) => !s.last_error),
    [warehouseStatus],
  );

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
          title="Lineage may be incomplete — a source is failing"
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
      {/* A warehouse source whose last refresh FAILED. Warning, not info (#915): this
          is an operational failure with the same consequence as a failing poll above —
          lineage is going stale right now — and rendering it at INFO weight next to
          tier qualifiers made a real breakage read as a footnote. */}
      {warehouseFailing.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          title="Warehouse lineage refresh is failing"
          description={
            <>
              {warehouseFailing.map((s) => (
                <div key={s.connection_id}>
                  <Typography.Text strong>{s.name}</Typography.Text> ({s.type}): last refresh failed
                  — {s.last_error}
                  {/* A failing source can ALSO carry a tier note (#987): the success
                      path records `degraded_reason`, and the error path never clears
                      it. That note is not stale trivia — it says which tier the source
                      is able to answer at, a property of its edition/grants that still
                      holds while refreshes fail. Showing only the error hid the reason
                      the graph was already thin before it went stale. */}
                  {s.degraded_reason ? (
                    <div style={{ marginLeft: 12 }}>
                      <Typography.Text type="secondary">
                        Last successful refresh also reported: {s.degraded_reason}
                      </Typography.Text>
                    </div>
                  ) : null}
                </div>
              ))}
              <div style={{ marginTop: 4 }}>
                Lineage from this source stops updating until it recovers, so what is drawn here may
                already be out of date.
              </div>
            </>
          }
        />
      )}
      {/* Warehouse-native lineage that is working but COARSE (a degraded tier — e.g.
          Snowflake view-level-only because the account isn't Enterprise) or stale. Info,
          not warning: the graph is real, just not the richest possible. Never let a
          view-level graph read as a confident complete one (#828, #858).

          Framed as WORKSPACE-level (#916): a warehouse's lineage tier is a property of
          the source, not of this asset (see `asset_view_service.warehouse_lineage_status`),
          so a pure-UC asset page legitimately lists Snowflake connections here. Saying so
          up front stops that reading as a bug. */}
      {warehouseDegraded.length > 0 && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          title="Workspace lineage sources: some report at a coarser tier"
          description={
            <>
              <div style={{ marginBottom: 4 }}>
                These are workspace-wide source qualifiers, not findings about this asset:
              </div>
              {warehouseDegraded.map((s) => (
                <div key={s.connection_id}>
                  <Typography.Text strong>{s.name}</Typography.Text> ({s.type}):{' '}
                  {s.degraded_reason ?? 'lineage is degraded'}
                </div>
              ))}
            </>
          }
        />
      )}
      {/* An asset with no neighbours still renders AS A GRAPH — just its own box, alone.
          The old `<Empty>` placeholder replaced the asset with a grey icon, which reads as
          "there is nothing here" when the truth is "here is the asset, and nothing is
          attached to it yet". The caption below still says so in words; what changes is
          that the asset itself stays on screen, so the panel keeps its shape whether or
          not lineage exists.

          This div is also the ONLY scroll container: a wide graph scrolls inside the card,
          so the page itself never overflows horizontally on a phone (#805). */}
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

      {/* Isolated: the asset's own box is on screen above; say in words why nothing is
          attached to it. The failing-source variant matters — an empty graph and a broken
          lineage pipeline look identical, and that ambiguity is what #828 was about. */}
      {isolated && (
        <Typography.Text type="secondary" style={{ fontSize: 12, marginTop: 8 }}>
          {failingSources.length > 0
            ? 'No lineage recorded — but a lineage source is currently failing (above), so this may not be the truth.'
            : 'No lineage recorded for this asset.'}
        </Typography.Text>
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
