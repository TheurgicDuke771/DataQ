import { Alert, Card, List, Typography } from 'antd';
import { Link } from 'react-router-dom';

import type { InheritedClassification } from '../../api/assets';

/**
 * Columns this asset masks only because lineage traces them to a sensitive upstream column
 * (#2114) — the one masking reason nothing else on the page explains.
 */
export function InheritedMaskingPanel({
  entries,
  truncated,
}: {
  entries?: InheritedClassification[] | null;
  truncated?: boolean;
}) {
  // Absent (a pre-#2114 API) says nothing, so render nothing.
  if (entries === undefined) return null;
  if (entries === null) {
    return (
      <Alert
        type="warning"
        showIcon
        data-testid="inherited-masking-unknown"
        title="Could not check whether any column here is masked through lineage: reading the lineage failed."
      />
    );
  }
  if (entries.length === 0 && !truncated) return null;

  return (
    <Card size="small" title="Masked through lineage" data-testid="inherited-masking-panel">
      {entries.length > 0 && (
        <>
          <Typography.Paragraph type="secondary">
            These columns have no classification of their own, but recorded lineage traces them to a
            sensitive column, so failing samples mask them. If the lineage is wrong, tag the column{' '}
            <Typography.Text code>public</Typography.Text> in the warehouse; its own tag overrides
            what it inherits.
          </Typography.Paragraph>
          <List
            size="small"
            dataSource={entries}
            renderItem={(entry) => (
              <List.Item>
                <Typography.Text code>{entry.column}</Typography.Text>
                <span>
                  inherited from{' '}
                  {entry.sources.map((src, i) => (
                    <span key={`${src.asset_id}:${src.column}`}>
                      {i > 0 && ', '}
                      <Link to={`/assets/${src.asset_id}`}>{src.asset_name}</Link>.{src.column}
                    </span>
                  ))}
                </span>
              </List.Item>
            )}
          />
        </>
      )}
      {truncated && (
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          The lineage walk stopped at its depth limit, so a column further upstream was not
          considered.
        </Typography.Paragraph>
      )}
    </Card>
  );
}
