import { Alert, AutoComplete, Button, Card, Empty, Flex, Segmented, Tag, Typography } from 'antd';
import { useState } from 'react';

import {
  type ColumnCoverage,
  type ColumnTrace,
  type LineageEdge,
  type LineageNode,
  traceColumn,
  type TraceDirection,
  type TraceStatus,
} from '../../api/assets';
import { errorMessage } from '../../utils/errors';
import { nameSegments } from './assetTree';

/** Why an edge has no column pairs (#1710) — never rendered as "no column dependency". */
const COVERAGE_TEXT: Record<Exclude<ColumnCoverage, 'recorded'>, string> = {
  none_recorded:
    'The source reads column lineage but recorded none for this edge (e.g. a view, or a write outside its window) — not proof the columns are unrelated.',
  unavailable: "The source's column-lineage read failed on its last refresh.",
  unknown: 'Column coverage unknown — the source has not refreshed since this was tracked.',
  not_captured: 'This lineage source (dbt manifest / catalog) never carries column detail.',
};

const COVERAGE_LABEL: Record<ColumnCoverage, string> = {
  recorded: 'recorded',
  none_recorded: 'none recorded',
  unavailable: 'unavailable',
  unknown: 'unknown',
  not_captured: 'not captured',
};

const STATUS_TEXT: Record<TraceStatus, string> = {
  traced: 'traced',
  no_table_lineage: 'no lineage edges this way',
  none_recorded: 'every adjacent edge has column detail; none maps this column',
  incomplete: 'nothing traced, and some adjacent edges lack column detail',
};

/**
 * Column-level lineage for the asset under view (#901): the direct edges with their
 * `upstream column → downstream column` mappings — and, for an edge without any, WHY (#1710) —
 * plus a single-column trace across every hop.
 */
export function ColumnLineagePanel({
  centerId,
  centerName,
  nodes,
  edges,
}: {
  centerId: string;
  centerName: string;
  nodes: LineageNode[];
  edges: LineageEdge[];
}) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const label = (id: string): string => {
    if (id === centerId) return tableName(centerName);
    const node = byId.get(id);
    // Defensive: every edge endpoint should be in the neighbourhood; a dangling
    // id must degrade to a placeholder, never crash the panel.
    return node ? tableName(node.name) : 'Unknown asset';
  };
  const direct = edges.filter((e) => e.source === centerId || e.target === centerId);
  const withColumns = direct.flatMap((e) =>
    e.columns != null && e.columns.length > 0 ? [{ ...e, columns: e.columns }] : [],
  );
  const without = direct.filter((e) => e.columns == null || e.columns.length === 0);
  // The center's own column names as seen on its direct edges — trace suggestions only.
  const centerColumns = [
    ...new Set(
      withColumns.flatMap((e) =>
        e.columns.map(([up, down]) => (e.source === centerId ? up : down)),
      ),
    ),
  ].sort();
  return (
    <Card size="small" title="Column lineage">
      <Flex vertical gap={16}>
        {direct.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="No direct lineage edges recorded for this asset"
          />
        ) : (
          <Flex vertical gap={12}>
            {withColumns.length === 0 && (
              <Typography.Text type="secondary">
                No column-level lineage recorded on this asset&apos;s direct edges — see each edge
                for why.
              </Typography.Text>
            )}
            {withColumns.map((edge) => (
              <div key={`${edge.source}->${edge.target}`} data-testid="column-edge">
                <Typography.Text strong>
                  {label(edge.source)} → {label(edge.target)}
                </Typography.Text>{' '}
                <Tag>{edge.columns.length} column links</Tag>
                <Flex vertical gap={2} style={{ marginTop: 4 }}>
                  {edge.columns.map(([up, down]) => (
                    <Typography.Text key={`${up}->${down}`} code>
                      {up} → {down}
                    </Typography.Text>
                  ))}
                </Flex>
              </div>
            ))}
            {without.map((edge) => {
              const coverage = coverageOf(edge);
              return (
                <div key={`${edge.source}->${edge.target}`} data-testid="column-edge-uncovered">
                  <Typography.Text strong>
                    {label(edge.source)} → {label(edge.target)}
                  </Typography.Text>{' '}
                  <Tag>{COVERAGE_LABEL[coverage]}</Tag>
                  <div>
                    <Typography.Text type="secondary">{COVERAGE_TEXT[coverage]}</Typography.Text>
                  </div>
                </div>
              );
            })}
          </Flex>
        )}
        <ColumnTraceForm assetId={centerId} suggestions={centerColumns} />
      </Flex>
    </Card>
  );
}

function coverageOf(edge: LineageEdge): Exclude<ColumnCoverage, 'recorded'> {
  const c = edge.column_coverage;
  // An edge without pairs cannot be `recorded`; an older API omits the field → unknown.
  return c === undefined || c === 'recorded' ? 'unknown' : c;
}

