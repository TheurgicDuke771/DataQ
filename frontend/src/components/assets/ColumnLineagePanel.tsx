import { Card, Empty, Flex, Tag, Typography } from 'antd';

import type { LineageEdge, LineageNode } from '../../api/assets';
import { nameSegments } from './assetTree';

/**
 * Column-level lineage for the asset under view (#901) — the direct edges that carry
 * a column-grain refinement, as `upstream column → downstream column` mappings.
 * Shown in full to every member (ADR 0037 — column names are schema metadata, i.e.
 * identity, not measurement).
 *
 * Edges without the column grain are table-level only and are omitted; if no direct
 * edge carries the grain, the panel says so rather than rendering an empty card.
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
  // flatMap so `columns` is narrowed to non-null in scope — no dead `?? []`
  // fallbacks downstream encoding a state the filter already excluded.
  const direct = edges.flatMap((e) =>
    (e.source === centerId || e.target === centerId) && e.columns != null
      ? [{ source: e.source, target: e.target, columns: e.columns }]
      : [],
  );
  return (
    <Card size="small" title="Column lineage">
      {direct.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="No column-level lineage recorded on this asset's direct edges"
        />
      ) : (
        <Flex vertical gap={12}>
          {direct.map((edge) => {
            const key = `${edge.source}->${edge.target}`;
            return (
              <div key={key} data-testid="column-edge">
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
            );
          })}
        </Flex>
      )}
    </Card>
  );
}

/** The table's own segment of a dotted identity — the panel's rows are about
 *  columns, so the full `db.schema.table` label would drown them. */
function tableName(name: string): string {
  const segments = nameSegments(name);
  return segments[segments.length - 1] ?? name;
}
