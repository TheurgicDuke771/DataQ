import { LockOutlined } from '@ant-design/icons';
import { Card, Empty, Flex, Tag, Typography } from 'antd';

import type { LineageEdge, LineageNode } from '../../api/assets';
import { nameSegments } from './assetTree';

/**
 * Column-level lineage for the asset under view (#901) — the direct edges that carry
 * a column-grain refinement, as `upstream column → downstream column` mappings.
 *
 * Redaction contract (the #845 one-rule, applied SERVER-side): an edge whose far
 * endpoint is outside the viewer's grants arrives with `columns: null` and only
 * `column_count` — rendered here as a locked box ("N column links · restricted
 * asset"), because a hidden asset's column names are schema disclosure. The panel
 * must never render that as "no column lineage": something maps, in N links —
 * the viewer just may not see what.
 *
 * Edges with neither field are table-grain only and are omitted; if no direct edge
 * carries the grain, the panel says so rather than rendering an empty card.
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
    if (!node || !node.name) return 'Restricted asset';
    return tableName(node.name);
  };
  const direct = edges.filter(
    (e) =>
      (e.source === centerId || e.target === centerId) &&
      (e.columns != null || e.column_count != null),
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
            const redacted = edge.columns == null;
            return (
              <div key={key} data-testid={redacted ? 'column-edge-redacted' : 'column-edge'}>
                <Typography.Text strong>
                  {label(edge.source)} → {label(edge.target)}
                </Typography.Text>{' '}
                <Tag>{edge.column_count ?? edge.columns?.length ?? 0} column links</Tag>
                {redacted ? (
                  // The server withheld the pairs (far endpoint outside the viewer's
                  // grants) — an honest locked box, never an empty list.
                  <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
                    <LockOutlined /> Column mappings are hidden — they involve an asset you don't
                    have access to.
                  </Typography.Paragraph>
                ) : (
                  <Flex vertical gap={2} style={{ marginTop: 4 }}>
                    {(edge.columns ?? []).map(([up, down]) => (
                      <Typography.Text key={`${up}->${down}`} code>
                        {up} → {down}
                      </Typography.Text>
                    ))}
                  </Flex>
                )}
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