function ColumnTraceForm({ assetId, suggestions }: { assetId: string; suggestions: string[] }) {
  const [column, setColumn] = useState('');
  const [direction, setDirection] = useState<TraceDirection>('both');
  const [state, setState] = useState<
    | { status: 'idle' }
    | { status: 'running' }
    | { status: 'ok'; trace: ColumnTrace }
    | { status: 'error'; error: string }
  >({ status: 'idle' });

  const run = async () => {
    const value = column.trim();
    if (!value) return;
    setState({ status: 'running' });
    try {
      setState({ status: 'ok', trace: await traceColumn(assetId, value, direction) });
    } catch (err) {
      setState({ status: 'error', error: errorMessage(err) });
    }
  };

  return (
    <Flex vertical gap={8}>
      <Typography.Text strong>Trace a column</Typography.Text>
      <Flex gap={8} wrap>
        <AutoComplete
          aria-label="Column to trace"
          placeholder="Column name"
          style={{ minWidth: 200, flex: 1 }}
          value={column}
          options={suggestions
            .filter((c) => c.toLowerCase().includes(column.trim().toLowerCase()))
            .map((value) => ({ value }))}
          onChange={setColumn}
        />
        <Segmented<TraceDirection>
          aria-label="Trace direction"
          value={direction}
          onChange={setDirection}
          options={[
            { label: 'Both', value: 'both' },
            { label: 'Upstream', value: 'upstream' },
            { label: 'Downstream', value: 'downstream' },
          ]}
        />
        <Button
          onClick={() => void run()}
          disabled={!column.trim()}
          loading={state.status === 'running'}
        >
          Trace
        </Button>
      </Flex>
      {state.status === 'error' && <Alert type="error" showIcon title={state.error} />}
      {state.status === 'ok' && <TraceResult trace={state.trace} />}
    </Flex>
  );
}

function TraceResult({ trace }: { trace: ColumnTrace }) {
  const names = new Map(trace.assets.map((a) => [a.id, tableName(a.name)]));
  const name = (id: string) => names.get(id) ?? 'Unknown asset';
  const cell = (id: string, col: string) => `${name(id)}.${col}`;
  return (
    <Flex vertical gap={8} data-testid="column-trace">
      <Typography.Text type="secondary">
        Trace of <Typography.Text code>{trace.column}</Typography.Text>
      </Typography.Text>
      {!trace.complete && (
        <Alert
          type="warning"
          showIcon
          title="Partial trace"
          description={[
            trace.truncated
              ? 'The walk hit its depth/size cap; more columns may lie beyond it.'
              : '',
            trace.gaps.length > 0
              ? 'Some lineage edges on the way carry no column detail (listed below) — this column may cross them.'
              : '',
            'Absence here is not evidence of no dependency.',
          ]
            .filter(Boolean)
            .join(' ')}
        />
      )}
      {trace.qualified_by.length > 0 && (
        <Alert
          type="info"
          showIcon
          title="Lineage qualified"
          description={trace.qualified_by.join('; ')}
        />
      )}
      {trace.upstream_status !== null && (
        <div>
          <Typography.Text strong>Origins</Typography.Text>{' '}
          <Typography.Text type="secondary">({STATUS_TEXT[trace.upstream_status]})</Typography.Text>
          <Flex vertical gap={2}>
            {trace.origins.map((o) => (
              <span key={`${o.asset_id}.${o.column}`}>
                <Typography.Text code>{cell(o.asset_id, o.column)}</Typography.Text>{' '}
                {!o.confirmed && <Tag color="warning">may have further upstream</Tag>}
              </span>
            ))}
          </Flex>
        </div>
      )}
      {trace.downstream_status !== null && (
        <div>
          <Typography.Text strong>Downstream columns</Typography.Text>{' '}
          <Typography.Text type="secondary">
            ({STATUS_TEXT[trace.downstream_status]})
          </Typography.Text>
          <Flex vertical gap={2}>
            {trace.downstream.map((n) => (
              <Typography.Text key={`${n.asset_id}.${n.column}`} code>
                {cell(n.asset_id, n.column)} (hop {n.depth})
              </Typography.Text>
            ))}
          </Flex>
        </div>
      )}
      {trace.gaps.length > 0 && (
        <div>
          <Typography.Text strong>Edges without column detail</Typography.Text>
          <Flex vertical gap={2}>
            {trace.gaps.map((g) => (
              <Typography.Text
                key={`${g.upstream_asset_id}->${g.downstream_asset_id}`}
                type="secondary"
              >
                {name(g.upstream_asset_id)} → {name(g.downstream_asset_id)}:{' '}
                {COVERAGE_LABEL[g.coverage]}
              </Typography.Text>
            ))}
          </Flex>
        </div>
      )}
    </Flex>
  );
}

/** The table's own segment of a dotted identity — the panel's rows are about
 *  columns, so the full `db.schema.table` label would drown them. */
function tableName(name: string): string {
  const segments = nameSegments(name);
  return segments[segments.length - 1] ?? name;
}
