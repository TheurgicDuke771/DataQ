import { Empty, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useNavigate } from 'react-router-dom';

import { type AssetSummary, listAssets } from '../api/assets';
import { AssetHealthTag } from '../components/assets/AssetHealthTag';
import { AsyncBody } from '../components/AsyncBody';
import { Page } from '../components/layout/Page';
import { formatTimestamp } from '../components/results/resultsFormat';
import { useAsyncData } from '../hooks/useAsyncData';

/**
 * Assets list (`/assets`, ADR 0034 gap G-d phase 2, #760) — the read-only
 * browse/reason surface over data assets. Each row is an asset the caller can see
 * (derived from suite grants; the backend filters), with the health rolled up
 * across the composing suites the caller can view. Click-through to the detail.
 *
 * This is phase 2 — a sidebar nav addition, **not** the navigation inversion
 * (that is phase 4, deliberately last): suites stay the primary browse surface.
 */
export function Assets() {
  const navigate = useNavigate();
  const { state } = useAsyncData(listAssets);

  return (
    <Page>
      <Typography.Title level={3} style={{ margin: 0 }}>
        Assets
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
        The tables and files your suites monitor. Health is rolled up across every suite you can see
        that targets the asset.
      </Typography.Paragraph>
      <AsyncBody state={state} loadingText="Loading assets…" errorTitle="Failed to load assets">
        {(assets) => <AssetsTable assets={assets} onOpen={(id) => navigate(`/assets/${id}`)} />}
      </AsyncBody>
    </Page>
  );
}

function AssetsTable({ assets, onOpen }: { assets: AssetSummary[]; onOpen: (id: string) => void }) {
  if (assets.length === 0) {
    return (
      <Empty description="No assets yet — give a suite a run target and it will appear here." />
    );
  }
  const columns: ColumnsType<AssetSummary> = [
    {
      title: 'Asset',
      dataIndex: 'name',
      render: (name: string, asset) => (
        <div style={{ minWidth: 0 }}>
          <Typography.Text strong ellipsis style={{ display: 'block' }}>
            {name}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }} ellipsis>
            {asset.namespace}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: 'Env',
      dataIndex: 'env',
      width: 90,
      render: (env: string | null) =>
        env ? <Tag>{env}</Tag> : <Typography.Text type="secondary">—</Typography.Text>,
    },
    {
      title: 'Suites',
      dataIndex: 'suite_count',
      width: 90,
      align: 'center',
    },
    {
      title: 'Health',
      key: 'health',
      width: 130,
      render: (_: unknown, asset) => <AssetHealthTag summary={asset} />,
    },
    {
      title: 'Last seen',
      dataIndex: 'last_seen',
      width: 200,
      render: (ts: string) => (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {formatTimestamp(ts)}
        </Typography.Text>
      ),
    },
  ];
  return (
    <Table<AssetSummary>
      scroll={{ x: 'max-content' }}
      rowKey="id"
      size="middle"
      columns={columns}
      dataSource={assets}
      pagination={false}
      onRow={(asset) => ({
        onClick: () => onOpen(asset.id),
        style: { cursor: 'pointer' },
      })}
    />
  );
}
